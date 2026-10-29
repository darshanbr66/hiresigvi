"""Campus affiliation verification (docs/campus-hiring.md § 3a): is this
campus-drive applicant really a member of the drive's institution?

Opening a drive's link proves nothing on its own, so a drive that requires
verification gets one `CampusAffiliationVerification` per application,
starting PENDING. It becomes VERIFIED automatically only on positive
evidence the platform itself checked — the candidate's *verified* email is
on an institution email domain (EMAIL_DOMAIN), or is on the college's
student list for the drive (STUDENT_LIST) — and otherwise waits for an HR
user's manual decision (VERIFIED or REJECTED, with a note). Every decision
records method, reviewer and time, and is written to the activity log.

Recruitment programs use the same record (`program_id` instead of
`campus_drive_id`, docs/programs.md § 3a): the institution is the one the
applicant chose from the registry (or NULL when they typed a name — then
only a manual decision is possible), EMAIL_DOMAIN can verify automatically,
and an HR decision names the evidence checked (student ID / document,
placement-coordinator confirmation, or another manual check).

Nobody can decide their own verification: a reviewer whose account email
is the candidate's email is refused.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.models.application import Application
from app.models.campus_drive import CampusDrive
from app.models.candidate import Candidate
from app.models.institution import (
    AffiliationStatus,
    AffiliationVerificationMethod,
    CampusAffiliationVerification,
    CampusDriveRosterEntry,
    Institution,
    MANUAL_VERIFICATION_METHODS,
)
from app.models.user import User
from app.services import activity_service, email_verification_service

_REVIEW_ACTIONS = {
    AffiliationStatus.VERIFIED: "CAMPUS_AFFILIATION_VERIFIED",
    AffiliationStatus.REJECTED: "CAMPUS_AFFILIATION_REJECTED",
    AffiliationStatus.PENDING: "CAMPUS_AFFILIATION_RESET",
}


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower()


def domain_matches(email: str, institution_domains: list[str]) -> bool:
    """Exact domain or a subdomain of one (`cse.college.edu` matches
    `college.edu`) — never a suffix that merely ends the same way
    (`notcollege.edu` does not)."""
    domain = email_domain(email)
    return any(domain == d or domain.endswith(f".{d}") for d in institution_domains)


async def open_verification(
    db: AsyncSession, *, drive: CampusDrive, application: Application, candidate: Candidate
) -> CampusAffiliationVerification | None:
    """Called once for a new campus-drive application. Returns None (and
    records nothing) when the drive doesn't require verification. The
    institution is always the *drive's*, never anything the candidate sent."""
    if drive.affiliation_verification_method == AffiliationVerificationMethod.NONE:
        return None

    record = CampusAffiliationVerification(
        organization_id=drive.organization_id,
        application_id=application.id,
        campus_drive_id=drive.id,
        institution_id=drive.institution_id,
        status=AffiliationStatus.PENDING,
    )
    # Automatic verification only from a *verified* email address.
    if candidate.email_verified_at is not None:
        if drive.affiliation_verification_method == AffiliationVerificationMethod.EMAIL_DOMAIN:
            institution = await db.get(Institution, drive.institution_id)
            if institution is not None and domain_matches(candidate.email, institution.email_domains):
                record.status = AffiliationStatus.VERIFIED
                record.method = AffiliationVerificationMethod.EMAIL_DOMAIN
        elif drive.affiliation_verification_method == AffiliationVerificationMethod.STUDENT_LIST:
            on_list = await db.scalar(
                select(CampusDriveRosterEntry.id).where(
                    CampusDriveRosterEntry.campus_drive_id == drive.id,
                    CampusDriveRosterEntry.email == candidate.email.strip().lower(),
                )
            )
            if on_list is not None:
                record.status = AffiliationStatus.VERIFIED
                record.method = AffiliationVerificationMethod.STUDENT_LIST
    if record.status == AffiliationStatus.VERIFIED:
        record.reviewed_at = datetime.now(UTC)
    db.add(record)
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=drive.organization_id,
        actor=None,
        action=(
            "CAMPUS_AFFILIATION_VERIFIED"
            if record.status == AffiliationStatus.VERIFIED
            else "CAMPUS_AFFILIATION_PENDING"
        ),
        entity_type="application",
        entity_id=application.id,
        entity_label=f"{candidate.full_name} — {drive.name}",
        description=(
            f"{candidate.full_name}'s membership of {drive.college_name} was verified automatically "
            f"({record.method.value.replace('_', ' ').lower()} match)."
            if record.status == AffiliationStatus.VERIFIED and record.method
            else f"{candidate.full_name}'s membership of {drive.college_name} is awaiting verification."
        ),
    )
    return record


