"""Program internships (docs/programs.md § 5f).

An internship is its own record (`ProgramInternship`), related to — but never
merged with — the participant's training and their full-time employment.
Its duration and stipend are snapshotted from the program when it is
planned, so a later program edit never changes an internship already
agreed; every change appends a `ProgramInternshipEvent`.

Completing an internship records an evaluation (QUALIFIED / NOT_QUALIFIED).
That outcome is never an employment decision: at most, a program that
requires it gates the start of the employment review on a qualifying
completion.
"""

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.models.program import (
    MAX_DURATION_MONTHS,
    InternshipMode,
    InternshipOutcome,
    InternshipStatus,
    ProgramEnrollment,
    ProgramInternship,
    ProgramInternshipEvent,
    RecruitmentProgram,
)
from app.models.team_hierarchy import Employee
from app.models.user import User
from app.services import program_service, reapply_service
from app.services.program_finance import money
from app.workflows import program_workflow
from app.workflows.program_workflow import require_transition

_ENDED_EARLY = frozenset({InternshipStatus.TERMINATED, InternshipStatus.WITHDRAWN})


def _end_from(start: date, months: int) -> date:
    return reapply_service.add_months(datetime.combine(start, time(), tzinfo=UTC), months).date()


def _label(status: InternshipStatus) -> str:
    return status.value.replace("_", " ").lower()


async def get_internship(db: AsyncSession, enrollment: ProgramEnrollment) -> ProgramInternship | None:
    internship: ProgramInternship | None = await db.scalar(
        select(ProgramInternship).where(
            ProgramInternship.enrollment_id == enrollment.id,
            ProgramInternship.organization_id == enrollment.organization_id,
        )
    )
    return internship


async def _require_internship(db: AsyncSession, enrollment: ProgramEnrollment) -> ProgramInternship:
    internship = await get_internship(db, enrollment)
    if internship is None:
        raise NotFoundError("No internship has been planned for this participant.")
    return internship


async def _event(
    db: AsyncSession,
    internship: ProgramInternship,
    *,
    event: str,
    from_status: InternshipStatus | None,
    note: str | None,
    actor: User,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        ProgramInternshipEvent(
            organization_id=internship.organization_id,
            internship_id=internship.id,
            event=event,
            from_status=from_status.value if from_status else None,
            to_status=internship.status.value,
            note=note,
            details=details or {},
            actor_user_id=actor.id,
        )
    )
    await db.flush()


async def _validated_supervisor(
    db: AsyncSession, organization_id: Any, employee_id: Any
) -> Employee:
    employee = await db.get(Employee, employee_id)
    if employee is None or employee.organization_id != organization_id or employee.deleted_at is not None:
        raise NotFoundError("Supervisor not found in the team directory.")
    return employee


async def plan_internship(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    enrollment: ProgramEnrollment,
    duration_months: int | None,
    start_date: date | None,
    mode: InternshipMode | None,
    role_title: str | None,
    supervisor_employee_id: Any,
    stipend_amount: Decimal | None,
    stipend_currency: str | None,
    can_set_stipend: bool,
    actor: User,
) -> ProgramInternship:
    """Creates the internship (PLANNED), or edits one that hasn't started.
    Duration and stipend default to the program's terms; only a finance
    user may record a different stipend."""
    if not program.tracks_internship:
        raise ConflictError("This program doesn't track internships.", code="internship_not_tracked")
    application = await program_service._application(db, enrollment)
    if application.status not in program_workflow.SELECTED_STATUSES:
        raise ConflictError(
            "Only a selected candidate can be placed on an internship.", code="selection_required"
        )
    stipend_given = stipend_amount is not None or stipend_currency is not None
    if stipend_given and not can_set_stipend:
        raise ForbiddenError("Only finance users can change an internship stipend.")
    if stipend_amount is not None and not stipend_currency:
        raise UnprocessableError("Enter the currency of the stipend.")
    if supervisor_employee_id is not None:
        await _validated_supervisor(db, enrollment.organization_id, supervisor_employee_id)

    internship = await get_internship(db, enrollment)
    created = internship is None
    if internship is None:
        duration = duration_months or program.internship_duration_months
        if duration is None:
            raise UnprocessableError("Enter the internship duration.")
        internship = ProgramInternship(
            organization_id=enrollment.organization_id,
            enrollment_id=enrollment.id,
            status=InternshipStatus.PLANNED,
            duration_months=duration,
            stipend_amount=(
                money(program.internship_stipend_amount)
                if program.internship_stipend_amount is not None
                else None
            ),
            stipend_currency=program.internship_stipend_currency,
            created_by_user_id=actor.id,
        )
        db.add(internship)
    elif internship.status != InternshipStatus.PLANNED:
        raise ConflictError(
            "The internship has started; use an extension or a status change instead.",
            code="internship_started",
        )
    elif duration_months is not None:
        internship.duration_months = duration_months

    if start_date is not None:
        internship.start_date = start_date
    if mode is not None:
        internship.mode = mode
    if role_title is not None:
        internship.role_title = role_title.strip() or None
    if supervisor_employee_id is not None:
        internship.supervisor_employee_id = supervisor_employee_id
    if stipend_given:
        internship.stipend_amount = money(stipend_amount) if stipend_amount is not None else None
        internship.stipend_currency = stipend_currency if stipend_amount is not None else None
    if internship.start_date is not None:
        internship.end_date = _end_from(internship.start_date, internship.duration_months)
    await db.flush()

    await _event(
        db, internship, event="PLANNED" if created else "UPDATED",
        from_status=None if created else InternshipStatus.PLANNED, note=None, actor=actor,
        details={
            "duration_months": internship.duration_months,
            "start_date": internship.start_date.isoformat() if internship.start_date else None,
            "end_date": internship.end_date.isoformat() if internship.end_date else None,
        },
    )
    await program_service._audit(
        db, enrollment=enrollment, actor=actor,
        action="PROGRAM_INTERNSHIP_PLANNED" if created else "PROGRAM_INTERNSHIP_UPDATED",
        description=(
            f"Internship {'planned' if created else 'updated'}: {internship.duration_months} months"
            + (f" from {internship.start_date.isoformat()}." if internship.start_date else ".")
        ),
    )
    return internship


