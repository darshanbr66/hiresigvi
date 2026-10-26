"""Recruiter-facing Application CRUD + status workflow. `organization_id` is
always taken from the authenticated caller, never from the request
(docs/security.md § 2). Status changes go through
app/workflows/application_workflow.py exclusively — see
docs/recruitment-workflow.md."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.integrations.storage import StorageError, get_resume_storage_for_provider
from app.models.application import Application, ApplicationSource, ApplicationStatus
from app.models.candidate import CandidateType
from app.models.user import User
from app.schemas.application import (
    AIScreeningOverrideRequest,
    ApplicationCreateRequest,
    ApplicationDeleteRequest,
    ApplicationResponse,
    ApplicationSortField,
    ApplicationStatusChangeRequest,
    SortDirection,
)
from app.schemas.assessment import AssessmentInvitationResponse, AssessmentResultResponse
from app.schemas.email import (
    EmailComposeRequest,
    EmailComposeResponse,
    EmailDraftRequest,
    EmailPreviewResponse,
    EmailSendResponse,
)
from app.schemas.public_assessment import MonitoringEventResponse
from app.services import (
    application_service,
    assessment_service,
    email_composer,
)

router = APIRouter(prefix="/applications", tags=["recruiter-applications"])


def to_response(application: Application) -> ApplicationResponse:
    return ApplicationResponse(
        id=application.id,
        organization_id=application.organization_id,
        candidate_id=application.candidate_id,
        candidate_full_name=application.candidate.full_name,
        candidate_email=application.candidate.email,
        candidate_phone=application.candidate.phone,
        job_id=application.job_id,
        job_title=application.job.title,
        job_employment_type=application.job.employment_type,
        campus_drive_id=application.campus_drive_id,
        status=application.status,
        source=application.source,
        is_self_service=application.is_self_service,
        applied_at=application.applied_at,
        created_at=application.created_at,
        updated_at=application.updated_at,
        resume_id=application.resume.id if application.resume else None,
        resume_filename=application.resume.original_filename if application.resume else None,
        deleted_at=application.deleted_at,
    )


@router.post("", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
async def create_application(
    payload: ApplicationCreateRequest,
    current_user: User = Depends(require_permission("application.create")),
    db: AsyncSession = Depends(get_db),
) -> ApplicationResponse:
    assert current_user.organization_id is not None
    application = await application_service.create_application(
        db,
        organization_id=current_user.organization_id,
        candidate_id=payload.candidate_id,
        job_id=payload.job_id,
        source=payload.source,
        actor_user_id=current_user.id,
    )
    return to_response(application)


# Upper bound on one page. The Applications page asks for 25; anything the
# caller sends beyond this is rejected rather than silently clamped.
MAX_PAGE_SIZE = 100

# Hard ceilings for user-typed numbers — far beyond any real value, they only
# exist to reject nonsense (and keep the integers well inside the column type).
_MAX_YEARS = 80
_MAX_NOTICE_DAYS = 365
_TEXT = 255


@router.get("", response_model=list[ApplicationResponse])
async def list_applications(
    response: Response,
    job_id: uuid.UUID | None = Query(default=None),
    candidate_id: uuid.UUID | None = Query(default=None),
    application_status: ApplicationStatus | None = Query(default=None, alias="status"),
    campus_drive_id: uuid.UUID | None = Query(default=None),
    institution_id: uuid.UUID | None = Query(default=None),
    program_id: uuid.UUID | None = Query(default=None),
    source: ApplicationSource | None = Query(default=None),
    q: str | None = Query(
        default=None,
        max_length=100,
        description="Words matched against candidate name, email, phone and job title.",
    ),
    candidate_type: CandidateType | None = Query(default=None),
    current_title: str | None = Query(default=None, max_length=_TEXT),
    current_company: str | None = Query(default=None, max_length=_TEXT),
    location: str | None = Query(default=None, max_length=_TEXT),
    preferred_location: str | None = Query(default=None, max_length=_TEXT),
    qualification: str | None = Query(default=None, max_length=_TEXT),
    min_experience: int | None = Query(default=None, ge=0, le=_MAX_YEARS),
    max_experience: int | None = Query(default=None, ge=0, le=_MAX_YEARS),
    max_notice_period_days: int | None = Query(default=None, ge=0, le=_MAX_NOTICE_DAYS),
    immediate_joiner: bool | None = Query(default=None),
    applied_from: date | None = Query(default=None),
    applied_to: date | None = Query(default=None),
    sort_by: ApplicationSortField = Query(default=ApplicationSortField.CREATED_AT),
    sort_dir: SortDirection = Query(default=SortDirection.DESC),
    limit: int | None = Query(default=None, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    current_user: User = Depends(require_permission("application.read")),
    db: AsyncSession = Depends(get_db),
) -> list[ApplicationResponse]:
    """The organization's applications, filtered, sorted and paged in the
    database. All filters combine with AND and paging is applied after
    filtering. The body stays a plain list (existing callers rely on it); the
    number of applications matching the filters — ignoring `limit`/`offset` —
    is returned in the `X-Total-Count` header. Without `limit`, every match is
    returned. Inverted ranges are a 422."""
    assert current_user.organization_id is not None
    filters = application_service.ApplicationFilters(
        job_id=job_id,
        candidate_id=candidate_id,
        status=application_status,
        campus_drive_id=campus_drive_id,
        institution_id=institution_id,
        program_id=program_id,
        source=source,
        search=q,
        candidate_type=candidate_type,
        current_title=current_title,
        current_company=current_company,
        location=location,
        preferred_location=preferred_location,
        qualification=qualification,
        min_experience=min_experience,
        max_experience=max_experience,
        max_notice_period_days=max_notice_period_days,
        immediate_joiner=immediate_joiner,
        applied_from=applied_from,
        applied_to=applied_to,
    )
    applications = await application_service.list_applications(
        db,
        current_user.organization_id,
        filters=filters,
        sort_by=sort_by,
        sort_direction=sort_dir,
        limit=limit,
        offset=offset,
    )
    # An unpaged request already holds every match; only a page needs a count.
    total = (
        len(applications)
        if limit is None
        else await application_service.count_applications(db, current_user.organization_id, filters)
    )
    response.headers["X-Total-Count"] = str(total)
    return [to_response(application) for application in applications]


@router.get("/{application_id}", response_model=ApplicationResponse)
async def get_application(
    application_id: uuid.UUID,
    _: User = Depends(require_permission("application.read")),
    db: AsyncSession = Depends(get_db),
) -> ApplicationResponse:
    application = await application_service.get_application(db, application_id)
    if application is None:
        raise NotFoundError("Application not found.")
    return to_response(application)


@router.post("/{application_id}/delete", response_model=ApplicationResponse)
async def delete_application(
    application_id: uuid.UUID,
    payload: ApplicationDeleteRequest,
    current_user: User = Depends(require_permission("application.delete")),
    db: AsyncSession = Depends(get_db),
) -> ApplicationResponse:
    application = await application_service.get_application(db, application_id)
    if application is None:
        raise NotFoundError("Application not found.")
    deleted = await application_service.delete_application(
        db, application, actor=current_user, reason=payload.reason
    )
    return to_response(deleted)


@router.get("/{application_id}/resume")
async def download_resume(
    application_id: uuid.UUID,
    _: User = Depends(require_permission("application.read")),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    application = await application_service.get_application(db, application_id)
    if application is None or application.resume is None:
        raise NotFoundError("No resume found for this application.")

    resume = application.resume
    try:
        storage = get_resume_storage_for_provider(resume.storage_provider)
        chunks = await storage.open_stream(resume.storage_path)
    except StorageError as exc:
        raise NotFoundError("The resume file could not be found in storage.") from exc

    safe_name = resume.original_filename.replace('"', "")
    return StreamingResponse(
        chunks,
        media_type=resume.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Content-Length": str(resume.size_bytes),
        },
    )


def _invitation_to_response(
    invitation, candidate_full_name: str
) -> AssessmentInvitationResponse:
    return AssessmentInvitationResponse(
        id=invitation.id,
        assessment_id=invitation.assessment_id,
        assessment_title=invitation.assessment.title,
        application_id=invitation.application_id,
        candidate_full_name=candidate_full_name,
        status=invitation.status,
        expires_at=invitation.expires_at,
        started_at=invitation.started_at,
        submitted_at=invitation.submitted_at,
        emailed_at=invitation.emailed_at,
        attempt_number=invitation.attempt_number,
        retest_reason=invitation.retest_reason,
        result=AssessmentResultResponse.model_validate(invitation.result) if invitation.result else None,
    )


@router.get("/{application_id}/assessment", response_model=AssessmentInvitationResponse | None)
async def get_application_assessment(
    application_id: uuid.UUID,
    _: User = Depends(require_permission("assessment.read")),
    db: AsyncSession = Depends(get_db),
) -> AssessmentInvitationResponse | None:
    invitation = await assessment_service.get_invitation_for_application(db, application_id)
    if invitation is None:
        return None
    application = await application_service.get_application(db, application_id)
    return _invitation_to_response(invitation, application.candidate.full_name if application else "")


@router.get("/{application_id}/assessment/attempts", response_model=list[AssessmentInvitationResponse])
async def list_application_assessment_attempts(
    application_id: uuid.UUID,
    _: User = Depends(require_permission("assessment.read")),
    db: AsyncSession = Depends(get_db),
) -> list[AssessmentInvitationResponse]:
    """Full retest history for this application, oldest attempt first —
    the original attempt is never overwritten or removed (CLAUDE.md § 3)."""
    application = await application_service.get_application(db, application_id)
    attempts = await assessment_service.list_attempts_for_application(db, application_id)
    candidate_name = application.candidate.full_name if application else ""
    return [_invitation_to_response(a, candidate_name) for a in attempts]


@router.get(
    "/{application_id}/assessment/events",
    response_model=list[MonitoringEventResponse],
)
async def list_application_assessment_events(
    application_id: uuid.UUID,
    _: User = Depends(require_permission("assessment.read")),
    db: AsyncSession = Depends(get_db),
) -> list[MonitoringEventResponse]:
    """Observed browser-monitoring events for this application's current
    (latest) attempt — "Assessment Activity" on the application detail
    page (SIGVITAS platform overhaul § 7). Permission-gated exactly like
    the assessment/result data above; never exposed to the candidate."""
    invitation = await assessment_service.get_invitation_for_application(db, application_id)
    if invitation is None:
        return []
    events = await assessment_service.list_monitoring_events(db, invitation.id)
    return [MonitoringEventResponse.model_validate(e) for e in events]


@router.post("/{application_id}/status", response_model=ApplicationResponse)
async def change_application_status(
    application_id: uuid.UUID,
    payload: ApplicationStatusChangeRequest,
    current_user: User = Depends(require_permission("application.status.change")),
    db: AsyncSession = Depends(get_db),
) -> ApplicationResponse:
    application = await application_service.get_application(db, application_id)
    if application is None:
        raise NotFoundError("Application not found.")
    updated = await application_service.change_status(
        db,
        application,
        to_status=payload.to_status,
        actor_user_id=current_user.id,
        reason=payload.reason,
    )

    # Deliberately no email here: candidate email is manual-only, sent from
    # the explicit "Send Email" action (`POST .../email/send`).
    return to_response(updated)


@router.post("/{application_id}/ai-override", response_model=ApplicationResponse)
async def override_ai_screening(
    application_id: uuid.UUID,
    payload: AIScreeningOverrideRequest,
    current_user: User = Depends(require_permission("application.status.change")),
    db: AsyncSession = Depends(get_db),
) -> ApplicationResponse:
    """HR has final authority: moves an AI-screened-out application back into
    review (UNDER_REVIEW). The reason is required and audited."""
    application = await application_service.get_application(db, application_id)
    if application is None:
        raise NotFoundError("Application not found.")
    updated = await application_service.override_ai_screening(
        db, application, actor=current_user, reason=payload.reason
    )
    return to_response(updated)


@router.post("/{application_id}/email/compose", response_model=EmailComposeResponse)
async def compose_email(
    application_id: uuid.UUID,
    payload: EmailComposeRequest,
    current_user: User = Depends(require_permission("application.email.send")),
    db: AsyncSession = Depends(get_db),
) -> EmailComposeResponse:
    """Loads a predefined template for this application — candidate, job,
    company and recruiter details filled in — as an editable subject/body.
    Nothing is sent."""
    composed = await email_composer.compose_email(
        db, application_id=application_id, actor=current_user, request=payload
    )
    return EmailComposeResponse(
        template_key=composed.template_key,
        subject=composed.subject,
        body=composed.body,
        variables=composed.variables,
        missing_required=composed.missing_required,
    )


@router.post("/{application_id}/email/preview", response_model=EmailPreviewResponse)
async def preview_email(
    application_id: uuid.UUID,
    payload: EmailDraftRequest,
    current_user: User = Depends(require_permission("application.email.send")),
    db: AsyncSession = Depends(get_db),
) -> EmailPreviewResponse:
    """Renders the (possibly edited) draft exactly as it would be sent.
    Nothing is sent."""
    previewed = await email_composer.preview_email(
        db, application_id=application_id, actor=current_user, draft=payload
    )
    return EmailPreviewResponse(
        to=previewed.to,
        reply_to=previewed.reply_to,
        subject=previewed.rendered.subject,
        html=previewed.rendered.html,
        text=previewed.rendered.text,
        has_call_to_action=previewed.has_call_to_action,
    )


@router.post("/{application_id}/email/send", response_model=EmailSendResponse)
async def send_email(
    application_id: uuid.UUID,
    payload: EmailDraftRequest,
    current_user: User = Depends(require_permission("application.email.send")),
    db: AsyncSession = Depends(get_db),
) -> EmailSendResponse:
    """The only way a candidate is emailed: an explicit action by an
    authorized recruiter/admin (never triggered by applying, assigning an
    assessment, or a status change). Delivery is never faked: no SMTP
    configuration -> 503 `email_not_configured`; the provider failing or
    refusing -> 502 `email_delivery_failed`. Both outcomes are recorded in
    Activities."""
    sent = await email_composer.send_email(
        db, application_id=application_id, actor=current_user, draft=payload
    )
    return EmailSendResponse(sent=True, to=sent.to, subject=sent.rendered.subject)