async def open_program_verification(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    program_id: uuid.UUID,
    program_title: str,
    method: AffiliationVerificationMethod,
    institution_id: uuid.UUID | None,
    institution_name: str,
    application: Application,
    candidate: Candidate,
) -> CampusAffiliationVerification | None:
    """Called once for a new program application (inside its transaction).
    None when the program doesn't require verification. Automatic only for
    EMAIL_DOMAIN with a *verified* candidate email on one of the registry
    institution's domains — a typed institution name is never verified."""
    if method == AffiliationVerificationMethod.NONE:
        return None
    record = CampusAffiliationVerification(
        organization_id=organization_id,
        application_id=application.id,
        program_id=program_id,
        institution_id=institution_id,
        status=AffiliationStatus.PENDING,
    )
    if (
        method == AffiliationVerificationMethod.EMAIL_DOMAIN
        and institution_id is not None
        and candidate.email_verified_at is not None
    ):
        institution = await db.get(Institution, institution_id)
        if institution is not None and domain_matches(candidate.email, institution.email_domains):
            record.status = AffiliationStatus.VERIFIED
            record.method = AffiliationVerificationMethod.EMAIL_DOMAIN
            record.reviewed_at = datetime.now(UTC)
    db.add(record)
    await db.flush()
    await activity_service.record_activity(
        db,
        organization_id=organization_id,
        actor=None,
        action=(
            "CAMPUS_AFFILIATION_VERIFIED"
            if record.status == AffiliationStatus.VERIFIED
            else "CAMPUS_AFFILIATION_PENDING"
        ),
        entity_type="application",
        entity_id=application.id,
        entity_label=f"{candidate.full_name} — {program_title}",
        description=(
            f"{candidate.full_name}'s membership of {institution_name} was verified automatically "
            "(email domain match)."
            if record.status == AffiliationStatus.VERIFIED
            else f"{candidate.full_name}'s membership of {institution_name} is awaiting verification."
        ),
    )
    return record


def _guard_self_review(actor: User, candidate: Candidate | None) -> None:
    if candidate is not None and actor.email.strip().lower() == candidate.email.strip().lower():
        raise ForbiddenError("You can't decide the verification of your own application.")


async def review_program_verification(
    db: AsyncSession,
    *,
    record: CampusAffiliationVerification,
    status: AffiliationStatus,
    method: AffiliationVerificationMethod | None,
    note: str | None,
    actor: User,
    context_label: str,
) -> CampusAffiliationVerification:
    """An HR decision on a program applicant's affiliation. VERIFIED names
    the evidence checked; REJECTED needs a note; PENDING reopens a decided
    record (e.g. the candidate sent new evidence — resubmission)."""
    application = await db.get(Application, record.application_id)
    candidate = await db.get(Candidate, application.candidate_id) if application else None
    _guard_self_review(actor, candidate)
    if status == AffiliationStatus.REJECTED and not (note and note.strip()):
        raise ConflictError("A note is required when rejecting an affiliation.", code="note_required")
    if status == record.status:
        raise ConflictError(
            f"This affiliation is already {status.value.lower()}.", code="affiliation_unchanged"
        )
    if status == AffiliationStatus.VERIFIED:
        method = method or AffiliationVerificationMethod.MANUAL
        if method not in MANUAL_VERIFICATION_METHODS:
            raise UnprocessableError("Choose the evidence you checked to verify this affiliation.")

    record.status = status
    if status == AffiliationStatus.PENDING:
        record.method = None
    elif status == AffiliationStatus.VERIFIED:
        record.method = method
    else:
        record.method = AffiliationVerificationMethod.MANUAL
    record.reviewed_by_user_id = None if status == AffiliationStatus.PENDING else actor.id
    record.reviewed_at = None if status == AffiliationStatus.PENDING else datetime.now(UTC)
    record.review_note = note.strip() if note and note.strip() else None
    await db.flush()
    evidence = (
        f" ({record.method.value.replace('_', ' ').lower()})"
        if status == AffiliationStatus.VERIFIED and record.method
        else ""
    )
    await activity_service.record_activity(
        db,
        organization_id=record.organization_id,
        actor=actor,
        action=_REVIEW_ACTIONS[status],
        entity_type="application",
        entity_id=record.application_id,
        entity_label=f"{candidate.full_name if candidate else 'Candidate'} — {context_label}",
        description=(
            f"Institution membership marked {status.value.lower()} by {actor.full_name}{evidence}."
        ),
        reason=record.review_note,
    )
    return record