async def change_status(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    status: InternshipStatus,
    on_date: date | None,
    outcome: InternshipOutcome | None,
    rating: int | None,
    note: str | None,
    actor: User,
) -> ProgramInternship:
    """ACTIVE needs a start date; COMPLETED needs the completion date and the
    evaluation outcome; TERMINATED / WITHDRAWN need a reason."""
    internship = await _require_internship(db, enrollment)
    require_transition(
        program_workflow.INTERNSHIP_TRANSITIONS, internship.status, status, what="internship"
    )
    clean_note = note.strip() if note and note.strip() else None
    previous = internship.status

    if status == InternshipStatus.ACTIVE:
        start = on_date or internship.start_date
        if start is None:
            raise UnprocessableError("Enter the date the internship started.")
        internship.start_date = start
        internship.end_date = _end_from(start, internship.duration_months)
        internship.original_end_date = internship.end_date
    elif status == InternshipStatus.COMPLETED:
        if on_date is None:
            raise UnprocessableError("Enter the date the internship was completed.")
        if outcome is None:
            raise UnprocessableError("Record the completion evaluation (qualified or not).")
        if internship.start_date and on_date < internship.start_date:
            raise UnprocessableError("An internship can't be completed before it starts.")
        internship.completed_on = on_date
        internship.outcome = outcome
        internship.evaluation_rating = rating
        internship.evaluation_note = clean_note
    else:
        if clean_note is None:
            raise UnprocessableError("Give the reason the internship ended.", code="note_required")
        internship.ended_on = on_date or date.today()
        internship.end_reason = clean_note
    internship.status = status
    await db.flush()

    await _event(
        db, internship, event=status.value, from_status=previous, note=clean_note, actor=actor,
        details={
            "on_date": on_date.isoformat() if on_date else None,
            "outcome": outcome.value if outcome else None,
            "rating": rating,
        },
    )
    description = f"Internship {_label(previous)} -> {_label(status)}"
    if status == InternshipStatus.COMPLETED and outcome is not None:
        description += f" (evaluation: {outcome.value.replace('_', ' ').lower()})"
    await program_service._audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_INTERNSHIP_STATUS_CHANGED",
        description=description + ".",
        reason=clean_note if status in _ENDED_EARLY else None,
    )
    return internship


async def extend(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    new_end_date: date,
    reason: str,
    actor: User,
) -> ProgramInternship:
    """Moves an active internship's planned end later, with a reason; the
    original end stays on record."""
    internship = await _require_internship(db, enrollment)
    if internship.status != InternshipStatus.ACTIVE:
        raise ConflictError("Only an active internship can be extended.", code="internship_not_active")
    if not reason.strip():
        raise UnprocessableError("Give the reason for the extension.", code="note_required")
    if internship.end_date is not None and new_end_date <= internship.end_date:
        raise UnprocessableError("The new end date must be after the current end date.")
    assert internship.start_date is not None
    if _end_from(internship.start_date, MAX_DURATION_MONTHS) < new_end_date:
        raise UnprocessableError(
            f"An internship can't run longer than {MAX_DURATION_MONTHS} months in total."
        )
    previous_end = internship.end_date
    internship.end_date = new_end_date
    internship.extension_count += 1
    await db.flush()
    await _event(
        db, internship, event="EXTENDED", from_status=InternshipStatus.ACTIVE, note=reason.strip(),
        actor=actor,
        details={
            "previous_end_date": previous_end.isoformat() if previous_end else None,
            "new_end_date": new_end_date.isoformat(),
        },
    )
    await program_service._audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_INTERNSHIP_EXTENDED",
        description=(
            f"Internship extended from {previous_end.isoformat() if previous_end else 'unset'} "
            f"to {new_end_date.isoformat()}."
        ),
        reason=reason.strip(),
    )
    return internship
