import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, UnprocessableError
from app.models.job import (
    EMPLOYMENT_TYPE_FIELDS,
    EMPLOYMENT_TYPE_LABELS,
    TYPE_SPECIFIC_FIELDS,
    EmploymentType,
    Job,
    JobStatus,
)
from app.models.program import RecruitmentProgram
from app.models.user import User
from app.schemas.job import JobCreateRequest, JobUpdateRequest
from app.services import activity_service

_COMPENSATION_FIELDS = (
    "compensation_min",
    "compensation_max",
    "compensation_currency",
    "compensation_period",
)

_FIELD_LABELS = {
    "work_mode": "work mode",
    "duration_months": "duration",
    "is_paid": "paid/unpaid",
    "compensation_min": "minimum pay",
    "compensation_max": "maximum pay",
    "compensation_currency": "pay currency",
    "compensation_period": "pay period",
    "hours_per_week": "hours per week",
    "work_schedule": "schedule",
    "min_experience_years": "minimum experience",
    "max_experience_years": "maximum experience",
    "eligibility_criteria": "eligibility",
    "conversion_to_full_time": "full-time conversion",
    "expected_joining_date": "expected joining date",
    "employment_terms": "employment terms",
}


def _apply_employment_rules(job: Job, explicit: set[str]) -> None:
    """Enforces EMPLOYMENT_TYPE_FIELDS on the job as it will be saved.
    `explicit` is what this request set: a field that doesn't apply to the
    job's type is an error if the caller sent a value for it, and is
    cleared if it is only left over from the job's previous type (so
    switching an internship to full-time drops its stipend status instead
    of failing on data the recruiter can no longer see)."""
    allowed = (
        EMPLOYMENT_TYPE_FIELDS[job.employment_type]
        if job.employment_type is not None
        else frozenset()
    )
    type_label = (
        EMPLOYMENT_TYPE_LABELS[job.employment_type].lower()
        if job.employment_type is not None
        else "a recruitment program's job"
    )
    not_applicable = sorted(
        field
        for field in TYPE_SPECIFIC_FIELDS - allowed
        if field in explicit and getattr(job, field) is not None
    )
    if not_applicable:
        raise UnprocessableError(
            f"These details don't apply to {type_label}: "
            + ", ".join(_FIELD_LABELS[f] for f in not_applicable)
            + ".",
            code="employment_field_not_applicable",
            data={"fields": not_applicable},
        )
    for field in TYPE_SPECIFIC_FIELDS - allowed:
        setattr(job, field, None)

    has_pay = any(getattr(job, field) is not None for field in _COMPENSATION_FIELDS)
    if job.employment_type == EmploymentType.INTERNSHIP and job.is_paid is False and has_pay:
        if explicit & set(_COMPENSATION_FIELDS):
            raise UnprocessableError(
                "An unpaid internship can't have a stipend.", code="unpaid_internship_stipend"
            )
        for field in _COMPENSATION_FIELDS:
            setattr(job, field, None)
        has_pay = False
    if has_pay:
        if job.compensation_min is None and job.compensation_max is None:
            raise UnprocessableError(
                "Enter a pay amount, or leave the currency and period empty.",
                code="compensation_incomplete",
            )
        if job.compensation_currency is None or job.compensation_period is None:
            raise UnprocessableError(
                "A pay amount needs a currency and a period (hourly, monthly or annual).",
                code="compensation_incomplete",
            )
        if (
            job.compensation_min is not None
            and job.compensation_max is not None
            and job.compensation_min > job.compensation_max
        ):
            raise UnprocessableError("The minimum pay can't be more than the maximum.")
    if (
        job.min_experience_years is not None
        and job.max_experience_years is not None
        and job.min_experience_years > job.max_experience_years
    ):
        raise UnprocessableError("The minimum experience can't be more than the maximum.")


async def program_job_ids(db: AsyncSession, organization_id: uuid.UUID) -> dict[uuid.UUID, uuid.UUID]:
    """Carrier job id -> its recruitment program id, for this organization."""
    result = await db.execute(
        select(RecruitmentProgram.job_id, RecruitmentProgram.id).where(
            RecruitmentProgram.organization_id == organization_id
        )
    )
    return {job_id: program_id for job_id, program_id in result.all()}


