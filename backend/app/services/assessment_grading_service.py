"""Assessment grading (docs/assessment.md § 6 / § 10).

Automatic grading runs at submission for every question it can decide:
choice questions against `QuestionOption.is_correct`, short answers against
`QuestionAnswerKey.accepted_answers`. Descriptive and coding answers — and
short answers with no accepted answers configured — wait for a person
(`awarded_points` NULL), and the attempt's result stays
PENDING_MANUAL_REVIEW, with `passed` NULL, until every one is graded.

Marks are always bounded server-side: a grader can award 0..points, never
more; negative marking only ever applies to automatically graded wrong
answers, and the total never goes below zero.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.models.assessment import (
    CHOICE_QUESTION_TYPES,
    MANUAL_QUESTION_TYPES,
    TEXT_QUESTION_TYPES,
    Assessment,
    AssessmentInvitation,
    AssessmentResult,
    CandidateAnswer,
    GradingMethod,
    InvitationStatus,
    Question,
    QuestionType,
    ResultGradingStatus,
)
from app.models.user import User
from app.services import activity_service


def _normalize_text(value: str, *, case_sensitive: bool) -> str:
    collapsed = " ".join(value.split())
    return collapsed if case_sensitive else collapsed.casefold()


@dataclass(frozen=True)
class AutoGrade:
    is_correct: bool | None
    awarded_points: int | None
    method: GradingMethod | None


def needs_manual_grading(question: Question) -> bool:
    if question.type in MANUAL_QUESTION_TYPES:
        return True
    if question.type == QuestionType.SHORT_ANSWER:
        key = question.answer_key
        return key is None or not key.accepted_answers
    return False


def auto_grade(
    question: Question, *, selected_option_ids: list[uuid.UUID], text_answer: str | None
) -> AutoGrade:
    """Marks for one submitted answer, or `awarded_points=None` when it must
    be graded by a person. A blank answer is always worth 0 — nothing to
    grade and nothing to deduct."""
    if question.type in CHOICE_QUESTION_TYPES:
        if not selected_option_ids:
            return AutoGrade(is_correct=False, awarded_points=0, method=GradingMethod.AUTO)
        correct_ids = {option.id for option in question.options if option.is_correct}
        if set(selected_option_ids) == correct_ids:
            return AutoGrade(is_correct=True, awarded_points=question.points, method=GradingMethod.AUTO)
        return AutoGrade(
            is_correct=False, awarded_points=-question.negative_points, method=GradingMethod.AUTO
        )

    if not (text_answer and text_answer.strip()):
        return AutoGrade(is_correct=False, awarded_points=0, method=GradingMethod.AUTO)
    if needs_manual_grading(question):
        return AutoGrade(is_correct=None, awarded_points=None, method=None)

    key = question.answer_key
    assert key is not None
    given = _normalize_text(text_answer, case_sensitive=key.case_sensitive)
    accepted = {_normalize_text(a, case_sensitive=key.case_sensitive) for a in key.accepted_answers}
    if given in accepted:
        return AutoGrade(is_correct=True, awarded_points=question.points, method=GradingMethod.AUTO)
    return AutoGrade(
        is_correct=False, awarded_points=-question.negative_points, method=GradingMethod.AUTO
    )


@dataclass(frozen=True)
class Totals:
    score: int
    max_score: int
    percentage: int
    pending: bool


def totals(questions: list[Question], answers: list[CandidateAnswer]) -> Totals:
    max_score = sum(question.points for question in questions)
    awarded = [a.awarded_points for a in answers]
    raw = sum(points for points in awarded if points is not None)
    score = max(0, raw)
    percentage = round((score / max_score) * 100) if max_score > 0 else 0
    return Totals(
        score=score,
        max_score=max_score,
        percentage=percentage,
        pending=any(points is None for points in awarded),
    )


def apply_totals(result: AssessmentResult, figures: Totals, *, pass_score: int) -> None:
    """`pass_score` is fixed onto the result the first time (at submission),
    so manual grading finished later is judged against the pass mark the
    attempt was taken under, not whatever the test says today."""
    if result.pass_score_applied is None:
        result.pass_score_applied = pass_score
    result.score = figures.score
    result.max_score = figures.max_score
    result.percentage = figures.percentage
    if figures.pending:
        result.grading_status = ResultGradingStatus.PENDING_MANUAL_REVIEW
        result.passed = None
    else:
        result.grading_status = ResultGradingStatus.FINAL
        result.passed = figures.percentage >= result.pass_score_applied
    result.evaluated_at = datetime.now(UTC)


async def load_attempt(
    db: AsyncSession, organization_id: uuid.UUID, invitation_id: uuid.UUID
) -> AssessmentInvitation:
    """The attempt with its assessment, questions (options + answer keys)
    and result — this organization's only (a foreign id is a 404)."""
    result = await db.execute(
        select(AssessmentInvitation)
        .where(
            AssessmentInvitation.id == invitation_id,
            AssessmentInvitation.organization_id == organization_id,
        )
        .options(
            joinedload(AssessmentInvitation.assessment)
            .selectinload(Assessment.questions)
            .selectinload(Question.options),
            joinedload(AssessmentInvitation.assessment)
            .selectinload(Assessment.questions)
            .selectinload(Question.answer_key),
            joinedload(AssessmentInvitation.result),
        )
        .execution_options(populate_existing=True)
    )
    invitation = result.unique().scalar_one_or_none()
    if invitation is None:
        raise NotFoundError("Assessment attempt not found.")
    return invitation


