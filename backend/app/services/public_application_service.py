"""Orchestrates the candidate-facing (self-service) apply flow — the one
place that composes email verification + candidate_service +
application_service + resume_service + screening for an anonymous caller.
Both self-service entry points use it, so they follow exactly the same
identity rules: the careers site (app/api/v1/public/applications.py) and a
campus drive's public link (app/services/public_campus_drive_service.py,
which adds the drive's default-assessment fast-track on top).

Flow (first HR meeting requirements, docs/recruitment-workflow.md § 6):

    early check as the email / mobile number is typed (`has_already_applied`
    — a yes/no only; an existing application with no reapply permission
    stops the candidate here, and no one-time code is sent to them either)
    -> verified email (one-time code; a returning candidate who already applied
    for this job, or may not reapply yet, is stopped right here, and one who
    may apply gets their stored profile back to prefill the form) ->
    precheck of the identity rules before the resume step (optional for
    the client, repeated at submission) -> submission with the verified
    token -> identity check (email/mobile: one profile per person; a
    returning candidate reuses their profile, one application per job, at
    most one self-service application per reapply window — app/services/
    reapply_service.py) -> Candidate + Application + resume saved and
    COMMITTED -> AI screening against the job's JD ->
    NOT_MATCH: AI_SCREENED_OUT (retained, visible to HR, overridable)
    MATCH / AI unavailable: stays APPLIED for recruiter review
    -> automatic welcome email, for both outcomes (the screened-out version
    tells the candidate they are not eligible for this particular role).

The application is committed *before* the AI call and before any email:
neither a slow/failed model nor a failed email can lose it.
"""

import uuid
from dataclasses import dataclass
from typing import NoReturn

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.core.logging import get_logger
from app.core.phone import InvalidPhoneNumberError, normalize_phone
from app.db.rls import set_tenant_context
from app.email_templates.system import render_welcome_email
from app.integrations.email import EmailError
from app.integrations.storage import ResumeStorage
from app.models.application import Application, ApplicationSource, ApplicationStatus
from app.models.campus_drive import CampusDrive, CampusDriveStatus
from app.models.candidate import Candidate, CandidateSource
from app.models.email_verification import EmailVerification
from app.models.job import Job, JobStatus
from app.models.organization import Organization
from app.models.screening import ScreeningDecision, ScreeningRun, ScreeningStatus
from app.schemas.candidate import PublicApplicantProfile
from app.schemas.public import PublicApplicationOutcome
from app.services import (
    activity_service,
    application_service,
    candidate_document_service,
    candidate_service,
    email_verification_service,
    notification_service,
    reapply_service,
    resume_service,
    screening_service,
)
from app.services.candidate_document_service import MarksCardUpload, StoredFileRef
from app.services.email_verification_service import IssuedToken

logger = get_logger(__name__)

AI_SCREENED_OUT_REASON = (
    "Automatic AI screening: the resume did not sufficiently match this job's requirements. "
    "Advisory only — HR can override."
)


def already_registered_message(contact_email: str | None) -> str:
    """Deliberately says nothing about *which* detail matched or what the
    existing profile contains — safe to show an unauthenticated caller."""
    contact = (
        f"please contact our recruitment team at {contact_email}"
        if contact_email
        else "please contact our recruitment team"
    )
    return (
        "An application has already been submitted with the details provided. Our "
        "recruitment team considers your profile for other suitable roles. If you need to "
        f"update your information, {contact}."
    )


def phone_already_registered_message(contact_email: str | None) -> str:
    """Says only that the mobile number is taken — never by whom, nor
    anything else about that profile. Reaches only a caller who has just
    verified a (different) email address, and the verification token is
    burned with it, so it can't be replayed to probe numbers."""
    contact = (
        f"contact our recruitment team at {contact_email}"
        if contact_email
        else "contact our recruitment team"
    )
    return (
        "This mobile number is already registered with another candidate profile, so it can't "
        "be used for this application. Please enter your own mobile number, or "
        f"{contact} if you believe this is a mistake."
    )


