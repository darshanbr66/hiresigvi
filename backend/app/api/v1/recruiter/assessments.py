"""Recruiter-facing Assessment management + candidate invitations
(docs/assessment.md). `organization_id` always comes from the authenticated
caller, never the request."""

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.integrations.documents.question_import import MAX_IMPORT_BYTES
from app.models.assessment import (
    CHOICE_QUESTION_TYPES,
    Assessment,
    AssessmentInvitation,
    AssessmentStatus,
    InvitationStatus,
    Question,
)
from app.models.user import User
from app.schemas.assessment import (
    AssessmentCreateRequest,
    AssessmentDeleteRequest,
    AssessmentInvitationResponse,
    AssessmentResponse,
    AssessmentResultResponse,
    AssessmentStatusChangeRequest,
    AssessmentSummary,
    AssessmentUpdateRequest,
    AttemptAnswerView,
    AttemptReviewResponse,
    GradeAttemptRequest,
    InviteCandidateRequest,
    ParsedQuestionsResponse,
    QuestionOptionResponse,
    QuestionResponse,
    RetestRequest,
)
from app.schemas.public_assessment import PublicQuestion, PublicQuestionOption
from app.services import application_service, assessment_grading_service, assessment_service

router = APIRouter(prefix="/assessments", tags=["recruiter-assessments"])


def _question_response(question: Question) -> QuestionResponse:
    key = question.answer_key
    return QuestionResponse(
        id=question.id,
        prompt=question.prompt,
        type=question.type,
        points=question.points,
        negative_points=question.negative_points,
        options=[QuestionOptionResponse.model_validate(o) for o in question.options],
        accepted_answers=list(key.accepted_answers) if key else [],
        case_sensitive=key.case_sensitive if key else False,
        grading_notes=key.grading_notes if key else None,
        explanation=key.explanation if key else None,
        source_bank_version_id=question.source_bank_version_id,
    )


async def _assessment_response(db: AsyncSession, assessment: Assessment) -> AssessmentResponse:
    return AssessmentResponse(
        id=assessment.id,
        title=assessment.title,
        instructions=assessment.instructions,
        duration_minutes=assessment.duration_minutes,
        pass_score=assessment.pass_score,
        status=assessment.status,
        deleted_at=assessment.deleted_at,
        created_at=assessment.created_at,
        updated_at=assessment.updated_at,
        questions=[_question_response(q) for q in assessment.questions],
        has_invitations=await assessment_service.assessment_has_invitations(db, assessment.id),
    )


class AssessmentPreviewResponse(BaseModel):
    """Exactly what a candidate would be shown — the public question shape,
    with no answer key."""

    title: str
    instructions: str
    duration_minutes: int
    questions: list[PublicQuestion]


class PendingReviewEntry(BaseModel):
    invitation_id: uuid.UUID
    application_id: uuid.UUID
    assessment_id: uuid.UUID
    assessment_title: str
    candidate_full_name: str
    attempt_number: int
    submitted_at: str | None


async def _invitation_response(
    db: AsyncSession, invitation: AssessmentInvitation, *, invitation_link: str | None = None
) -> AssessmentInvitationResponse:
    application = await application_service.get_application(db, invitation.application_id)
    return AssessmentInvitationResponse(
        id=invitation.id,
        assessment_id=invitation.assessment_id,
        assessment_title=invitation.assessment.title,
        application_id=invitation.application_id,
        candidate_full_name=application.candidate.full_name if application else "",
        status=invitation.status,
        expires_at=invitation.expires_at,
        started_at=invitation.started_at,
        submitted_at=invitation.submitted_at,
        emailed_at=invitation.emailed_at,
        attempt_number=invitation.attempt_number,
        retest_reason=invitation.retest_reason,
        result=AssessmentResultResponse.model_validate(invitation.result) if invitation.result else None,
        invitation_link=invitation_link,
    )


