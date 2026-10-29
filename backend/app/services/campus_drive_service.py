import uuid
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.core.security import generate_opaque_token, hash_opaque_token
from app.models.application import Application, ApplicationStatus
from app.models.assessment import (
    Assessment,
    AssessmentInvitation,
    AssessmentResult,
    AssessmentStatus,
    ResultGradingStatus,
)
from app.models.campus_drive import CAMPUS_DRIVE_TRANSITIONS, CampusDrive, CampusDriveStatus
from app.models.institution import (
    AffiliationStatus,
    AffiliationVerificationMethod,
    CampusAffiliationVerification,
    Institution,
)
from app.models.user import User
from app.schemas.campus_drive import (
    CampusDriveCreateRequest,
    CampusDriveFunnelCounts,
    CampusDriveReportRow,
    CampusDriveUpdateRequest,
)
from app.schemas.job import JobCreateRequest
from app.services import (
    activity_service,
    campus_affiliation_service,
    institution_service,
    job_service,
)

_STATUS_ACTIONS = {
    CampusDriveStatus.ACTIVE: "CAMPUS_DRIVE_ACTIVATED",
    CampusDriveStatus.PAUSED: "CAMPUS_DRIVE_PAUSED",
    CampusDriveStatus.CLOSED: "CAMPUS_DRIVE_CLOSED",
}


async def _resolve_job_id(
    db: AsyncSession, *, actor: User, payload: CampusDriveCreateRequest
) -> uuid.UUID:
    if payload.job_id is not None:
        job = await job_service.get_job(db, payload.job_id)
        if job is None:
            raise NotFoundError("Job not found.")
        return job.id

    assert payload.new_job_title is not None and payload.new_job_description is not None
    job = await job_service.create_job(
        db,
        actor=actor,
        payload=JobCreateRequest(title=payload.new_job_title, description=payload.new_job_description),
    )
    return job.id


async def _resolve_institution(
    db: AsyncSession, *, actor: User, payload: CampusDriveCreateRequest
) -> Institution:
    assert actor.organization_id is not None
    if payload.institution_id is not None:
        return await institution_service.require_institution(
            db, actor.organization_id, payload.institution_id
        )
    if payload.new_institution is not None:
        return await institution_service.create_institution(
            db, actor=actor, payload=payload.new_institution
        )
    assert payload.college_name is not None
    return await institution_service.find_or_create_by_name(db, actor=actor, name=payload.college_name)


def _check_verification_method(
    method: AffiliationVerificationMethod, institution: Institution
) -> None:
    if method in (
        AffiliationVerificationMethod.STUDENT_ID_DOCUMENT,
        AffiliationVerificationMethod.COORDINATOR_CONFIRMATION,
    ):
        # Evidence labels recorded on a manual decision, not drive settings.
        raise UnprocessableError(
            "Choose not required, email domain, student list or manual review.",
            code="invalid_verification_method",
        )
    if method == AffiliationVerificationMethod.EMAIL_DOMAIN and not institution.email_domains:
        raise ConflictError(
            f'"{institution.name}" has no official email domain on record, so email-domain '
            "verification can't be used. Add its domain to the institution, or choose another "
            "verification method.",
            code="institution_has_no_email_domain",
        )


async def require_assignable_assessment(
    db: AsyncSession, organization_id: uuid.UUID, assessment_id: uuid.UUID
) -> Assessment:
    """A drive's default assessment must be this organization's, live, and
    PUBLISHED — a draft can't be sent to anyone."""
    assessment = await db.get(Assessment, assessment_id)
    if (
        assessment is None
        or assessment.organization_id != organization_id
        or assessment.deleted_at is not None
    ):
        raise NotFoundError("Assessment not found.")
    if assessment.status != AssessmentStatus.PUBLISHED:
        raise ConflictError(
            "Publish this assessment before attaching it.", code="assessment_not_published"
        )
    return assessment


async def create_campus_drive(
    db: AsyncSession, *, actor: User, payload: CampusDriveCreateRequest
) -> tuple[CampusDrive, str]:
    """Returns the drive and its raw public application-link token — shown
    once, like an assessment invitation link (only the hash is persisted)."""
    assert actor.organization_id is not None
    if payload.default_assessment_id is not None:
        await require_assignable_assessment(db, actor.organization_id, payload.default_assessment_id)

    institution = await _resolve_institution(db, actor=actor, payload=payload)
    _check_verification_method(payload.affiliation_verification_method, institution)
    job_id = await _resolve_job_id(db, actor=actor, payload=payload)

    raw_token = generate_opaque_token()
    drive = CampusDrive(
        organization_id=actor.organization_id,
        created_by=actor.id,
        name=payload.name,
        job_id=job_id,
        institution_id=institution.id,
        college_name=institution.name,
        affiliation_verification_method=payload.affiliation_verification_method,
        description=payload.description,
        batch_year=payload.batch_year,
        start_date=payload.start_date,
        end_date=payload.end_date,
        registration_deadline=payload.registration_deadline,
        default_assessment_id=payload.default_assessment_id,
        link_token_hash=hash_opaque_token(raw_token),
    )
    db.add(drive)
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=drive.organization_id,
        actor=actor,
        action="CAMPUS_DRIVE_CREATED",
        entity_type="campus_drive",
        entity_id=drive.id,
        entity_label=drive.name,
        description=f"Campus drive \"{drive.name}\" was created for {institution.name}.",
    )

    reloaded = await get_campus_drive(db, drive.id)
    assert reloaded is not None
    return reloaded, raw_token