def already_applied_to_job_error(job: Job, contact_email: str | None) -> ConflictError:
    """The verified owner of a profile that already has an application for
    this job (one per candidate per job: uq_applications_candidate_job).
    Applying for a *different* job is unaffected. The only way to apply for
    the same job again is HR granting reapply permission, which clears the
    earlier self-service application (reapply_service.grant_reapply_permission)."""
    contact = (
        f"please contact our recruitment team at {contact_email}"
        if contact_email
        else "please contact our recruitment team"
    )
    return ConflictError(
        f'You have already applied for the "{job.title}" position, so a new application for '
        "it can't be submitted. A repeat application for the same role is only possible if our "
        f"recruitment team grants permission to reapply — {contact}. You're welcome to apply "
        "for our other open roles.",
        code="already_applied_to_job",
        data={"job_title": job.title},
    )


#: The only thing the early check (and a refused code request) tells a caller
#: who hasn't verified an email yet.
ALREADY_APPLIED_MESSAGE = "You have already applied."


def already_applied_error() -> ConflictError:
    return ConflictError(ALREADY_APPLIED_MESSAGE, code="already_applied")


@dataclass(frozen=True)
class VerifiedApplicant:
    """`existing_candidate` is the returning candidate's own profile (for
    prefilling the form) — set only once the code has been verified, and
    only when they may go on to apply."""

    issued: IssuedToken
    existing_candidate: Candidate | None


@dataclass(frozen=True)
class SubmissionResult:
    application: Application
    outcome: PublicApplicationOutcome
    confirmation_email_sent: bool


async def _already_applied_to(
    db: AsyncSession, *, organization_id: uuid.UUID, candidate_id: uuid.UUID, job_id: uuid.UUID
) -> bool:
    """Any application for this job, archived included — the unique
    (candidate, job) index covers those too."""
    existing = await db.scalar(
        select(Application.id).where(
            Application.organization_id == organization_id,
            Application.candidate_id == candidate_id,
            Application.job_id == job_id,
        )
    )
    return existing is not None


async def _blocks_new_application(
    db: AsyncSession, *, organization: Organization, candidate: Candidate, job: Job | None
) -> bool:
    """Whether this existing profile may not start a new self-service
    application: a removed profile; an application for this very job already
    (one per job, uq_applications_candidate_job); or an earlier application
    with no reapply permission — the reapply window not yet passed and no HR
    grant (reapply_service.check_eligibility, unchanged). Whatever job the
    earlier application was for."""
    if candidate.deleted_at is not None:
        return True
    if job is not None and await _already_applied_to(
        db, organization_id=organization.id, candidate_id=candidate.id, job_id=job.id
    ):
        return True
    eligibility = await reapply_service.check_eligibility(
        db, organization_id=organization.id, candidate_id=candidate.id
    )
    return not eligibility.allowed


async def has_already_applied(
    db: AsyncSession,
    *,
    organization: Organization,
    email: str | None,
    phone: str | None,
    job_id: uuid.UUID | None,
    campus_drive: CampusDrive | None = None,
) -> bool:
    """The apply form's early check, before any one-time code: does the
    profile this email — or this mobile number — belongs to already block a
    new application (`_blocks_new_application`)? Only ever answered with a
    yes/no: nothing about the profile is revealed to a caller who hasn't
    proven they own the address. The OTP request, verification, precheck
    and submission each enforce the same rules on their own."""
    job = (
        await _load_job(db, job_id, organization_id=organization.id, campus_drive=campus_drive)
        if job_id is not None
        else None
    )
    normalized_phone = None
    if phone is not None:
        try:
            normalized_phone = normalize_phone(phone)
        except InvalidPhoneNumberError as exc:
            raise UnprocessableError(str(exc), code="invalid_phone") from exc
    by_email, by_phone = await candidate_service.find_identity_matches(
        db,
        organization_id=organization.id,
        email=email_verification_service.normalize_email(email) if email else None,
        phone=normalized_phone,
    )
    for candidate in {c.id: c for c in (by_email, by_phone) if c is not None}.values():
        if await _blocks_new_application(
            db, organization=organization, candidate=candidate, job=job
        ):
            return True
    return False