@router.post("/parse-questions", response_model=ParsedQuestionsResponse)
async def parse_import_questions(
    file: UploadFile = File(...),
    _: User = Depends(require_permission("assessment.manage")),
) -> ParsedQuestionsResponse:
    # One byte over the limit is enough for the parser to refuse it.
    content = await file.read(MAX_IMPORT_BYTES + 1)
    return assessment_service.parse_import_file(content=content, filename=file.filename or "")


@router.post("", response_model=AssessmentResponse, status_code=status.HTTP_201_CREATED)
async def create_assessment(
    payload: AssessmentCreateRequest,
    current_user: User = Depends(require_permission("assessment.manage")),
    db: AsyncSession = Depends(get_db),
) -> AssessmentResponse:
    assert current_user.organization_id is not None
    assessment = await assessment_service.create_assessment(
        db,
        organization_id=current_user.organization_id,
        created_by=current_user.id,
        payload=payload,
        actor=current_user,
    )
    return await _assessment_response(db, assessment)


@router.post("/{assessment_id}/delete", response_model=AssessmentResponse)
async def delete_assessment(
    assessment_id: uuid.UUID,
    payload: AssessmentDeleteRequest,
    current_user: User = Depends(require_permission("assessment.delete")),
    db: AsyncSession = Depends(get_db),
) -> AssessmentResponse:
    assessment = await assessment_service.get_assessment(db, assessment_id)
    if assessment is None:
        raise NotFoundError("Assessment not found.")
    deleted = await assessment_service.delete_assessment(
        db, assessment, actor=current_user, reason=payload.reason
    )
    return await _assessment_response(db, deleted)


@router.get("", response_model=list[AssessmentSummary])
async def list_assessments(
    assessment_status: AssessmentStatus | None = Query(default=None, alias="status"),
    current_user: User = Depends(require_permission("assessment.read")),
    db: AsyncSession = Depends(get_db),
) -> list[AssessmentSummary]:
    assert current_user.organization_id is not None
    assessments = await assessment_service.list_assessments(db, current_user.organization_id)
    return [
        AssessmentSummary(
            id=a.id,
            title=a.title,
            duration_minutes=a.duration_minutes,
            pass_score=a.pass_score,
            question_count=len(a.questions),
            status=a.status,
            created_at=a.created_at,
        )
        for a in assessments
        if assessment_status is None or a.status == assessment_status
    ]


@router.get("/pending-review", response_model=list[PendingReviewEntry])
async def list_attempts_pending_review(
    assessment_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(require_permission("assessment.read")),
    db: AsyncSession = Depends(get_db),
) -> list[PendingReviewEntry]:
    """Submitted attempts with answers still waiting for a grader."""
    assert current_user.organization_id is not None
    attempts = await assessment_grading_service.pending_review_attempts(
        db, current_user.organization_id, assessment_id=assessment_id
    )
    entries: list[PendingReviewEntry] = []
    for attempt in attempts:
        application = await application_service.get_application(db, attempt.application_id)
        if application is None or application.deleted_at is not None:
            continue
        entries.append(
            PendingReviewEntry(
                invitation_id=attempt.id,
                application_id=attempt.application_id,
                assessment_id=attempt.assessment_id,
                assessment_title=attempt.assessment.title,
                candidate_full_name=application.candidate.full_name,
                attempt_number=attempt.attempt_number,
                submitted_at=attempt.submitted_at.isoformat() if attempt.submitted_at else None,
            )
        )
    return entries


