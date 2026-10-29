"""Interview rounds of an application (docs/recruitment-workflow.md § 2a).

`interview.read` to list (ORG_ADMIN, RECRUITER, HIRING_MANAGER, INTERVIEWER);
`interview.manage` to schedule, cancel and record any outcome. An
interviewer without `interview.manage` may record the outcome of — and read
the feedback on — only the interviews assigned to them. Tenancy: the
application is checked against the caller's organization here as well as
by RLS.
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import granted_permission_codes, require_permission
from app.api.v1.recruiter.programs import interview_response, user_names
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.application import Application
from app.models.interview import ApplicationInterview
from app.models.user import User
from app.schemas.program import (
    InterviewCloseRequest,
    InterviewListResponse,
    InterviewOutcomeRequest,
    InterviewResponse,
    InterviewScheduleRequest,
)
from app.services import application_service, interview_service

router = APIRouter(prefix="/applications/{application_id}/interviews", tags=["recruiter-interviews"])


async def _application(db: AsyncSession, user: User, application_id: uuid.UUID) -> Application:
    application = await application_service.get_application(db, application_id)
    if (
        application is None
        or application.organization_id != user.organization_id
        or application.deleted_at is not None
    ):
        raise NotFoundError("Application not found.")
    return application


async def _out(db: AsyncSession, user: User, interview: ApplicationInterview) -> InterviewResponse:
    codes = await granted_permission_codes(db, user)
    names = await user_names(db, {interview.interviewer_user_id} if interview.interviewer_user_id else set())
    return interview_response(interview, user=user, codes=codes, names=names)


@router.get("", response_model=InterviewListResponse)
async def list_interviews(
    application_id: uuid.UUID,
    current_user: User = Depends(require_permission("interview.read")),
    db: AsyncSession = Depends(get_db),
) -> InterviewListResponse:
    application = await _application(db, current_user, application_id)
    interviews = await interview_service.list_for_application(db, application.organization_id, application.id)
    codes = await granted_permission_codes(db, current_user)
    names = await user_names(db, {i.interviewer_user_id for i in interviews if i.interviewer_user_id})
    return InterviewListResponse(
        items=[interview_response(i, user=current_user, codes=codes, names=names) for i in interviews],
        viewer_permissions=sorted(c for c in codes if c.startswith("interview.")),
    )


@router.post("", response_model=InterviewResponse, status_code=status.HTTP_201_CREATED)
async def schedule_interview(
    application_id: uuid.UUID,
    payload: InterviewScheduleRequest,
    current_user: User = Depends(require_permission("interview.manage")),
    db: AsyncSession = Depends(get_db),
) -> InterviewResponse:
    application = await _application(db, current_user, application_id)
    interview = await interview_service.schedule(
        db,
        application=application,
        stage=payload.stage,
        scheduled_at=payload.scheduled_at,
        duration_minutes=payload.duration_minutes,
        location=payload.location,
        interviewer_user_id=payload.interviewer_user_id,
        actor=current_user,
    )
    return await _out(db, current_user, interview)


@router.post("/{interview_id}/outcome", response_model=InterviewResponse)
async def record_outcome(
    application_id: uuid.UUID,
    interview_id: uuid.UUID,
    payload: InterviewOutcomeRequest,
    current_user: User = Depends(require_permission("interview.read")),
    db: AsyncSession = Depends(get_db),
) -> InterviewResponse:
    application = await _application(db, current_user, application_id)
    interview = await interview_service.require_interview(db, application, interview_id)
    codes = await granted_permission_codes(db, current_user)
    await interview_service.record_outcome(
        db,
        application=application,
        interview=interview,
        outcome=payload.outcome,
        rating=payload.rating,
        feedback=payload.feedback,
        actor=current_user,
        can_manage="interview.manage" in codes,
    )
    return await _out(db, current_user, interview)


@router.post("/{interview_id}/close", response_model=InterviewResponse)
async def close_interview(
    application_id: uuid.UUID,
    interview_id: uuid.UUID,
    payload: InterviewCloseRequest,
    current_user: User = Depends(require_permission("interview.manage")),
    db: AsyncSession = Depends(get_db),
) -> InterviewResponse:
    application = await _application(db, current_user, application_id)
    interview = await interview_service.require_interview(db, application, interview_id)
    await interview_service.close_without_outcome(
        db, application=application, interview=interview, status=payload.status,
        reason=payload.reason, actor=current_user,
    )
    return await _out(db, current_user, interview)