async def request_applicant_code(
    db: AsyncSession,
    *,
    organization: Organization,
    email: str,
    job_id: uuid.UUID | None,
    campus_drive: CampusDrive | None = None,
) -> email_verification_service.CodeSent:
    """Sends the one-time code — unless this email's profile already blocks
    a new application, in which case no code is sent and the caller gets the
    same minimal "You have already applied." as the early check: an applicant
    who is blocked never reaches the code step, even with the form bypassed."""
    if await has_already_applied(
        db,
        organization=organization,
        email=email,
        phone=None,
        job_id=job_id,
        campus_drive=campus_drive,
    ):
        raise already_applied_error()
    return await email_verification_service.request_code(
        db, organization=organization, email=email
    )


async def verify_applicant_email(
    db: AsyncSession,
    *,
    organization: Organization,
    email: str,
    code: str,
    job_id: uuid.UUID | None = None,
    campus_drive: CampusDrive | None = None,
) -> VerifiedApplicant:
    """Verifies the code, then stops a returning candidate who can't apply
    from continuing into a form they can't submit: one who already applied
    for this job (`job_id`, when the client says which job it is), or who
    may not self-apply again yet. Telling them so — and handing back their
    stored profile to prefill the form when they *may* apply — is safe only
    because the caller has just proven they own the address; nothing about a
    candidate is revealed before the code is verified. A returning candidate
    who may apply (window passed, HR grant, or no self-service history — e.g.
    a candidate HR created) continues normally, for any job they haven't
    applied to.

    The submission repeats every one of these checks: this is an early,
    friendlier stop, never the enforcement."""
    job = (
        await _load_job(db, job_id, organization_id=organization.id, campus_drive=campus_drive)
        if job_id is not None
        else None
    )
    issued = await email_verification_service.verify_code(
        db, organization_id=organization.id, email=email, code=code
    )
    existing, _ = await candidate_service.find_identity_matches(
        db, organization_id=organization.id, email=email, phone=None
    )
    if existing is None:
        return VerifiedApplicant(issued=issued, existing_candidate=None)
    if existing.deleted_at is not None:
        raise ConflictError(
            already_registered_message(organization.careers_contact_email),
            code="already_registered",
        )
    if job is not None and await _already_applied_to(
        db, organization_id=organization.id, candidate_id=existing.id, job_id=job.id
    ):
        raise already_applied_to_job_error(job, organization.careers_contact_email)
    eligibility = await reapply_service.check_eligibility(
        db, organization_id=organization.id, candidate_id=existing.id
    )
    if not eligibility.allowed:
        raise reapply_service.reapply_locked_error(eligibility, organization.careers_contact_email)
    return VerifiedApplicant(issued=issued, existing_candidate=existing)


async def precheck_applicant(
    db: AsyncSession,
    *,
    organization: Organization,
    job_id: uuid.UUID,
    email: str,
    verification_token: str,
    phone: str,
    campus_drive: CampusDrive | None = None,
) -> None:
    """Runs a submission's identity rules (`_identify_applicant`) for the
    details entered so far — before the candidate uploads a resume — with
    the same consequences: a mobile number held by another profile, or a
    returning candidate inside the reapply window, burns the token exactly
    as a submission would. Nothing is created; the submission checks it all
    again."""
    job = await _load_job(db, job_id, organization_id=organization.id, campus_drive=campus_drive)
    email = email_verification_service.normalize_email(email)
    verification = await email_verification_service.require_verified_token(
        db, organization_id=organization.id, email=email, token=verification_token
    )
    try:
        normalized_phone = normalize_phone(phone)
    except InvalidPhoneNumberError as exc:
        raise UnprocessableError(str(exc), code="invalid_phone") from exc
    await _identify_applicant(
        db,
        organization=organization,
        email=email,
        phone=normalized_phone,
        job=job,
        verification=verification,
    )


async def _load_job(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    organization_id: uuid.UUID,
    campus_drive: CampusDrive | None,
) -> Job:
    """A careers-site application needs an OPEN job. A campus drive link is
    governed by the drive's own status instead (the caller has already
    checked it is ACTIVE), exactly as before this flow existed — the drive's
    job only has to still exist. Always this organization's job (RLS
    enforces it too)."""
    job = await db.get(Job, job_id)
    if job is None or job.deleted_at is not None or job.organization_id != organization_id:
        raise NotFoundError("This job is not accepting applications.")
    if campus_drive is None and job.status != JobStatus.OPEN:
        raise NotFoundError("This job is not accepting applications.")
    return job


