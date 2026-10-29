"""Interview rounds for an application (docs/recruitment-workflow.md § 2a).

* Scheduling is allowed only for an application that reached the shortlist
  (SHORTLISTED or INTERVIEW). Scheduling the first interview of a
  SHORTLISTED application moves it to INTERVIEW through the ordinary status
  pipeline, so every program gate (eligibility, passed test) still applies.
* One SCHEDULED interview per stage at a time (DB partial unique index).
* The outcome (PASS / FAIL / ON_HOLD) and feedback are recorded by the
  assigned interviewer or by a user with `interview.manage`. A recorded
  interview is final — corrections are a new interview, never an edit.
* An interview outcome is input to the selection decision, never the
  decision: selection stays an explicit `Application.status` change.

The candidate is invited through the existing manual email composer
(Interview Invitation template) — nothing here sends email.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.models.application import Application, ApplicationStatus
from app.models.interview import ApplicationInterview, InterviewOutcome, InterviewStatus
from app.models.notification import Notification, NotificationType
from app.models.user import User
from app.services import activity_service, application_service

#: Application statuses in which an interview can be scheduled.
SCHEDULABLE_STATUSES = frozenset({ApplicationStatus.SHORTLISTED, ApplicationStatus.INTERVIEW})


def _now() -> datetime:
    return datetime.now(UTC)


def _clean(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


async def list_for_application(
    db: AsyncSession, organization_id: uuid.UUID, application_id: uuid.UUID
) -> list[ApplicationInterview]:
    result = await db.execute(
        select(ApplicationInterview)
        .where(
            ApplicationInterview.organization_id == organization_id,
            ApplicationInterview.application_id == application_id,
        )
        .order_by(ApplicationInterview.scheduled_at, ApplicationInterview.created_at)
    )
    return list(result.scalars().all())


async def require_interview(
    db: AsyncSession, application: Application, interview_id: uuid.UUID
) -> ApplicationInterview:
    interview = await db.get(ApplicationInterview, interview_id)
    if (
        interview is None
        or interview.organization_id != application.organization_id
        or interview.application_id != application.id
    ):
        raise NotFoundError("Interview not found.")
    return interview


async def _audit(
    db: AsyncSession, *, application: Application, actor: User, action: str, description: str,
    reason: str | None = None,
) -> None:
    await activity_service.record_activity(
        db,
        organization_id=application.organization_id,
        actor=actor,
        action=action,
        entity_type="application",
        entity_id=application.id,
        entity_label=f"{application.candidate.full_name} — {application.job.title}",
        description=description,
        reason=reason,
    )


async def schedule(
    db: AsyncSession,
    *,
    application: Application,
    stage: str,
    scheduled_at: datetime,
    duration_minutes: int | None,
    location: str | None,
    interviewer_user_id: uuid.UUID | None,
    actor: User,
) -> ApplicationInterview:
    if application.status not in SCHEDULABLE_STATUSES:
        raise ConflictError(
            "An interview can be scheduled only for a shortlisted candidate.",
            code="interview_not_allowed",
        )
    stage = stage.strip()
    if not stage:
        raise UnprocessableError("Enter the interview stage.")
    if interviewer_user_id is not None:
        interviewer = await db.get(User, interviewer_user_id)
        if (
            interviewer is None
            or interviewer.organization_id != application.organization_id
            or not interviewer.is_active
        ):
            raise UnprocessableError("Choose an active member of your organization as interviewer.")

    if application.status == ApplicationStatus.SHORTLISTED:
        # Through the pipeline, so program gates (passed test, eligibility)
        # are enforced exactly as for a manual status change.
        await application_service.change_status(
            db, application, to_status=ApplicationStatus.INTERVIEW, actor_user_id=actor.id
        )

    interview = ApplicationInterview(
        organization_id=application.organization_id,
        application_id=application.id,
        stage=stage,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        location=_clean(location),
        interviewer_user_id=interviewer_user_id,
        scheduled_by_user_id=actor.id,
    )
    db.add(interview)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        raise ConflictError(
            f'A "{stage}" interview is already scheduled for this candidate.',
            code="interview_already_scheduled",
        ) from exc
    await _audit(
        db, application=application, actor=actor, action="INTERVIEW_SCHEDULED",
        description=f'"{stage}" interview scheduled for {scheduled_at.isoformat()}.',
    )
    if interviewer_user_id is not None and interviewer_user_id != actor.id:
        # In-app only (never email), in the same transaction as the
        # interview, so an assignment and its notice exist together.
        db.add(
            Notification(
                organization_id=application.organization_id,
                recipient_user_id=interviewer_user_id,
                sender_user_id=actor.id,
                type=NotificationType.INTERVIEW_ASSIGNED,
                title=f"Interview assigned: {stage}",
                message=(
                    f"You are the interviewer for {application.candidate.full_name} "
                    f"({application.job.title}) on {scheduled_at.astimezone(UTC).strftime('%b %d, %Y at %H:%M UTC')}."
                ),
                related_entity_type="APPLICATION",
                related_entity_id=application.id,
            )
        )
        await db.flush()
    return interview


async def record_outcome(
    db: AsyncSession,
    *,
    application: Application,
    interview: ApplicationInterview,
    outcome: InterviewOutcome,
    rating: int | None,
    feedback: str | None,
    actor: User,
    can_manage: bool,
) -> ApplicationInterview:
    if not can_manage and interview.interviewer_user_id != actor.id:
        raise ForbiddenError("Only the assigned interviewer can record this interview's outcome.")
    if interview.status != InterviewStatus.SCHEDULED:
        raise ConflictError(
            f"This interview is already {interview.status.value.replace('_', ' ').lower()}.",
            code="interview_closed",
        )
    if outcome != InterviewOutcome.PASS and not _clean(feedback):
        raise UnprocessableError("Give feedback when the outcome isn't a pass.", code="feedback_required")
    interview.status = InterviewStatus.COMPLETED
    interview.outcome = outcome
    interview.rating = rating
    interview.feedback = _clean(feedback)
    interview.recorded_by_user_id = actor.id
    interview.recorded_at = _now()
    await db.flush()
    await _audit(
        db, application=application, actor=actor, action="INTERVIEW_OUTCOME_RECORDED",
        # The feedback itself stays on the interview (restricted), not in
        # the organization-wide activity log.
        description=f'"{interview.stage}" interview recorded: {outcome.value.replace("_", " ").lower()}.',
    )
    return interview


async def close_without_outcome(
    db: AsyncSession,
    *,
    application: Application,
    interview: ApplicationInterview,
    status: InterviewStatus,
    reason: str | None,
    actor: User,
) -> ApplicationInterview:
    """CANCELLED (reason required) or NO_SHOW."""
    if status not in (InterviewStatus.CANCELLED, InterviewStatus.NO_SHOW):
        raise UnprocessableError("An interview can only be cancelled or marked as a no-show here.")
    if interview.status != InterviewStatus.SCHEDULED:
        raise ConflictError(
            f"This interview is already {interview.status.value.replace('_', ' ').lower()}.",
            code="interview_closed",
        )
    if status == InterviewStatus.CANCELLED and not _clean(reason):
        raise UnprocessableError("Give a reason for cancelling the interview.", code="reason_required")
    interview.status = status
    interview.cancellation_reason = _clean(reason)
    interview.recorded_by_user_id = actor.id
    interview.recorded_at = _now()
    await db.flush()
    await _audit(
        db, application=application, actor=actor,
        action="INTERVIEW_CANCELLED" if status == InterviewStatus.CANCELLED else "INTERVIEW_NO_SHOW",
        description=f'"{interview.stage}" interview marked {status.value.replace("_", " ").lower()}.',
        reason=interview.cancellation_reason,
    )
    return interview


def completion_problems(interviews: list[ApplicationInterview], required_stages: list[str]) -> list[str]:
    """Why the interview process can't be recorded as complete yet (empty
    list = it can): nothing may still be scheduled, at least one interview
    must have a recorded outcome, and every configured stage needs one."""
    problems: list[str] = []
    if any(i.status == InterviewStatus.SCHEDULED for i in interviews):
        problems.append("Record the outcome of (or cancel) every scheduled interview.")
    completed_stages = {i.stage.strip().lower() for i in interviews if i.status == InterviewStatus.COMPLETED}
    if not completed_stages:
        problems.append("Record at least one completed interview with its outcome.")
    missing = [s for s in required_stages if s.strip().lower() not in completed_stages]
    if missing:
        problems.append("No completed interview yet for: " + ", ".join(missing) + ".")
    return problems