async def get_for_application(
    db: AsyncSession, organization_id: uuid.UUID, application_id: uuid.UUID
) -> CampusAffiliationVerification | None:
    record = await db.scalar(
        select(CampusAffiliationVerification).where(
            CampusAffiliationVerification.organization_id == organization_id,
            CampusAffiliationVerification.application_id == application_id,
        )
    )
    return record


@dataclass(frozen=True)
class VerificationRow:
    verification: CampusAffiliationVerification
    candidate_name: str
    candidate_email: str
    reviewer_name: str | None


async def list_for_drive(
    db: AsyncSession, drive: CampusDrive, *, status: AffiliationStatus | None = None
) -> list[VerificationRow]:
    query = (
        select(CampusAffiliationVerification, Candidate.full_name, Candidate.email, User.full_name)
        .join(Application, Application.id == CampusAffiliationVerification.application_id)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .outerjoin(User, User.id == CampusAffiliationVerification.reviewed_by_user_id)
        .where(
            CampusAffiliationVerification.organization_id == drive.organization_id,
            CampusAffiliationVerification.campus_drive_id == drive.id,
            Application.deleted_at.is_(None),
        )
        .order_by(CampusAffiliationVerification.created_at.desc())
    )
    if status is not None:
        query = query.where(CampusAffiliationVerification.status == status)
    result = await db.execute(query)
    return [
        VerificationRow(verification=v, candidate_name=name, candidate_email=email, reviewer_name=reviewer)
        for v, name, email, reviewer in result.all()
    ]


async def status_counts(db: AsyncSession, drive_id: uuid.UUID) -> dict[AffiliationStatus, int]:
    result = await db.execute(
        select(CampusAffiliationVerification.status, func.count(CampusAffiliationVerification.id))
        .join(Application, Application.id == CampusAffiliationVerification.application_id)
        .where(
            CampusAffiliationVerification.campus_drive_id == drive_id,
            Application.deleted_at.is_(None),
        )
        .group_by(CampusAffiliationVerification.status)
    )
    return {status: count for status, count in result.all()}


async def review(
    db: AsyncSession,
    *,
    drive: CampusDrive,
    verification_id: uuid.UUID,
    status: AffiliationStatus,
    note: str | None,
    actor: User,
) -> CampusAffiliationVerification:
    """A placement officer's / HR user's manual decision. Rejecting needs a
    note, so the reason is on record."""
    record = await db.get(CampusAffiliationVerification, verification_id)
    if (
        record is None
        or record.organization_id != drive.organization_id
        or record.campus_drive_id != drive.id
    ):
        raise NotFoundError("Verification record not found.")
    if status == AffiliationStatus.REJECTED and not (note and note.strip()):
        raise ConflictError("A note is required when rejecting an affiliation.", code="note_required")
    application = await db.get(Application, record.application_id)
    candidate = await db.get(Candidate, application.candidate_id) if application else None
    _guard_self_review(actor, candidate)

    record.status = status
    record.method = None if status == AffiliationStatus.PENDING else AffiliationVerificationMethod.MANUAL
    record.reviewed_by_user_id = None if status == AffiliationStatus.PENDING else actor.id
    record.reviewed_at = None if status == AffiliationStatus.PENDING else datetime.now(UTC)
    record.review_note = note.strip() if note and note.strip() else None
    await db.flush()
    await activity_service.record_activity(
        db,
        organization_id=drive.organization_id,
        actor=actor,
        action=_REVIEW_ACTIONS[status],
        entity_type="application",
        entity_id=record.application_id,
        entity_label=f"{candidate.full_name if candidate else 'Candidate'} — {drive.name}",
        description=(
            f"Membership of {drive.college_name} marked {status.value.lower()} by {actor.full_name}."
        ),
        reason=record.review_note,
    )
    return record


