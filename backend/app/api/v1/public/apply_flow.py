"""The progressive self-service apply flow's endpoints — the same set for a
careers-site job (`/organizations/{slug}/jobs/{job_id}/...`) and a campus
drive link (`/campus-drive/{token}/...`). Handlers only validate the
transport and call app/services/application_draft_service.py, which owns
every rule.

    GET  .../qualification-options          the JD-derived qualification list
    POST .../apply/draft                    start/resume (after email verify)
    POST .../apply/draft/view               current draft
    PUT  .../apply/draft/background         Step 3
    PUT  .../apply/draft/education          Step 4 details
    POST .../apply/draft/education/document Step 4 upload + AI check
    POST .../apply/draft/education/recheck  re-run the check
    POST .../apply/draft/education/manual-review
    POST .../apply                          Step 6: resume + submit

The verification token travels in the request body (or form), never in a
URL, so it doesn't end up in access logs.
"""

import uuid
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.public.applications import read_marks_card
from app.api.v1.public.candidate_intake import (
    CandidateIntakeRateLimiters,
    enforce,
    get_candidate_intake_rate_limiters,
    resolve_organization,
)
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db.session import get_db
from app.integrations.storage import ResumeStorage, get_resume_storage
from app.schemas.application_draft import (
    BackgroundSave,
    DraftAuth,
    DraftStart,
    DraftView,
    EducationSave,
    QualificationOptionsView,
    QualificationOptionView,
)
from app.schemas.public import PublicApplicationResult
from app.schemas.public_campus_drive import PublicCampusDriveApplicationResult
from app.services import (
    application_draft_service,
    public_application_service,
    public_campus_drive_service,
)
from app.services.application_draft_service import ApplyContext, SubmittedDraft
from app.services.education import job_qualification_service
from app.services.education.job_qualification_service import QualificationRequirements

ACCEPTED_DOCUMENT_TYPES = ["PDF", "PNG", "JPG"]