async def _block_duplicate(
    db: AsyncSession,
    *,
    organization: Organization,
    existing: Candidate,
    job: Job,
    verification: EmailVerification,
) -> NoReturn:
    """Records the blocked attempt for HR (on the existing profile — it can
    be a legitimate "please update my details" signal), burns the
    verification token so it can't be replayed to probe other mobile
    numbers, commits both, then refuses."""
    await email_verification_service.consume(db, verification)
    await activity_service.record_activity(
        db,
        organization_id=organization.id,
        actor=None,
        action="DUPLICATE_APPLICATION_BLOCKED",
        entity_type="candidate",
        entity_id=existing.id,
        entity_label=f"{existing.full_name} ({existing.email})",
        description=(
            f'A new self-service application for "{job.title}" was blocked: its email or '
            "mobile number belongs to this existing candidate profile, and the submission "
            "could not be matched to it as the same person."
        ),
    )
    await db.commit()
    raise ConflictError(
        already_registered_message(organization.careers_contact_email),
        code="already_registered",
    )


async def _block_phone_conflict(
    db: AsyncSession,
    *,
    organization: Organization,
    owner: Candidate,
    job: Job,
    verification: EmailVerification,
) -> NoReturn:
    """A mobile number that belongs to a *different* profile: recorded for HR
    on that profile, token burned (so one verified email can't be replayed to
    probe numbers), committed, then refused with a mobile-specific message
    that reveals nothing about the other profile."""
    await email_verification_service.consume(db, verification)
    await activity_service.record_activity(
        db,
        organization_id=organization.id,
        actor=None,
        action="DUPLICATE_APPLICATION_BLOCKED",
        entity_type="candidate",
        entity_id=owner.id,
        entity_label=f"{owner.full_name} ({owner.email})",
        description=(
            f'A self-service application for "{job.title}" from a different email address was '
            "blocked: its mobile number belongs to this candidate profile."
        ),
    )
    await db.commit()
    raise ConflictError(
        phone_already_registered_message(organization.careers_contact_email),
        code="phone_already_registered",
    )


async def _block_reapply(
    db: AsyncSession,
    *,
    organization: Organization,
    existing: Candidate,
    job: Job,
    verification: EmailVerification,
    eligibility: reapply_service.ReapplyEligibility,
) -> NoReturn:
    """The verified owner of an existing profile applying again inside the
    reapply window: recorded for HR (who can grant an early reapply), token
    burned, committed, then refused with the date they may apply again."""
    await email_verification_service.consume(db, verification)
    await activity_service.record_activity(
        db,
        organization_id=organization.id,
        actor=None,
        action="SELF_REAPPLY_BLOCKED",
        entity_type="candidate",
        entity_id=existing.id,
        entity_label=f"{existing.full_name} ({existing.email})",
        description=(
            f'{existing.full_name} tried to apply for "{job.title}" before the reapply window '
            "ended."
        ),
    )
    await db.commit()
    raise reapply_service.reapply_locked_error(eligibility, organization.careers_contact_email)


async def _identify_applicant(
    db: AsyncSession,
    *,
    organization: Organization,
    email: str,
    phone: str,
    job: Job,
    verification: EmailVerification,
) -> Candidate | None:
    """The existing profile this verified submission belongs to (None = a
    new person). The email is the identity key — its owner just proved
    control of it; the same email and mobile on one profile are that same
    person, never a second profile. A mobile number already held by a
    *different* profile, or a soft-deleted profile, is refused, revealing
    nothing about that profile. Then, for a returning candidate: one
    application per job, and the reapply window."""
    by_email, by_phone = await candidate_service.find_identity_matches(
        db, organization_id=organization.id, email=email, phone=phone
    )
    if by_phone is not None and (by_email is None or by_phone.id != by_email.id):
        await _block_phone_conflict(
            db, organization=organization, owner=by_phone, job=job, verification=verification
        )
    if by_email is None:
        return None
    if by_email.deleted_at is not None:
        await _block_duplicate(
            db, organization=organization, existing=by_email, job=job, verification=verification
        )

    # Serializes concurrent submissions for the same person, so two tabs
    # can't both pass the window check.
    await db.refresh(by_email, with_for_update=True)
    # Checked before the reapply window: "you already applied for this job"
    # is the more specific answer. The token stays valid so they can choose
    # another role.
    if await _already_applied_to(
        db, organization_id=organization.id, candidate_id=by_email.id, job_id=job.id
    ):
        raise already_applied_to_job_error(job, organization.careers_contact_email)
    eligibility = await reapply_service.check_eligibility(
        db, organization_id=organization.id, candidate_id=by_email.id
    )
    if not eligibility.allowed:
        await _block_reapply(
            db,
            organization=organization,
            existing=by_email,
            job=job,
            verification=verification,
            eligibility=eligibility,
        )
    return by_email