async def create_job(
    db: AsyncSession,
    *,
    actor: User,
    payload: JobCreateRequest,
) -> Job:
    assert actor.organization_id is not None
    job = Job(
        organization_id=actor.organization_id,
        created_by=actor.id,
        title=payload.title,
        department=payload.department,
        location=payload.location,
        employment_type=payload.employment_type,
        description=payload.description,
        description_visible=payload.description_visible,
        openings_count=payload.openings_count,
    )
    for field in TYPE_SPECIFIC_FIELDS:
        setattr(job, field, getattr(payload, field))
    _apply_employment_rules(job, payload.model_fields_set)
    db.add(job)
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=job.organization_id,
        actor=actor,
        action="JOB_CREATED",
        entity_type="job",
        entity_id=job.id,
        entity_label=job.title,
        description=f"Job \"{job.title}\" was created.",
    )
    return job


async def list_jobs(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    status: JobStatus | None = None,
    employment_type: EmploymentType | None = None,
) -> list[Job]:
    query = (
        select(Job)
        .where(Job.organization_id == organization_id, Job.deleted_at.is_(None))
        .order_by(Job.created_at.desc())
    )
    if status is not None:
        query = query.where(Job.status == status)
    if employment_type is not None:
        query = query.where(Job.employment_type == employment_type)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """RLS scopes this to the caller's own tenant — a cross-tenant id
    returns None exactly as if the row didn't exist (docs/security.md § 2)."""
    return await db.get(Job, job_id)


def _job_change_action(job: Job, updates: dict[str, Any]) -> tuple[str, str]:
    """Picks the most specific activity action/description for a job
    update — a plain field edit is JOB_UPDATED, but a status change gets
    its own more legible action name (QA § 5)."""
    new_status = updates.get("status")
    if new_status is not None and new_status != job.status:
        if new_status == JobStatus.CLOSED:
            return "JOB_CLOSED", f"Job \"{job.title}\" was closed."
        if new_status == JobStatus.OPEN and job.status in (JobStatus.CLOSED, JobStatus.ON_HOLD):
            return "JOB_REOPENED", f"Job \"{job.title}\" was reopened."
        return (
            "JOB_STATUS_CHANGED",
            f"Job \"{job.title}\" status changed from {job.status.value} to {new_status.value}.",
        )

    changed_fields = [field for field in updates if field != "status"]
    if changed_fields:
        details = ", ".join(sorted(changed_fields))
        return "JOB_UPDATED", f"Job \"{job.title}\" was updated ({details})."
    return "JOB_UPDATED", f"Job \"{job.title}\" was updated."


async def update_job(
    db: AsyncSession, job: Job, payload: JobUpdateRequest, *, actor: User
) -> Job:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return job

    if job.employment_type is None and updates.get("employment_type") is not None:
        # Only a program's carrier job has no type (job_service never
        # creates one without it) — the program is its own workflow.
        raise ConflictError(
            "This job belongs to a recruitment program; it has no employment type. "
            "Edit the program instead.",
            code="program_job_employment_type",
        )

    action, description = _job_change_action(job, updates)
    for field, value in updates.items():
        setattr(job, field, value)
    _apply_employment_rules(job, set(updates))
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=job.organization_id,
        actor=actor,
        action=action,
        entity_type="job",
        entity_id=job.id,
        entity_label=job.title,
        description=description,
    )
    return job


async def delete_job(db: AsyncSession, job: Job, *, actor: User, reason: str) -> Job:
    """Soft-deletes/archives: keeps the row (and every Application/
    CampusDrive pointing at it) but removes it from `list_jobs` and records
    an Activity (CLAUDE.md § 3: never silently destroy recruitment history)."""
    if job.deleted_at is not None:
        raise ConflictError("This job has already been deleted.")

    job.deleted_at = datetime.now(UTC)
    job.deleted_by_user_id = actor.id
    job.deletion_reason = reason
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=job.organization_id,
        actor=actor,
        action="JOB_DELETED",
        entity_type="job",
        entity_id=job.id,
        entity_label=job.title,
        description=f"Job \"{job.title}\" was deleted.",
        reason=reason,
    )
    return job