async def _attempt_review(db: AsyncSession, attempt: AssessmentInvitation) -> AttemptReviewResponse:
    application = await application_service.get_application(db, attempt.application_id)
    answers = {
        a.question_id: a for a in await assessment_grading_service.answers_for(db, attempt.id)
    }
    grader_ids = {a.graded_by_user_id for a in answers.values() if a.graded_by_user_id}
    graders: dict[uuid.UUID, str] = {}
    if grader_ids:
        rows = await db.execute(select(User.id, User.full_name).where(User.id.in_(grader_ids)))
        graders = dict(rows.all())
    views: list[AttemptAnswerView] = []
    for question in attempt.assessment.questions:
        answer = answers.get(question.id)
        key = question.answer_key
        manual_capable = answer is not None and bool(answer.text_answer)
        views.append(
            AttemptAnswerView(
                question_id=question.id,
                order=question.order_index,
                prompt=question.prompt,
                type=question.type,
                points=question.points,
                negative_points=question.negative_points,
                options=[QuestionOptionResponse.model_validate(o) for o in question.options],
                accepted_answers=list(key.accepted_answers) if key else [],
                grading_notes=key.grading_notes if key else None,
                explanation=key.explanation if key else None,
                answer_id=answer.id if answer else None,
                selected_option_ids=(
                    [uuid.UUID(i) for i in answer.selected_option_ids] if answer else []
                ),
                text_answer=answer.text_answer if answer else None,
                is_correct=answer.is_correct if answer else None,
                awarded_points=answer.awarded_points if answer else None,
                grading_method=answer.grading_method if answer else None,
                # Ungraded = an answered text question with no marks yet.
                needs_manual_grading=answer is not None and answer.awarded_points is None,
                manually_gradable=question.type not in CHOICE_QUESTION_TYPES and manual_capable,
                graded_by_name=(
                    graders.get(answer.graded_by_user_id)
                    if answer and answer.graded_by_user_id
                    else None
                ),
                graded_at=answer.graded_at if answer else None,
                grader_comment=answer.grader_comment if answer else None,
            )
        )
    return AttemptReviewResponse(
        invitation_id=attempt.id,
        application_id=attempt.application_id,
        candidate_full_name=application.candidate.full_name if application else "",
        assessment_title=attempt.assessment.title,
        attempt_number=attempt.attempt_number,
        status=attempt.status,
        submitted_at=attempt.submitted_at,
        result=AssessmentResultResponse.model_validate(attempt.result) if attempt.result else None,
        answers=views,
    )


@router.get("/attempts/{invitation_id}/review", response_model=AttemptReviewResponse)
async def review_attempt(
    invitation_id: uuid.UUID,
    current_user: User = Depends(require_permission("assessment.read")),
    db: AsyncSession = Depends(get_db),
) -> AttemptReviewResponse:
    """The candidate's responses next to the answer key — staff only."""
    assert current_user.organization_id is not None
    attempt = await assessment_grading_service.load_attempt(
        db, current_user.organization_id, invitation_id
    )
    if attempt.status != InvitationStatus.SUBMITTED:
        raise NotFoundError("This attempt hasn't been submitted yet.")
    return await _attempt_review(db, attempt)


@router.post("/attempts/{invitation_id}/grades", response_model=AttemptReviewResponse)
async def grade_attempt(
    invitation_id: uuid.UUID,
    payload: GradeAttemptRequest,
    current_user: User = Depends(require_permission("assessment.grade")),
    db: AsyncSession = Depends(get_db),
) -> AttemptReviewResponse:
    assert current_user.organization_id is not None
    attempt = await assessment_grading_service.grade_attempt(
        db,
        organization_id=current_user.organization_id,
        invitation_id=invitation_id,
        grades=[(g.question_id, g.awarded_points, g.comment) for g in payload.grades],
        actor=current_user,
    )
    return await _attempt_review(db, attempt)


@router.get("/{assessment_id}", response_model=AssessmentResponse)
async def get_assessment(
    assessment_id: uuid.UUID,
    _: User = Depends(require_permission("assessment.read")),
    db: AsyncSession = Depends(get_db),
) -> AssessmentResponse:
    assessment: Assessment | None = await assessment_service.get_assessment(db, assessment_id)
    if assessment is None:
        raise NotFoundError("Assessment not found.")
    return await _assessment_response(db, assessment)