async def _refresh_returning_profile(
    db: AsyncSession,
    *,
    organization: Organization,
    candidate: Candidate,
    profile: PublicApplicantProfile,
    verification: EmailVerification,
    campus_drive: CampusDrive | None,
) -> None:
    """A returning candidate keeps the same profile; the details they just
    submitted (with a freshly verified email) are their current ones, so
    they replace the stored values. Source, history and every earlier
    application stay untouched."""
    for field, value in profile.model_dump().items():
        setattr(candidate, field, value)
    candidate.email_verified_at = verification.verified_at
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        # The mobile number was taken by another profile concurrently.
        raise ConflictError(
            already_registered_message(organization.careers_contact_email),
            code="already_registered",
        ) from exc

    label = f"{candidate.full_name} ({candidate.email})"
    await activity_service.record_activity(
        db,
        organization_id=organization.id,
        actor=None,
        action="CANDIDATE_REAPPLIED",
        entity_type="candidate",
        entity_id=candidate.id,
        entity_label=label,
        description=(
            f'{candidate.full_name} applied again through the campus drive "{campus_drive.name}"; '
            "their profile details were updated from the new submission."
            if campus_drive
            else f"{candidate.full_name} applied again through the careers site; their profile "
            "details were updated from the new submission."
        ),
    )
    await activity_service.record_activity(
        db,
        organization_id=organization.id,
        actor=None,
        action="CANDIDATE_EMAIL_VERIFIED",
        entity_type="candidate",
        entity_id=candidate.id,
        entity_label=label,
        description=f"{candidate.email} was verified with a one-time code.",
    )


async def _create_new_profile(
    db: AsyncSession,
    *,
    organization: Organization,
    email: str,
    profile: PublicApplicantProfile,
    verification: EmailVerification,
    campus_drive: CampusDrive | None,
) -> Candidate:
    candidate = Candidate(
        organization_id=organization.id,
        email=email,
        source=CandidateSource.CAMPUS_IMPORT if campus_drive else CandidateSource.PORTAL,
        email_verified_at=verification.verified_at,
        **profile.model_dump(),
    )
    db.add(candidate)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        # Lost a race with a concurrent submission for the same person.
        raise ConflictError(
            already_registered_message(organization.careers_contact_email),
            code="already_registered",
        ) from exc

    label = f"{candidate.full_name} ({candidate.email})"
    await activity_service.record_activity(
        db,
        organization_id=organization.id,
        actor=None,
        action="CANDIDATE_REGISTERED",
        entity_type="candidate",
        entity_id=candidate.id,
        entity_label=label,
        description=(
            f"{candidate.full_name} created a profile through the campus drive "
            f'"{campus_drive.name}".'
            if campus_drive
            else f"{candidate.full_name} created a profile through the careers site."
        ),
    )
    await activity_service.record_activity(
        db,
        organization_id=organization.id,
        actor=None,
        action="CANDIDATE_EMAIL_VERIFIED",
        entity_type="candidate",
        entity_id=candidate.id,
        entity_label=label,
        description=f"{candidate.email} was verified with a one-time code.",
    )
    return candidate