_DRIVE_LOADS = (
    joinedload(CampusDrive.job),
    joinedload(CampusDrive.default_assessment),
    joinedload(CampusDrive.institution),
)


async def list_campus_drives(
    db: AsyncSession, organization_id: uuid.UUID, *, institution_id: uuid.UUID | None = None
) -> list[CampusDrive]:
    query = (
        select(CampusDrive)
        .where(CampusDrive.organization_id == organization_id, CampusDrive.deleted_at.is_(None))
        .options(*_DRIVE_LOADS)
        .order_by(CampusDrive.created_at.desc())
    )
    if institution_id is not None:
        query = query.where(CampusDrive.institution_id == institution_id)
    result = await db.execute(query)
    return list(result.unique().scalars().all())


async def get_campus_drive(db: AsyncSession, drive_id: uuid.UUID) -> CampusDrive | None:
    result = await db.execute(
        select(CampusDrive).where(CampusDrive.id == drive_id).options(*_DRIVE_LOADS)
    )
    return result.unique().scalar_one_or_none()


async def update_campus_drive(
    db: AsyncSession, drive: CampusDrive, payload: CampusDriveUpdateRequest, *, actor: User
) -> CampusDrive:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return drive

    activities: list[tuple[str, str]] = []
    from_status = drive.status

    new_status = updates.pop("status", None)
    if new_status is not None and new_status != drive.status:
        allowed = CAMPUS_DRIVE_TRANSITIONS[drive.status]
        if new_status not in allowed:
            raise ConflictError(
                f"Cannot move a campus drive from {drive.status.value} to {new_status.value}."
            )
        drive.status = new_status
        if new_status == CampusDriveStatus.ACTIVE and from_status == CampusDriveStatus.CLOSED:
            activities.append(
                ("CAMPUS_DRIVE_REOPENED", f"Campus drive \"{drive.name}\" was reopened.")
            )
        else:
            action = _STATUS_ACTIONS.get(new_status, "CAMPUS_DRIVE_UPDATED")
            activities.append(
                (action, f"Campus drive \"{drive.name}\" status changed to {new_status.value}.")
            )

    if "default_assessment_id" in updates and updates["default_assessment_id"] is not None:
        await require_assignable_assessment(
            db, drive.organization_id, updates["default_assessment_id"]
        )

    institution: Institution | None = None
    new_institution_id = updates.pop("institution_id", None)
    if new_institution_id is not None and new_institution_id != drive.institution_id:
        has_applications = await db.scalar(
            select(func.count(Application.id)).where(Application.campus_drive_id == drive.id)
        )
        if has_applications:
            raise ConflictError(
                "This drive already has applications, so its institution can't be changed. "
                "Create a new drive for the other institution.",
                code="drive_has_applications",
            )
        institution = await institution_service.require_institution(
            db, drive.organization_id, new_institution_id
        )
        drive.institution_id = institution.id
        drive.college_name = institution.name
        activities.append(
            (
                "CAMPUS_DRIVE_UPDATED",
                f"Campus drive \"{drive.name}\" was moved to institution {institution.name}.",
            )
        )

    method = updates.get("affiliation_verification_method")
    if method is not None:
        if institution is None:
            institution = await db.get(Institution, drive.institution_id)
        assert institution is not None
        _check_verification_method(method, institution)
    elif "affiliation_verification_method" in updates:
        updates.pop("affiliation_verification_method")

    assessment_mode_changed = (
        "default_assessment_id" in updates
        and updates["default_assessment_id"] != drive.default_assessment_id
    )
    if assessment_mode_changed:
        activities.append(
            (
                "CAMPUS_DRIVE_ASSESSMENT_MODE_CHANGED",
                f"Campus drive \"{drive.name}\" default assessment was changed.",
            )
        )

    other_fields = [f for f in updates if f not in ("default_assessment_id",)]
    if not activities and other_fields:
        activities.append(("CAMPUS_DRIVE_UPDATED", f"Campus drive \"{drive.name}\" was updated."))

    for field, value in updates.items():
        setattr(drive, field, value)

    await db.flush()

    for action, description in activities:
        await activity_service.record_activity(
            db,
            organization_id=drive.organization_id,
            actor=actor,
            action=action,
            entity_type="campus_drive",
            entity_id=drive.id,
            entity_label=drive.name,
            description=description,
        )

    reloaded = await get_campus_drive(db, drive.id)
    assert reloaded is not None
    return reloaded