async def careers_context(
    slug: str, job_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ApplyContext:
    organization = await resolve_organization(db, slug)
    job = await public_application_service.load_job(
        db, job_id, organization_id=organization.id, campus_drive=None
    )
    return ApplyContext(organization=organization, job=job)


async def campus_context(token: str, db: AsyncSession = Depends(get_db)) -> ApplyContext:
    drive, organization = await public_campus_drive_service.resolve_active_drive(db, token)
    job = await public_application_service.load_job(
        db, drive.job_id, organization_id=organization.id, campus_drive=drive
    )
    return ApplyContext(organization=organization, job=job, campus_drive=drive)


def options_view(requirements: QualificationRequirements) -> QualificationOptionsView:
    return QualificationOptionsView(
        restricted=requirements.restricted,
        summary=requirements.summary,
        options=[
            QualificationOptionView(
                key=option.key,
                label=option.label,
                level=option.level.value,
                branch_label=option.branch_label,
                branch_required=application_draft_service.branch_required(option, requirements),
                requires_degree_name=option.is_other,
                document_hint=option.document_hint,
            )
            for option in application_draft_service.offered_options(requirements)
        ],
        fields_of_study=requirements.fields_of_study if requirements.field_mandatory else [],
        field_mandatory=requirements.field_mandatory,
        accepted_document_types=ACCEPTED_DOCUMENT_TYPES,
        max_document_size_mb=get_settings().max_academic_document_size_mb,
    )


def _careers_result(ctx: ApplyContext, result: SubmittedDraft) -> PublicApplicationResult:
    application = result.submission.application
    return PublicApplicationResult(
        id=application.id,
        job_title=application.job.title,
        candidate_email=application.candidate.email,
        confirmation_email_sent=result.submission.confirmation_email_sent,
        careers_contact_email=ctx.organization.careers_contact_email,
        submitted_at=application.applied_at,
    )


def _campus_result(ctx: ApplyContext, result: SubmittedDraft) -> PublicCampusDriveApplicationResult:
    base = _careers_result(ctx, result)
    return PublicCampusDriveApplicationResult(
        **base.model_dump(), assessment_invitation_link=result.assessment_invitation_link
    )


def build_router(
    *,
    prefix: str,
    tags: list[str | Enum],
    context: Callable[..., Awaitable[ApplyContext]],
    result_model: type[PublicApplicationResult],
    to_result: Callable[[ApplyContext, SubmittedDraft], Any],
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=tags)

    @router.get("/qualification-options", response_model=QualificationOptionsView)
    async def qualification_options(
        request: Request,
        ctx: ApplyContext = Depends(context),
        db: AsyncSession = Depends(get_db),
        limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
    ) -> QualificationOptionsView:
        enforce(limiters.draft, request)
        return options_view(await job_qualification_service.get_requirements(db, ctx.job))

    @router.post("/apply/draft", response_model=DraftView)
    async def start_draft(
        payload: DraftStart,
        request: Request,
        ctx: ApplyContext = Depends(context),
        db: AsyncSession = Depends(get_db),
        limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
    ) -> DraftView:
        enforce(limiters.draft, request)
        draft = await application_draft_service.start_draft(
            db,
            ctx,
            email=str(payload.email),
            token=payload.email_verification_token,
            full_name=payload.full_name,
            phone=payload.phone,
        )
        return await application_draft_service.view(db, draft)

    @router.post("/apply/draft/view", response_model=DraftView)
    async def view_draft(
        payload: DraftAuth,
        request: Request,
        ctx: ApplyContext = Depends(context),
        db: AsyncSession = Depends(get_db),
        limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
    ) -> DraftView:
        enforce(limiters.draft, request)
        draft = await application_draft_service.get_draft(
            db, ctx, email=str(payload.email), token=payload.email_verification_token
        )
        return await application_draft_service.view(db, draft)

    @router.put("/apply/draft/background", response_model=DraftView)
    async def save_background(
        payload: BackgroundSave,
        request: Request,
        ctx: ApplyContext = Depends(context),
        db: AsyncSession = Depends(get_db),
        limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
    ) -> DraftView:
        enforce(limiters.draft, request)
        draft = await application_draft_service.save_background(
            db,
            ctx,
            email=str(payload.email),
            token=payload.email_verification_token,
            background=payload.background,
        )
        return await application_draft_service.view(db, draft)

    @router.put("/apply/draft/education", response_model=DraftView)
    async def save_education(
        payload: EducationSave,
        request: Request,
        ctx: ApplyContext = Depends(context),
        db: AsyncSession = Depends(get_db),
        limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
    ) -> DraftView:
        enforce(limiters.draft, request)
        draft = await application_draft_service.save_education(
            db,
            ctx,
            email=str(payload.email),
            token=payload.email_verification_token,
            details=payload.education,
        )
        return await application_draft_service.view(db, draft)

    @router.post("/apply/draft/education/document", response_model=DraftView)
    async def upload_document(
        request: Request,
        email: EmailStr = Form(...),
        email_verification_token: str = Form(..., min_length=1, max_length=200),
        document: UploadFile = File(...),
        ctx: ApplyContext = Depends(context),
        db: AsyncSession = Depends(get_db),
        storage: ResumeStorage = Depends(get_resume_storage),
        limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
    ) -> DraftView:
        enforce(limiters.document_check, request)
        # At most one byte past the limit is read: an oversized upload is
        # rejected without being held in memory in full.
        max_bytes = get_settings().max_academic_document_size_mb * 1024 * 1024
        content = await document.read(max_bytes + 1)
        draft = await application_draft_service.upload_document(
            db,
            storage,
            ctx,
            email=str(email),
            token=email_verification_token,
            filename=document.filename or "",
            content=content,
        )
        return await application_draft_service.view(db, draft)

    @router.post("/apply/draft/education/recheck", response_model=DraftView)
    async def recheck_document(
        payload: DraftAuth,
        request: Request,
        ctx: ApplyContext = Depends(context),
        db: AsyncSession = Depends(get_db),
        limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
    ) -> DraftView:
        enforce(limiters.document_check, request)
        draft = await application_draft_service.recheck_document(
            db, ctx, email=str(payload.email), token=payload.email_verification_token
        )
        return await application_draft_service.view(db, draft)

    @router.post("/apply/draft/education/manual-review", response_model=DraftView)
    async def accept_manual_review(
        payload: DraftAuth,
        request: Request,
        ctx: ApplyContext = Depends(context),
        db: AsyncSession = Depends(get_db),
        limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
    ) -> DraftView:
        enforce(limiters.draft, request)
        draft = await application_draft_service.accept_manual_review(
            db, ctx, email=str(payload.email), token=payload.email_verification_token
        )
        return await application_draft_service.view(db, draft)

    @router.post("/apply", response_model=result_model, status_code=201)
    async def submit(
        request: Request,
        email: EmailStr = Form(...),
        email_verification_token: str = Form(..., min_length=1, max_length=200),
        resume: UploadFile = File(...),
        tenth_marks_card: UploadFile | None = File(default=None),
        ctx: ApplyContext = Depends(context),
        db: AsyncSession = Depends(get_db),
        storage: ResumeStorage = Depends(get_resume_storage),
        limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
    ) -> Any:
        enforce(limiters.apply, request)
        max_bytes = get_settings().max_resume_size_mb * 1024 * 1024
        resume_bytes = await resume.read(max_bytes + 1)
        if len(resume_bytes) > max_bytes:
            raise AppError(
                f"Resume must be smaller than {get_settings().max_resume_size_mb}MB.",
                code="file_too_large",
            )
        result = await application_draft_service.submit(
            db,
            storage,
            ctx,
            email=str(email),
            token=email_verification_token,
            resume_filename=resume.filename or "resume.pdf",
            resume_bytes=resume_bytes,
            marks_card=await read_marks_card(tenth_marks_card),
        )
        return to_result(ctx, result)

    return router


careers_router = build_router(
    prefix="/organizations/{slug}/jobs/{job_id}",
    tags=["public-apply-flow"],
    context=careers_context,
    result_model=PublicApplicationResult,
    to_result=_careers_result,
)

campus_router = build_router(
    prefix="/campus-drive/{token}",
    tags=["public-campus-drive-apply-flow"],
    context=campus_context,
    result_model=PublicCampusDriveApplicationResult,
    to_result=_campus_result,
)