async def submit_application(
    db: AsyncSession,
    storage: ResumeStorage,
    *,
    organization: Organization,
    job_id: uuid.UUID,
    email: str,
    verification_token: str | None,
    profile: PublicApplicantProfile,
    resume_filename: str,
    resume_content_type: str,
    resume_bytes: bytes,
    marks_card: MarksCardUpload | None = None,
    campus_drive: CampusDrive | None = None,
) -> SubmissionResult:
    """`campus_drive` set = the candidate came in through that drive's public
    link: the application is tied to that drive and recorded with the
    campus sources; every identity rule, the screening and the welcome
    email are identical to the careers site.

    `marks_card` (optional, already validated) is stored on the candidate
    profile. If the submission fails before it is committed, the stored file
    is removed again; a returning candidate's previous card is removed only
    once the new one is committed."""
    job = await _load_job(db, job_id, organization_id=organization.id, campus_drive=campus_drive)
    # Cheap checks first, so a bad file never spends the verification token.
    resume_service.validate_resume_upload(
        filename=resume_filename, content=resume_bytes, pdf_only=True
    )
    if marks_card is not None:
        candidate_document_service.validate_marks_card(
            filename=marks_card.filename, content=marks_card.content
        )
    email = email_verification_service.normalize_email(email)
    verification = await email_verification_service.require_verified_token(
        db, organization_id=organization.id, email=email, token=verification_token
    )

    existing = await _identify_applicant(
        db,
        organization=organization,
        email=email,
        phone=profile.phone,
        job=job,
        verification=verification,
    )
    if existing is not None:
        candidate = existing
        await _refresh_returning_profile(
            db,
            organization=organization,
            candidate=candidate,
            profile=profile,
            verification=verification,
            campus_drive=campus_drive,
        )
    else:
        candidate = await _create_new_profile(
            db,
            organization=organization,
            email=email,
            profile=profile,
            verification=verification,
            campus_drive=campus_drive,
        )

    if campus_drive is None:
        # A job with an ACTIVE campus drive gets a careers-site application
        # associated automatically (docs/campus-hiring.md § 2: "Public portal
        # association").
        campus_drive_id = await db.scalar(
            select(CampusDrive.id).where(
                CampusDrive.job_id == job.id, CampusDrive.status == CampusDriveStatus.ACTIVE
            )
        )
    else:
        campus_drive_id = campus_drive.id
    application = await application_service.create_application(
        db,
        organization_id=organization.id,
        candidate_id=candidate.id,
        job_id=job.id,
        source=ApplicationSource.CAMPUS_IMPORT if campus_drive else ApplicationSource.PORTAL,
        actor_user_id=None,
        campus_drive_id=campus_drive_id,
        is_self_service=True,
    )
    if existing is not None:
        await reapply_service.consume_open_grant(
            db, organization_id=organization.id, candidate=candidate, application_id=application.id
        )
    await resume_service.save_resume(
        db,
        storage,
        organization_id=organization.id,
        candidate_id=candidate.id,
        application_id=application.id,
        original_filename=resume_filename,
        content_type=resume_content_type,
        content=resume_bytes,
        pdf_only=True,
    )
    stored_card: StoredFileRef | None = None
    replaced_card: StoredFileRef | None = None
    try:
        if marks_card is not None:
            stored_card, replaced_card = await candidate_document_service.store_marks_card(
                storage, candidate=candidate, upload=marks_card
            )
            await db.flush()
        # `application` was loaded (resume: none yet) before the resume
        # existed; refresh it so screening below sees the file just stored.
        await db.refresh(application, attribute_names=["resume"])
        await email_verification_service.consume(db, verification)

        # Durable from here on: the AI call below can take tens of seconds
        # and may fail; the application must survive either. RLS tenant
        # context is transaction-scoped, so it is re-applied after every
        # commit.
        await db.commit()
    except BaseException:
        # Nothing references the new marks card: don't leave it in storage.
        if stored_card is not None:
            await candidate_document_service.discard(stored_card)
        raise
    if replaced_card is not None:
        await candidate_document_service.discard(replaced_card)
    await set_tenant_context(db, organization.id)

    outcome = await screen_new_application(
        db, organization_id=organization.id, application=application
    )
    await db.commit()
    await set_tenant_context(db, organization.id)

    email_sent = await _send_welcome_email(
        db,
        organization=organization,
        candidate=candidate,
        job=job,
        application=application,
        eligible_for_role=outcome == PublicApplicationOutcome.RECEIVED,
    )

    reloaded = await application_service.get_application(db, application.id)
    assert reloaded is not None
    return SubmissionResult(
        application=reloaded, outcome=outcome, confirmation_email_sent=email_sent
    )