async def delete_campus_drive(
    db: AsyncSession, drive: CampusDrive, *, actor: User, reason: str
) -> CampusDrive:
    """Soft-deletes/archives: keeps the row (and every Application sourced
    from it via `Application.campus_drive_id`) but removes it from
    `list_campus_drives` and records an Activity."""
    if drive.deleted_at is not None:
        raise ConflictError("This campus drive has already been deleted.")

    drive.deleted_at = datetime.now(UTC)
    drive.deleted_by_user_id = actor.id
    drive.deletion_reason = reason
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=drive.organization_id,
        actor=actor,
        action="CAMPUS_DRIVE_DELETED",
        entity_type="campus_drive",
        entity_id=drive.id,
        entity_label=drive.name,
        description=f"Campus drive \"{drive.name}\" was deleted.",
        reason=reason,
    )
    return drive


async def regenerate_link(db: AsyncSession, drive: CampusDrive, *, actor: User) -> str:
    raw_token = generate_opaque_token()
    drive.link_token_hash = hash_opaque_token(raw_token)
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=drive.organization_id,
        actor=actor,
        action="CAMPUS_DRIVE_LINK_REGENERATED",
        entity_type="campus_drive",
        entity_id=drive.id,
        entity_label=drive.name,
        description=f"Public application link regenerated for \"{drive.name}\".",
    )
    return raw_token


