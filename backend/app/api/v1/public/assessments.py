"""Candidate-facing assessment access by opaque token — no authentication
(docs/assessment.md § 4)."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.db.session import get_db
from app.models.organization import Organization
from app.schemas.public_assessment import (
    MonitoringEventBatchCreate,
    PublicInvitationView,
    PublicQuestion,
    PublicQuestionOption,
    PublicSubmissionResult,
    SubmitAnswersRequest,
)
from app.services import application_service, assessment_public_service

router = APIRouter(prefix="/assessment", tags=["public-assessment"])


async def _to_public_view(db: AsyncSession, invitation) -> PublicInvitationView:
    application = await application_service.get_application(db, invitation.application_id)
    organization = await db.get(Organization, invitation.organization_id)
    assessment = invitation.assessment
    return PublicInvitationView(
        status=invitation.status,
        assessment_title=assessment.title,
        instructions=assessment.instructions,
        duration_minutes=assessment.duration_minutes,
        job_title=application.job.title if application else "",
        organization_name=organization.name if organization else "",
        expires_at=invitation.expires_at,
        started_at=invitation.started_at,
        questions=[
            PublicQuestion(
                id=question.id,
                prompt=question.prompt,
                type=question.type,
                points=question.points,
                options=[
                    PublicQuestionOption(id=option.id, label=option.label)
                    for option in question.options
                ],
            )
            for question in assessment.questions
        ],
    )


@router.get("/{token}", response_model=PublicInvitationView)
async def get_invitation(token: str, db: AsyncSession = Depends(get_db)) -> PublicInvitationView:
    invitation = await assessment_public_service.get_invitation(db, token)
    return await _to_public_view(db, invitation)


@router.post("/{token}/start", response_model=PublicInvitationView)
async def start_assessment(token: str, db: AsyncSession = Depends(get_db)) -> PublicInvitationView:
    invitation = await assessment_public_service.start_attempt(db, token)
    return await _to_public_view(db, invitation)


@router.post("/{token}/submit", response_model=PublicSubmissionResult)
async def submit_assessment(
    token: str, payload: SubmitAnswersRequest, db: AsyncSession = Depends(get_db)
) -> PublicSubmissionResult:
    if not payload.answers:
        raise AppError("At least one answer is required.", code="no_answers")
    answers = [
        (answer.question_id, answer.selected_option_ids, answer.text_answer)
        for answer in payload.answers
    ]
    result = await assessment_public_service.submit_attempt(db, token, answers)
    return PublicSubmissionResult(submitted_at=result.evaluated_at)


@router.post("/{token}/events", status_code=status.HTTP_204_NO_CONTENT)
async def record_monitoring_events(
    token: str, payload: MonitoringEventBatchCreate, db: AsyncSession = Depends(get_db)
) -> None:
    await assessment_public_service.record_monitoring_events(db, token, payload.events)