async def screen_new_application(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    application: Application,
    requested_by_user_id: uuid.UUID | None = None,
) -> PublicApplicationOutcome:
    """Runs the AI screening against the job's JD and applies the gate. Any
    failure — provider down, unreadable PDF, or an unexpected error — leaves
    the application in APPLIED for a recruiter: the AI only ever screens
    *out* on an explicit, successful NOT_MATCH verdict.

    Also the gate for an application HR creates with a resume
    (app/services/hr_candidate_intake_service.py), where
    `requested_by_user_id` is that HR user; the AI_SCREENED_OUT status
    change itself is always the system's, never the person's. The caller
    must have committed the application first — a crash here rolls back."""
    label = f"{application.candidate.full_name} — {application.job.title}"
    run: ScreeningRun | None
    try:
        run = await screening_service.run_screening(
            db,
            organization_id=organization_id,
            application_id=application.id,
            requested_by_user_id=requested_by_user_id,
        )
    except Exception:  # never lose or block the application over screening
        logger.exception(
            "Submission-time AI screening crashed",
            extra={"extra_fields": {"application_id": str(application.id)}},
        )
        await db.rollback()
        await set_tenant_context(db, organization_id)
        run = None

    if run is None or run.status != ScreeningStatus.COMPLETED:
        await activity_service.record_activity(
            db,
            organization_id=organization_id,
            actor=None,
            action="AI_SCREENING_FAILED",
            entity_type="application",
            entity_id=application.id,
            entity_label=label,
            description=(
                "Automatic AI screening could not be completed; the application is waiting "
                "for manual recruiter review."
            ),
        )
        return PublicApplicationOutcome.RECEIVED

    if run.decision == ScreeningDecision.NOT_MATCH:
        await application_service.change_status(
            db,
            application,
            to_status=ApplicationStatus.AI_SCREENED_OUT,
            actor_user_id=None,
            reason=AI_SCREENED_OUT_REASON,
        )
        await activity_service.record_activity(
            db,
            organization_id=organization_id,
            actor=None,
            action="AI_SCREENED_OUT",
            entity_type="application",
            entity_id=application.id,
            entity_label=label,
            description=(
                "Automatic AI screening judged the resume a NOT_MATCH for this job. The "
                "profile is retained and HR can override the decision."
            ),
        )
        return PublicApplicationOutcome.NOT_SHORTLISTED_FOR_ROLE

    await activity_service.record_activity(
        db,
        organization_id=organization_id,
        actor=None,
        action="AI_SCREENING_COMPLETED",
        entity_type="application",
        entity_id=application.id,
        entity_label=label,
        description="Automatic AI screening judged the resume a MATCH for this job.",
    )
    return PublicApplicationOutcome.RECEIVED


async def _send_welcome_email(
    db: AsyncSession,
    *,
    organization: Organization,
    candidate: Candidate,
    job: Job,
    application: Application,
    eligible_for_role: bool,
) -> bool:
    """Sent after every successful verified submission, whatever the
    screening decided (`eligible_for_role=False` = the screened-out version).
    Best-effort by design: the application is already committed, so a
    delivery failure is logged and audited — and reported as "not sent" to
    the candidate — but never undoes or fails the submission."""
    rendered = render_welcome_email(
        company_name=organization.name,
        candidate_name=candidate.full_name,
        job_title=job.title,
        contact_email=organization.careers_contact_email,
        eligible_for_role=eligible_for_role,
    )
    label = f"{candidate.full_name} — {job.title}"
    try:
        await notification_service.send_email(
            to=[candidate.email],
            subject=rendered.subject,
            html=rendered.html,
            text=rendered.text,
            reply_to=organization.careers_contact_email,
            kind="candidate_welcome",
        )
    except EmailError as exc:
        await activity_service.record_activity(
            db,
            organization_id=organization.id,
            actor=None,
            action="CANDIDATE_WELCOME_EMAIL_FAILED",
            entity_type="application",
            entity_id=application.id,
            entity_label=label,
            description=f"The automatic welcome email to {candidate.full_name} could not be sent.",
            reason=str(exc)[:500],
        )
        return False

    await activity_service.record_activity(
        db,
        organization_id=organization.id,
        actor=None,
        action="CANDIDATE_WELCOME_EMAIL_SENT",
        entity_type="application",
        entity_id=application.id,
        entity_label=label,
        description=f'Welcome email sent to {candidate.full_name} ("{rendered.subject}").',
    )
    return True