async def application_counts(
    db: AsyncSession, organization_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    result = await db.execute(
        select(Application.campus_drive_id, func.count(Application.id))
        .where(
            Application.organization_id == organization_id,
            Application.campus_drive_id.is_not(None),
            Application.deleted_at.is_(None),
        )
        .group_by(Application.campus_drive_id)
    )
    return {drive_id: count for drive_id, count in result.all()}


_SCREENING_STATUSES = {ApplicationStatus.SCREENING}
_ASSESSMENT_INVITED_STATUSES = {
    ApplicationStatus.ASSESSMENT_INVITED,
    ApplicationStatus.ASSESSMENT_STARTED,
}
_ASSESSMENT_COMPLETED_STATUSES = {ApplicationStatus.ASSESSMENT_COMPLETED}


async def get_funnel_counts(db: AsyncSession, drive_id: uuid.UUID) -> CampusDriveFunnelCounts:
    """Every count is a live GROUP BY over real Application/AssessmentResult
    rows for this drive — never a fabricated/hardcoded metric (CLAUDE.md § 2)."""
    result = await db.execute(
        select(Application.status, func.count(Application.id))
        .where(Application.campus_drive_id == drive_id, Application.deleted_at.is_(None))
        .group_by(Application.status)
    )
    counts = Counter({status: count for status, count in result.all()})

    pass_fail_counts = await _pass_fail_counts(db, [drive_id])
    verification = await campus_affiliation_service.status_counts(db, drive_id)

    return CampusDriveFunnelCounts(
        registered=sum(counts.values()),
        screening=sum(counts[s] for s in _SCREENING_STATUSES),
        assessment_invited=sum(counts[s] for s in _ASSESSMENT_INVITED_STATUSES),
        assessment_completed=sum(counts[s] for s in _ASSESSMENT_COMPLETED_STATUSES),
        assessment_passed=pass_fail_counts[(drive_id, True)],
        assessment_failed=pass_fail_counts[(drive_id, False)],
        shortlisted=counts[ApplicationStatus.SHORTLISTED],
        interview=counts[ApplicationStatus.INTERVIEW],
        selected=counts[ApplicationStatus.SELECTED],
        rejected=counts[ApplicationStatus.REJECTED],
        hired=counts[ApplicationStatus.HIRED],
        verification_pending=verification.get(AffiliationStatus.PENDING, 0),
        verified=verification.get(AffiliationStatus.VERIFIED, 0),
        verification_rejected=verification.get(AffiliationStatus.REJECTED, 0),
    )


async def _pass_fail_counts(
    db: AsyncSession, drive_ids: list[uuid.UUID]
) -> Counter[tuple[uuid.UUID, bool | None]]:
    """Per drive: each application's *latest* attempt result — passed,
    failed, or None while it waits for manual grading. Counting the latest
    attempt only means a retest never double-counts one candidate."""
    if not drive_ids:
        return Counter()
    latest = (
        select(
            AssessmentInvitation.id.label("invitation_id"),
            AssessmentInvitation.application_id,
            func.row_number()
            .over(
                partition_by=AssessmentInvitation.application_id,
                order_by=AssessmentInvitation.attempt_number.desc(),
            )
            .label("rank"),
        )
        .subquery()
    )
    result = await db.execute(
        select(Application.campus_drive_id, AssessmentResult.passed, func.count(AssessmentResult.id))
        .join(latest, latest.c.invitation_id == AssessmentResult.invitation_id)
        .join(Application, Application.id == latest.c.application_id)
        .where(
            latest.c.rank == 1,
            Application.campus_drive_id.in_(drive_ids),
            Application.deleted_at.is_(None),
        )
        .group_by(Application.campus_drive_id, AssessmentResult.passed)
    )
    return Counter({(drive_id, passed): count for drive_id, passed, count in result.all()})


async def campus_report(
    db: AsyncSession, organization_id: uuid.UUID, *, institution_id: uuid.UUID | None = None
) -> list[CampusDriveReportRow]:
    """One row per (non-deleted) drive, optionally for one institution —
    applications, verification, assessment, interview and outcome counts,
    all computed from live rows (CLAUDE.md § 2: "Reports != hardcoded")."""
    drives = await list_campus_drives(db, organization_id, institution_id=institution_id)
    drive_ids = [drive.id for drive in drives]
    if not drive_ids:
        return []

    status_rows = await db.execute(
        select(Application.campus_drive_id, Application.status, func.count(Application.id))
        .where(
            Application.organization_id == organization_id,
            Application.campus_drive_id.in_(drive_ids),
            Application.deleted_at.is_(None),
        )
        .group_by(Application.campus_drive_id, Application.status)
    )
    by_status: Counter[tuple[uuid.UUID, ApplicationStatus]] = Counter(
        {(drive_id, status): count for drive_id, status, count in status_rows.all()}
    )
    verification_rows = await db.execute(
        select(
            CampusAffiliationVerification.campus_drive_id,
            CampusAffiliationVerification.status,
            func.count(CampusAffiliationVerification.id),
        )
        .join(Application, Application.id == CampusAffiliationVerification.application_id)
        .where(
            CampusAffiliationVerification.campus_drive_id.in_(drive_ids),
            Application.deleted_at.is_(None),
        )
        .group_by(CampusAffiliationVerification.campus_drive_id, CampusAffiliationVerification.status)
    )
    by_verification: Counter[tuple[uuid.UUID, AffiliationStatus]] = Counter(
        {(drive_id, status): count for drive_id, status, count in verification_rows.all()}
    )
    results = await _pass_fail_counts(db, drive_ids)
    pending_review_rows = await db.execute(
        select(Application.campus_drive_id, func.count(AssessmentResult.id))
        .join(AssessmentInvitation, AssessmentInvitation.id == AssessmentResult.invitation_id)
        .join(Application, Application.id == AssessmentInvitation.application_id)
        .where(
            Application.campus_drive_id.in_(drive_ids),
            Application.deleted_at.is_(None),
            AssessmentResult.grading_status == ResultGradingStatus.PENDING_MANUAL_REVIEW,
        )
        .group_by(Application.campus_drive_id)
    )
    pending_review: dict[uuid.UUID | None, int] = dict(pending_review_rows.tuples().all())

    rows: list[CampusDriveReportRow] = []
    for drive in drives:
        def count(*statuses: ApplicationStatus, _id: uuid.UUID = drive.id) -> int:
            return sum(by_status[(_id, s)] for s in statuses)

        rows.append(
            CampusDriveReportRow(
                drive_id=drive.id,
                drive_name=drive.name,
                drive_status=drive.status,
                institution_id=drive.institution_id,
                institution_name=drive.institution.name,
                job_title=drive.job.title,
                affiliation_verification_method=drive.affiliation_verification_method,
                applications=sum(v for (d, _), v in by_status.items() if d == drive.id),
                verification_pending=by_verification[(drive.id, AffiliationStatus.PENDING)],
                verified=by_verification[(drive.id, AffiliationStatus.VERIFIED)],
                verification_rejected=by_verification[(drive.id, AffiliationStatus.REJECTED)],
                assessment_invited=count(*_ASSESSMENT_INVITED_STATUSES),
                assessment_completed=count(*_ASSESSMENT_COMPLETED_STATUSES),
                assessment_passed=results[(drive.id, True)],
                assessment_failed=results[(drive.id, False)],
                assessment_pending_review=pending_review.get(drive.id, 0),
                interview=count(ApplicationStatus.INTERVIEW),
                selected=count(ApplicationStatus.SELECTED),
                hired=count(ApplicationStatus.HIRED),
                rejected=count(ApplicationStatus.REJECTED),
            )
        )
    return rows