@router.get("/{assessment_id}/preview", response_model=AssessmentPreviewResponse)
async def preview_assessment(
    assessment_id: uuid.UUID,
    _: User = Depends(require_permission("assessment.read")),
    db: AsyncSession = Depends(get_db),
) -> AssessmentPreviewResponse:
    assessment = await assessment_service.get_assessment(db, assessment_id)
    if assessment is None:
        raise NotFoundError("Assessment not found.")
    return AssessmentPreviewResponse(
        title=assessment.title,
        instructions=assessment.instructions,
        duration_minutes=assessment.duration_minutes,
        questions=[
            PublicQuestion(
                id=q.id,
                prompt=q.prompt,
                type=q.type,
                points=q.points,
                options=[PublicQuestionOption(id=o.id, label=o.label) for o in q.options],
            )
            for q in assessment.questions
        ],
    )


@router.post("/{assessment_id}/status", response_model=AssessmentResponse)
async def change_assessment_status(
    assessment_id: uuid.UUID,
    payload: AssessmentStatusChangeRequest,
    current_user: User = Depends(require_permission("assessment.manage")),
    db: AsyncSession = Depends(get_db),
) -> AssessmentResponse:
    assessment = await assessment_service.get_assessment(db, assessment_id)
    if assessment is None:
        raise NotFoundError("Assessment not found.")
    updated = await assessment_service.change_status(
        db, assessment, payload.status, actor=current_user
    )
    return await _assessment_response(db, updated)


@router.patch("/{assessment_id}", response_model=AssessmentResponse)
async def update_assessment(
    assessment_id: uuid.UUID,
    payload: AssessmentUpdateRequest,
    current_user: User = Depends(require_permission("assessment.manage")),
    db: AsyncSession = Depends(get_db),
) -> AssessmentResponse:
    assessment = await assessment_service.get_assessment(db, assessment_id)
    if assessment is None or assessment.deleted_at is not None:
        raise NotFoundError("Assessment not found.")
    updated = await assessment_service.update_assessment(db, assessment, payload, actor=current_user)
    return await _assessment_response(db, updated)


@router.post("/invite", response_model=AssessmentInvitationResponse, status_code=status.HTTP_201_CREATED)
async def invite_candidate(
    payload: InviteCandidateRequest,
    current_user: User = Depends(require_permission("assessment.manage")),
    db: AsyncSession = Depends(get_db),
) -> AssessmentInvitationResponse:
    assert current_user.organization_id is not None
    invitation, raw_token = await assessment_service.invite_candidate(
        db,
        organization_id=current_user.organization_id,
        assessment_id=payload.assessment_id,
        application_id=payload.application_id,
        invited_by_user_id=current_user.id,
    )
    # Surfaced so a recruiter can copy the link directly when no email
    # provider is configured locally (see app/integrations/email) — this is
    # the one response that ever carries it, matching the "shown once"
    # handling of the underlying raw token.
    link = f"{get_settings().frontend_base_url}/assessment/{raw_token}"
    return await _invitation_response(db, invitation, invitation_link=link)


@router.post("/retest", response_model=AssessmentInvitationResponse, status_code=status.HTTP_201_CREATED)
async def retest_candidate(
    payload: RetestRequest,
    current_user: User = Depends(require_permission("assessment.manage")),
    db: AsyncSession = Depends(get_db),
) -> AssessmentInvitationResponse:
    assert current_user.organization_id is not None
    invitation, raw_token = await assessment_service.create_retest(
        db,
        organization_id=current_user.organization_id,
        application_id=payload.application_id,
        authorized_by=current_user,
        reason=payload.reason,
        assessment_choice=payload.assessment_choice,
        assessment_id=payload.assessment_id,
        new_assessment=payload.new_assessment,
    )
    link = f"{get_settings().frontend_base_url}/assessment/{raw_token}"
    return await _invitation_response(db, invitation, invitation_link=link)