@dataclass(frozen=True)
class RosterEntryInput:
    email: str
    student_identifier: str | None = None
    full_name: str | None = None


async def add_roster_entries(
    db: AsyncSession, *, drive: CampusDrive, entries: list[RosterEntryInput], actor: User
) -> int:
    """Adds the college-provided student list (duplicates within the drive
    are ignored). Returns how many new entries were stored. Pending
    STUDENT_LIST verifications whose verified email is now on the list are
    verified automatically."""
    rows = []
    seen: set[str] = set()
    for entry in entries:
        email = email_verification_service.normalize_email(entry.email)
        if email in seen:
            continue
        seen.add(email)
        rows.append(
            {
                "organization_id": drive.organization_id,
                "campus_drive_id": drive.id,
                "email": email,
                "student_identifier": (entry.student_identifier or "").strip() or None,
                "full_name": (entry.full_name or "").strip() or None,
                "added_by_user_id": actor.id,
            }
        )
    if not rows:
        return 0
    result = await db.execute(
        insert(CampusDriveRosterEntry)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["campus_drive_id", "email"])
        .returning(CampusDriveRosterEntry.id)
    )
    added = len(result.all())

    if drive.affiliation_verification_method == AffiliationVerificationMethod.STUDENT_LIST:
        pending = await db.execute(
            select(CampusAffiliationVerification, Candidate)
            .join(Application, Application.id == CampusAffiliationVerification.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .where(
                CampusAffiliationVerification.campus_drive_id == drive.id,
                CampusAffiliationVerification.status == AffiliationStatus.PENDING,
                Candidate.email_verified_at.is_not(None),
                Candidate.email.in_(seen),
            )
        )
        for record, _candidate in pending.all():
            record.status = AffiliationStatus.VERIFIED
            record.method = AffiliationVerificationMethod.STUDENT_LIST
            record.reviewed_at = datetime.now(UTC)
        await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=drive.organization_id,
        actor=actor,
        action="CAMPUS_DRIVE_ROSTER_UPDATED",
        entity_type="campus_drive",
        entity_id=drive.id,
        entity_label=drive.name,
        description=f"{added} student(s) added to the verification list for \"{drive.name}\".",
    )
    return added


async def list_roster(db: AsyncSession, drive: CampusDrive) -> list[CampusDriveRosterEntry]:
    result = await db.execute(
        select(CampusDriveRosterEntry)
        .where(
            CampusDriveRosterEntry.organization_id == drive.organization_id,
            CampusDriveRosterEntry.campus_drive_id == drive.id,
        )
        .order_by(CampusDriveRosterEntry.email)
    )
    return list(result.scalars().all())


async def remove_roster_entry(
    db: AsyncSession, *, drive: CampusDrive, entry_id: uuid.UUID, actor: User
) -> None:
    """Removes a list entry. A verification already granted from it stays as
    recorded (it was true when decided); HR can reset it manually."""
    entry = await db.get(CampusDriveRosterEntry, entry_id)
    if entry is None or entry.campus_drive_id != drive.id:
        raise NotFoundError("Student list entry not found.")
    await db.delete(entry)
    await db.flush()
    await activity_service.record_activity(
        db,
        organization_id=drive.organization_id,
        actor=actor,
        action="CAMPUS_DRIVE_ROSTER_UPDATED",
        entity_type="campus_drive",
        entity_id=drive.id,
        entity_label=drive.name,
        description=f"{entry.email} was removed from the verification list for \"{drive.name}\".",
    )