async def answers_for(db: AsyncSession, invitation_id: uuid.UUID) -> list[CandidateAnswer]:
    result = await db.execute(
        select(CandidateAnswer).where(CandidateAnswer.invitation_id == invitation_id)
    )
    return list(result.scalars().all())


async def grade_attempt(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
    grades: list[tuple[uuid.UUID, int, str | None]],
    actor: User,
) -> AssessmentInvitation:
    """Records a grader's marks for text-answer questions of a submitted
    attempt, then recomputes the result — FINAL (with pass/fail) once no
    answer is left ungraded. Re-grading is allowed and audited."""
    invitation = await load_attempt(db, organization_id, invitation_id)
    if invitation.status != InvitationStatus.SUBMITTED or invitation.result is None:
        raise ConflictError("Only a submitted attempt can be graded.")

    questions = {question.id: question for question in invitation.assessment.questions}
    answers = {answer.question_id: answer for answer in await answers_for(db, invitation.id)}
    now = datetime.now(UTC)
    changes: list[str] = []
    for question_id, awarded, comment in grades:
        question = questions.get(question_id)
        if question is None:
            raise UnprocessableError("That question is not part of this assessment.")
        if question.type not in TEXT_QUESTION_TYPES:
            raise UnprocessableError(
                "Choice questions are graded automatically and can't be graded manually."
            )
        if awarded > question.points:
            raise UnprocessableError(
                f"At most {question.points} mark(s) can be awarded for this question.",
                code="marks_exceed_maximum",
            )
        answer = answers.get(question_id)
        if answer is None or not (answer.text_answer and answer.text_answer.strip()):
            raise UnprocessableError("The candidate didn't answer this question; there is nothing to grade.")
        previous = answer.awarded_points
        answer.awarded_points = awarded
        answer.is_correct = awarded == question.points
        answer.grading_method = GradingMethod.MANUAL
        answer.graded_by_user_id = actor.id
        answer.graded_at = now
        answer.grader_comment = comment.strip() if comment and comment.strip() else None
        changes.append(
            f"Q{question.order_index + 1}: {'ungraded' if previous is None else previous} -> {awarded}"
        )
    await db.flush()

    result = invitation.result
    was = result.grading_status
    apply_totals(
        result,
        totals(invitation.assessment.questions, list(answers.values())),
        pass_score=invitation.assessment.pass_score,
    )
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=organization_id,
        actor=actor,
        action="ASSESSMENT_GRADED",
        entity_type="application",
        entity_id=invitation.application_id,
        entity_label=f"{invitation.assessment.title} (attempt {invitation.attempt_number})",
        description=(
            f"Manual grading recorded ({'; '.join(changes)}). "
            + (
                f"Result is now final: {result.percentage}% — "
                f"{'passed' if result.passed else 'failed'}."
                if result.grading_status == ResultGradingStatus.FINAL
                else "Some answers are still awaiting grading."
            )
            + (" (re-graded)" if was == ResultGradingStatus.FINAL else "")
        ),
    )
    return await load_attempt(db, organization_id, invitation_id)


async def pending_review_attempts(
    db: AsyncSession, organization_id: uuid.UUID, *, assessment_id: uuid.UUID | None = None
) -> list[AssessmentInvitation]:
    query = (
        select(AssessmentInvitation)
        .join(AssessmentResult, AssessmentResult.invitation_id == AssessmentInvitation.id)
        .where(
            AssessmentInvitation.organization_id == organization_id,
            AssessmentResult.grading_status == ResultGradingStatus.PENDING_MANUAL_REVIEW,
        )
        .options(selectinload(AssessmentInvitation.assessment), selectinload(AssessmentInvitation.result))
        .order_by(AssessmentInvitation.submitted_at.asc())
    )
    if assessment_id is not None:
        query = query.where(AssessmentInvitation.assessment_id == assessment_id)
    result = await db.execute(query)
    return list(result.scalars().all())
