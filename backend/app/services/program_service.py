"""Recruitment programs — configuration, publication and the per-candidate
enrollment lifecycle (docs/programs.md).

Program applications are ordinary `Application`s on the program's own
`Job`; everything program-specific is on `ProgramEnrollment`, and every
state change goes through the tables in app/workflows/program_workflow.py.
Nothing here invents a business value: amounts, fees and dates are only
ever what an HR user entered.
"""

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.models.application import Application, ApplicationStatus
from app.models.assessment import Assessment, AssessmentInvitation, AssessmentStatus
from app.models.candidate import Candidate, EducationLevel
from app.models.institution import Institution
from app.models.job import EmploymentType, Job, JobStatus
from app.models.program import (
    AgreementStatus,
    DisbursementStatus,
    EligibilityStatus,
    OfferStatus,
    PaymentRecordStatus,
    ProgramEnrollment,
    ProgramFeePayment,
    ProgramStatus,
    RecruitmentProgram,
    RecruitmentProgramInstitution,
    ScholarshipDisbursement,
    ScholarshipEligibility,
    ScholarshipStatus,
    TrainingStatus,
)
from app.models.user import User
from app.schemas.program import (
    ProgramCreateRequest,
    ProgramEducationDeclaration,
    ProgramReport,
    ProgramUpdateRequest,
)
from app.services import (
    activity_service,
    application_service,
    institution_service,
    reapply_service,
)
from app.workflows import program_workflow
from app.workflows.program_workflow import require_transition

_EDUCATION_LABELS = {
    EducationLevel.DIPLOMA: "Diploma",
    EducationLevel.BACHELORS: "Bachelor's degree",
    EducationLevel.MASTERS: "Master's degree",
    EducationLevel.MTECH: "M.Tech",
    EducationLevel.PHD: "PhD",
    EducationLevel.OTHER: "Other",
}

#: Values copied 1:1 between the request schemas and the model.
_SIMPLE_FIELDS = (
    "title",
    "description",
    "eligible_study_years",
    "eligible_specializations",
    "expected_completion_from",
    "expected_completion_to",
    "eligibility_notes",
    "training_duration_options",
    "training_fee_amount",
    "training_fee_currency",
    "fee_payment_terms",
    "fee_disclosure",
    "scholarship_amount_min",
    "scholarship_amount_max",
    "scholarship_currency",
    "scholarship_eligibility_rules",
    "scholarship_disbursement_timing",
    "scholarship_disclosure",
    "employment_pathway",
    "employment_job_id",
    "bond_duration_months",
    "bond_terms_summary",
    "bond_start_rule",
    "application_opens_at",
    "application_closes_at",
    "interview_stages",
    "required_documents",
    "candidate_disclosures",
    "terms_version",
    "tracks_fee_payments",
    "tracks_training",
    "tracks_employment",
    "requires_agreement",
)

#: Changing any of these on a program that already has applicants requires
#: a new `terms_version`, so every applicant's acceptance stays tied to the
#: exact wording they saw.
_DISCLOSED_FIELDS = (
    "candidate_disclosures",
    "training_fee_amount",
    "training_fee_currency",
    "fee_payment_terms",
    "fee_disclosure",
    "scholarship_amount_min",
    "scholarship_amount_max",
    "scholarship_currency",
    "scholarship_eligibility_rules",
    "scholarship_disbursement_timing",
    "scholarship_disclosure",
    "bond_duration_months",
    "bond_terms_summary",
    "employment_pathway",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _money(amount: Decimal | None, currency: str | None) -> str:
    return f"{currency} {amount:,.2f}" if amount is not None and currency else ""


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


# --- Loading ---


_PROGRAM_LOADS = (
    selectinload(RecruitmentProgram.eligible_institutions),
    selectinload(RecruitmentProgram.assessment),
    selectinload(RecruitmentProgram.employment_job),
)


async def get_program(
    db: AsyncSession, organization_id: uuid.UUID, program_id: uuid.UUID
) -> RecruitmentProgram | None:
    result = await db.execute(
        select(RecruitmentProgram)
        .where(
            RecruitmentProgram.id == program_id,
            RecruitmentProgram.organization_id == organization_id,
        )
        .options(*_PROGRAM_LOADS)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def require_program(
    db: AsyncSession, organization_id: uuid.UUID, program_id: uuid.UUID
) -> RecruitmentProgram:
    program = await get_program(db, organization_id, program_id)
    if program is None:
        raise NotFoundError("Program not found.")
    return program


async def list_programs(
    db: AsyncSession, organization_id: uuid.UUID, *, status: ProgramStatus | None = None
) -> list[RecruitmentProgram]:
    query = (
        select(RecruitmentProgram)
        .where(RecruitmentProgram.organization_id == organization_id)
        .options(*_PROGRAM_LOADS)
        .order_by(RecruitmentProgram.created_at.desc())
    )
    if status is not None:
        query = query.where(RecruitmentProgram.status == status)
    result = await db.execute(query)
    return list(result.scalars().all())


async def enrollment_counts(db: AsyncSession, organization_id: uuid.UUID) -> dict[uuid.UUID, int]:
    result = await db.execute(
        select(ProgramEnrollment.program_id, func.count(ProgramEnrollment.id))
        .join(Application, Application.id == ProgramEnrollment.application_id)
        .where(
            ProgramEnrollment.organization_id == organization_id,
            Application.deleted_at.is_(None),
        )
        .group_by(ProgramEnrollment.program_id)
    )
    return {program_id: count for program_id, count in result.all()}


async def is_program_job(db: AsyncSession, job_id: uuid.UUID) -> bool:
    """A program's carrier job is never applied to through the careers site."""
    return (
        await db.scalar(select(RecruitmentProgram.id).where(RecruitmentProgram.job_id == job_id))
        is not None
    )


# --- Configuration ---


async def _validated_assessment(
    db: AsyncSession, organization_id: uuid.UUID, assessment_id: uuid.UUID
) -> Assessment:
    assessment = await db.get(Assessment, assessment_id)
    if (
        assessment is None
        or assessment.organization_id != organization_id
        or assessment.deleted_at is not None
    ):
        raise NotFoundError("Assessment not found.")
    return assessment


async def _validated_employment_job(
    db: AsyncSession, organization_id: uuid.UUID, job_id: uuid.UUID
) -> Job:
    """The program's intended destination must be an ordinary FULL_TIME
    job of this organization — never another program's carrier job."""
    job = await db.get(Job, job_id)
    if job is None or job.organization_id != organization_id or job.deleted_at is not None:
        raise NotFoundError("Job not found.")
    if job.employment_type != EmploymentType.FULL_TIME or await is_program_job(db, job.id):
        raise UnprocessableError(
            "A program's employment pathway can only point to a full-time job.",
            code="program_employment_job_not_full_time",
        )
    return job


async def _set_institutions(
    db: AsyncSession, program: RecruitmentProgram, institution_ids: list[uuid.UUID]
) -> None:
    for institution_id in institution_ids:
        await institution_service.require_institution(db, program.organization_id, institution_id)
    existing = await db.execute(
        select(RecruitmentProgramInstitution).where(
            RecruitmentProgramInstitution.program_id == program.id
        )
    )
    for link in existing.scalars().all():
        await db.delete(link)
    await db.flush()
    for institution_id in dict.fromkeys(institution_ids):
        db.add(RecruitmentProgramInstitution(program_id=program.id, institution_id=institution_id))
    await db.flush()


def _apply_fields(program: RecruitmentProgram, values: dict[str, Any]) -> None:
    for field in _SIMPLE_FIELDS:
        if field in values and values[field] is not None:
            setattr(program, field, values[field])
        elif field in values and field not in ("title", "description"):
            # An explicit null clears an optional value (lists to empty).
            current = getattr(program, field)
            setattr(program, field, [] if isinstance(current, list) else None)
    if "eligible_education_levels" in values:
        program.eligible_education_levels = [
            level.value for level in (values["eligible_education_levels"] or [])
        ]
    for flag in ("tracks_fee_payments", "tracks_training", "tracks_employment", "requires_agreement"):
        if flag in values and values[flag] is None:
            setattr(program, flag, True)


async def create_program(
    db: AsyncSession, *, actor: User, payload: ProgramCreateRequest
) -> RecruitmentProgram:
    """A new DRAFT program, with its own carrier job (kept in DRAFT so it is
    never listed on the careers site)."""
    assert actor.organization_id is not None
    if payload.assessment_id is not None:
        await _validated_assessment(db, actor.organization_id, payload.assessment_id)
    if payload.employment_job_id is not None:
        await _validated_employment_job(db, actor.organization_id, payload.employment_job_id)

    job = Job(
        organization_id=actor.organization_id,
        created_by=actor.id,
        title=payload.title,
        description=payload.description,
        # No employment type: the program is its own workflow, never an
        # ordinary internship or job (app/models/job.py::EmploymentType).
        employment_type=None,
        status=JobStatus.DRAFT,
    )
    db.add(job)
    await db.flush()

    program = RecruitmentProgram(
        organization_id=actor.organization_id,
        job_id=job.id,
        created_by=actor.id,
        status=ProgramStatus.DRAFT,
        title=payload.title,
        description=payload.description,
        assessment_id=payload.assessment_id,
    )
    values = payload.model_dump(exclude_unset=True)
    _apply_fields(program, values)
    if program.bond_duration_months is None and "bond_duration_months" not in values:
        program.bond_duration_months = 36
    db.add(program)
    await db.flush()
    if payload.eligible_institution_ids:
        await _set_institutions(db, program, payload.eligible_institution_ids)

    await activity_service.record_activity(
        db,
        organization_id=program.organization_id,
        actor=actor,
        action="PROGRAM_CREATED",
        entity_type="program",
        entity_id=program.id,
        entity_label=program.title,
        description=f'Program "{program.title}" was created as a draft.',
    )
    return await require_program(db, program.organization_id, program.id)


def publication_problems(program: RecruitmentProgram) -> list[str]:
    """What a program needs before it can be shown to candidates. Each
    not-yet-finalized money value may be left unset only if HR supplied
    business-approved wording to show instead."""
    problems: list[str] = []
    if _blank(program.title) or _blank(program.description):
        problems.append("Add a title and description.")
    if not program.eligible_education_levels:
        problems.append("Choose at least one eligible education level.")
    if not program.training_duration_options:
        problems.append("Choose the training duration (6 or 12 months).")
    has_fee = program.training_fee_amount is not None and program.training_fee_currency
    if not has_fee and _blank(program.fee_disclosure):
        problems.append(
            "Enter the finalized training fee and currency, or the approved fee disclosure wording."
        )
    has_scholarship = program.scholarship_amount_min is not None and program.scholarship_currency
    if not has_scholarship and _blank(program.scholarship_disclosure):
        problems.append(
            "Enter the finalized scholarship amount/range and currency, or the approved "
            "scholarship disclosure wording."
        )
    if _blank(program.scholarship_eligibility_rules):
        problems.append("Describe the scholarship eligibility and approval rules.")
    if _blank(program.employment_pathway):
        problems.append("Describe the full-time employment pathway.")
    if not program.bond_duration_months or _blank(program.bond_terms_summary):
        problems.append("Enter the commitment (bond) duration and the approved summary of its terms.")
    if _blank(program.candidate_disclosures) or _blank(program.terms_version):
        problems.append("Add the approved candidate terms/disclosures and their version label.")
    if program.assessment is not None and (
        program.assessment.deleted_at is not None
        or program.assessment.status != AssessmentStatus.PUBLISHED
    ):
        problems.append("Publish the selection assessment (or remove it) first.")
    if (
        program.application_opens_at
        and program.application_closes_at
        and program.application_closes_at <= program.application_opens_at
    ):
        problems.append("Applications must close after they open.")
    return problems


def _require_publishable(program: RecruitmentProgram) -> None:
    problems = publication_problems(program)
    if problems:
        raise UnprocessableError(
            "This program can't be published yet: " + " ".join(problems),
            code="program_incomplete",
            data={"problems": problems},
        )


async def has_enrollments(db: AsyncSession, program_id: uuid.UUID) -> bool:
    return (
        await db.scalar(
            select(ProgramEnrollment.id).where(ProgramEnrollment.program_id == program_id).limit(1)
        )
        is not None
    )


async def update_program(
    db: AsyncSession, program: RecruitmentProgram, payload: ProgramUpdateRequest, *, actor: User
) -> RecruitmentProgram:
    if program.status == ProgramStatus.ARCHIVED:
        raise ConflictError("An archived program can't be edited.")
    values = payload.model_dump(exclude_unset=True)
    if not values:
        return program

    before = {field: getattr(program, field) for field in _DISCLOSED_FIELDS}
    previous_terms = program.terms_version

    if "assessment_id" in values:
        if values["assessment_id"] is not None:
            await _validated_assessment(db, program.organization_id, values["assessment_id"])
        if values["assessment_id"] != program.assessment_id and await has_enrollments(db, program.id):
            raise ConflictError(
                "Candidates have already applied to this program, so its selection test "
                "can't be changed.",
                code="program_has_applicants",
            )
        program.assessment_id = values["assessment_id"]
    if values.get("employment_job_id") is not None:
        await _validated_employment_job(db, program.organization_id, values["employment_job_id"])
    _apply_fields(program, values)
    if "eligible_institution_ids" in values:
        await _set_institutions(db, program, values["eligible_institution_ids"] or [])

    disclosed_changed = any(getattr(program, f) != before[f] for f in _DISCLOSED_FIELDS)
    if (
        disclosed_changed
        and program.terms_version == previous_terms
        and await has_enrollments(db, program.id)
    ):
        raise ConflictError(
            "Candidates have already accepted this program's terms. Changing the disclosed "
            "terms needs a new terms version label.",
            code="terms_version_required",
        )

    await db.flush()
    reloaded = await require_program(db, program.organization_id, program.id)
    if reloaded.status in (ProgramStatus.PUBLISHED, ProgramStatus.PAUSED):
        _require_publishable(reloaded)

    job = await db.get(Job, reloaded.job_id)
    if job is not None:
        job.title = reloaded.title
        job.description = reloaded.description
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=program.organization_id,
        actor=actor,
        action="PROGRAM_UPDATED",
        entity_type="program",
        entity_id=program.id,
        entity_label=reloaded.title,
        description=f'Program "{reloaded.title}" was updated ({", ".join(sorted(values))}).',
    )
    return reloaded


async def change_status(
    db: AsyncSession, program: RecruitmentProgram, status: ProgramStatus, *, actor: User
) -> RecruitmentProgram:
    if status == program.status:
        return program
    require_transition(
        program_workflow.PROGRAM_STATUS_TRANSITIONS, program.status, status, what="program"
    )
    if status == ProgramStatus.PUBLISHED:
        _require_publishable(program)
        if program.published_at is None:
            program.published_at = _now()
            program.published_by_user_id = actor.id
    previous = program.status
    program.status = status
    await db.flush()
    await activity_service.record_activity(
        db,
        organization_id=program.organization_id,
        actor=actor,
        action=f"PROGRAM_{status.value}",
        entity_type="program",
        entity_id=program.id,
        entity_label=program.title,
        description=(
            f'Program "{program.title}" moved from {previous.value.lower()} to {status.value.lower()}.'
        ),
    )
    return await require_program(db, program.organization_id, program.id)


# --- Candidate-facing ---


def accepting_applications(program: RecruitmentProgram) -> tuple[bool, str | None]:
    if program.status != ProgramStatus.PUBLISHED:
        return False, "This program is not currently accepting applications."
    now = _now()
    if program.application_opens_at and now < program.application_opens_at:
        return False, "Applications for this program have not opened yet."
    if program.application_closes_at and now >= program.application_closes_at:
        return False, "Applications for this program have closed."
    return True, None


def disclosure_text(program: RecruitmentProgram) -> str:
    """The exact terms a candidate agrees to by applying — built only from
    what HR entered, so nothing is promised that HR didn't approve. Stored
    verbatim on the enrollment."""
    lines = [f"{program.title} — terms (version {program.terms_version})", ""]
    if program.training_duration_options:
        durations = " or ".join(f"{d} months" for d in program.training_duration_options)
        lines.append(f"Training duration: {durations}.")
    fee = _money(program.training_fee_amount, program.training_fee_currency)
    if fee:
        lines.append(f"Training fee (paid by the candidate): {fee}.")
    if program.fee_payment_terms:
        lines.append(f"Fee payment terms: {program.fee_payment_terms.strip()}")
    if program.fee_disclosure:
        lines.append(f"Training fee: {program.fee_disclosure.strip()}")
    low = _money(program.scholarship_amount_min, program.scholarship_currency)
    high = _money(program.scholarship_amount_max, program.scholarship_currency)
    if low:
        lines.append(f"Scholarship: {low}" + (f" to {high}" if high and high != low else "") + ".")
    if program.scholarship_disclosure:
        lines.append(f"Scholarship: {program.scholarship_disclosure.strip()}")
    if program.scholarship_eligibility_rules:
        lines.append(f"Scholarship eligibility: {program.scholarship_eligibility_rules.strip()}")
    if program.scholarship_disbursement_timing:
        lines.append(f"Scholarship disbursement: {program.scholarship_disbursement_timing.strip()}")
    if program.bond_duration_months:
        lines.append(
            f"Scholarship recipients agree to a {program.bond_duration_months}-month commitment "
            "under the company's approved agreement."
        )
    if program.bond_terms_summary:
        lines.append(f"Commitment terms: {program.bond_terms_summary.strip()}")
    if program.employment_pathway:
        lines.append(f"Employment pathway: {program.employment_pathway.strip()}")
    lines.append(
        "Passing the test, receiving a scholarship or completing training does not by itself "
        "guarantee employment; the final decision follows the company's approved process."
    )
    if program.candidate_disclosures:
        lines.extend(["", program.candidate_disclosures.strip()])
    return "\n".join(lines)


def eligibility_problems(
    program: RecruitmentProgram, declaration: ProgramEducationDeclaration
) -> list[str]:
    """Checks the candidate's declaration against the program's configured
    criteria (an empty criterion accepts anything)."""
    problems: list[str] = []
    levels = program.eligible_education_levels
    if levels and declaration.education_level.value not in levels:
        accepted = ", ".join(_EDUCATION_LABELS[EducationLevel(v)] for v in levels)
        problems.append(f"This program is open to: {accepted}.")
    years = program.eligible_study_years
    if years:
        if declaration.study_year is None:
            problems.append("Enter your current year of study.")
        elif declaration.study_year not in years:
            problems.append(
                "This program is open to students in year "
                + ", ".join(str(y) for y in years)
                + " of their programme."
            )
    specializations = program.eligible_specializations
    if specializations and declaration.specialization.lower() not in {
        s.lower() for s in specializations
    }:
        problems.append("This program is open to: " + ", ".join(specializations) + ".")
    if program.eligible_institutions:
        allowed = {i.id for i in program.eligible_institutions if i.is_active}
        if declaration.institution_id is None or declaration.institution_id not in allowed:
            problems.append("Choose your institution from the program's eligible institutions.")
    if program.expected_completion_from or program.expected_completion_to:
        completion = declaration.expected_completion_date
        if completion is None:
            problems.append("Enter your expected completion date.")
        elif (program.expected_completion_from and completion < program.expected_completion_from) or (
            program.expected_completion_to and completion > program.expected_completion_to
        ):
            problems.append("Your expected completion date is outside this program's window.")
    return problems


def check_application_allowed(
    program: RecruitmentProgram, declaration: ProgramEducationDeclaration
) -> None:
    """Before anything is stored (so a refused application never spends the
    email-verification token)."""
    accepting, reason = accepting_applications(program)
    if not accepting:
        raise ConflictError(reason or "Not accepting applications.", code="program_not_accepting")
    if declaration.terms_version != program.terms_version:
        raise ConflictError(
            "The program terms have been updated since you opened this page. Please review "
            "the current terms and accept them again.",
            code="terms_outdated",
        )
    problems = eligibility_problems(program, declaration)
    if problems:
        raise UnprocessableError(
            "You don't meet this program's eligibility criteria. " + " ".join(problems),
            code="program_ineligible",
            data={"problems": problems},
        )


async def record_enrollment(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    application: Application,
    candidate: Candidate,
    declaration: ProgramEducationDeclaration,
) -> ProgramEnrollment:
    """Called inside the application's own transaction (before its first
    commit), so a program application never exists without its enrollment.
    The declared education also refreshes the candidate's profile."""
    institution_name = declaration.institution_name or ""
    if declaration.institution_id is not None:
        institution = await db.get(Institution, declaration.institution_id)
        assert institution is not None  # checked by eligibility_problems
        institution_name = institution.name

    candidate.education_level = declaration.education_level
    candidate.current_study_year = declaration.study_year
    candidate.specialization = declaration.specialization
    candidate.institution_id = declaration.institution_id
    candidate.institution_name = institution_name
    candidate.expected_completion_date = declaration.expected_completion_date

    enrollment = ProgramEnrollment(
        organization_id=program.organization_id,
        program_id=program.id,
        application_id=application.id,
        candidate_id=candidate.id,
        education_level=declaration.education_level,
        study_year=declaration.study_year,
        specialization=declaration.specialization,
        institution_id=declaration.institution_id,
        institution_name=institution_name,
        expected_completion_date=declaration.expected_completion_date,
        terms_version_accepted=declaration.terms_version,
        terms_accepted_at=_now(),
        disclosure_snapshot=disclosure_text(program),
        bond_duration_months=program.bond_duration_months,
    )
    db.add(enrollment)
    await db.flush()
    await activity_service.record_activity(
        db,
        organization_id=program.organization_id,
        actor=None,
        action="PROGRAM_APPLICATION_RECEIVED",
        entity_type="application",
        entity_id=application.id,
        entity_label=f"{candidate.full_name} — {program.title}",
        description=(
            f"{candidate.full_name} applied to the program \"{program.title}\" and accepted "
            f"terms version {declaration.terms_version}."
        ),
    )
    return enrollment


# --- Enrollments ---


@dataclass(frozen=True)
class EnrollmentView:
    enrollment: ProgramEnrollment
    application: Application
    candidate: Candidate
    latest_invitation: AssessmentInvitation | None
    fee_payments: list[ProgramFeePayment]
    disbursements: list[ScholarshipDisbursement]


async def _latest_invitations(
    db: AsyncSession, application_ids: list[uuid.UUID]
) -> dict[uuid.UUID, AssessmentInvitation]:
    if not application_ids:
        return {}
    result = await db.execute(
        select(AssessmentInvitation)
        .where(AssessmentInvitation.application_id.in_(application_ids))
        .options(selectinload(AssessmentInvitation.result))
        .order_by(AssessmentInvitation.application_id, AssessmentInvitation.attempt_number)
    )
    latest: dict[uuid.UUID, AssessmentInvitation] = {}
    for invitation in result.scalars().all():
        latest[invitation.application_id] = invitation
    return latest


async def list_enrollments(
    db: AsyncSession,
    program: RecruitmentProgram,
    *,
    eligibility: EligibilityStatus | None = None,
    scholarship: ScholarshipStatus | None = None,
    training: TrainingStatus | None = None,
) -> list[EnrollmentView]:
    query = (
        select(ProgramEnrollment, Application, Candidate)
        .join(Application, Application.id == ProgramEnrollment.application_id)
        .join(Candidate, Candidate.id == ProgramEnrollment.candidate_id)
        .where(
            ProgramEnrollment.organization_id == program.organization_id,
            ProgramEnrollment.program_id == program.id,
            Application.deleted_at.is_(None),
        )
        .order_by(Application.applied_at.desc())
    )
    if eligibility is not None:
        query = query.where(ProgramEnrollment.eligibility_status == eligibility)
    if scholarship is not None:
        query = query.where(ProgramEnrollment.scholarship_status == scholarship)
    if training is not None:
        query = query.where(ProgramEnrollment.training_status == training)
    rows = (await db.execute(query)).all()
    invitations = await _latest_invitations(db, [application.id for _, application, _ in rows])
    return [
        EnrollmentView(
            enrollment=enrollment,
            application=application,
            candidate=candidate,
            latest_invitation=invitations.get(application.id),
            fee_payments=[],
            disbursements=[],
        )
        for enrollment, application, candidate in rows
    ]


async def require_enrollment(
    db: AsyncSession, program: RecruitmentProgram, enrollment_id: uuid.UUID
) -> ProgramEnrollment:
    enrollment = await db.get(ProgramEnrollment, enrollment_id)
    if (
        enrollment is None
        or enrollment.organization_id != program.organization_id
        or enrollment.program_id != program.id
    ):
        raise NotFoundError("Program participant not found.")
    return enrollment


async def enrollment_view(db: AsyncSession, enrollment: ProgramEnrollment) -> EnrollmentView:
    application = await application_service.get_application(db, enrollment.application_id)
    assert application is not None
    invitations = await _latest_invitations(db, [application.id])
    payments = await db.execute(
        select(ProgramFeePayment)
        .where(ProgramFeePayment.enrollment_id == enrollment.id)
        .order_by(ProgramFeePayment.paid_on, ProgramFeePayment.created_at)
    )
    disbursements = await db.execute(
        select(ScholarshipDisbursement)
        .where(ScholarshipDisbursement.enrollment_id == enrollment.id)
        .order_by(ScholarshipDisbursement.created_at)
    )
    return EnrollmentView(
        enrollment=enrollment,
        application=application,
        candidate=application.candidate,
        latest_invitation=invitations.get(application.id),
        fee_payments=list(payments.scalars().all()),
        disbursements=list(disbursements.scalars().all()),
    )


async def _audit(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    actor: User,
    action: str,
    description: str,
    reason: str | None = None,
) -> None:
    candidate = await db.get(Candidate, enrollment.candidate_id)
    program = await db.get(RecruitmentProgram, enrollment.program_id)
    await activity_service.record_activity(
        db,
        organization_id=enrollment.organization_id,
        actor=actor,
        action=action,
        entity_type="application",
        entity_id=enrollment.application_id,
        entity_label=(
            f"{candidate.full_name if candidate else 'Candidate'} — "
            f"{program.title if program else 'Program'}"
        ),
        description=description,
        reason=reason,
    )


async def _application(db: AsyncSession, enrollment: ProgramEnrollment) -> Application:
    application = await application_service.get_application(db, enrollment.application_id)
    if application is None or application.deleted_at is not None:
        raise NotFoundError("Application not found.")
    return application


async def review_eligibility(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    status: EligibilityStatus,
    note: str | None,
    actor: User,
) -> ProgramEnrollment:
    application = await _application(db, enrollment)
    if application.status not in program_workflow.REVIEW_STATUSES:
        raise ConflictError(
            "This application has moved past eligibility review.", code="eligibility_locked"
        )
    if status == EligibilityStatus.NOT_ELIGIBLE and _blank(note):
        raise UnprocessableError("Give a reason when marking a candidate not eligible.", code="note_required")
    require_transition(
        program_workflow.ELIGIBILITY_TRANSITIONS, enrollment.eligibility_status, status,
        what="eligibility review",
    )
    enrollment.eligibility_status = status
    enrollment.eligibility_reviewed_by_user_id = actor.id
    enrollment.eligibility_reviewed_at = _now()
    enrollment.eligibility_note = note.strip() if note and note.strip() else None
    await db.flush()
    if status == EligibilityStatus.ELIGIBLE and application.status == ApplicationStatus.APPLIED:
        await application_service.change_status(
            db, application, to_status=ApplicationStatus.UNDER_REVIEW, actor_user_id=actor.id
        )
    await _audit(
        db,
        enrollment=enrollment,
        actor=actor,
        action="PROGRAM_ELIGIBILITY_REVIEWED",
        description=f"Program eligibility marked {status.value.replace('_', ' ').lower()}.",
        reason=enrollment.eligibility_note,
    )
    return enrollment


async def invite_to_assessment(
    db: AsyncSession, *, program: RecruitmentProgram, enrollment: ProgramEnrollment, actor: User
) -> tuple[AssessmentInvitation, str]:
    """Moves an eligible candidate into the program's selection test through
    the ordinary pipeline (UNDER_REVIEW -> SCREENING -> invited). Nothing is
    emailed here — the invitation is sent from the application, like every
    other assessment invitation."""
    from app.services import assessment_service

    if program.assessment_id is None:
        raise ConflictError("This program has no selection test configured.")
    application = await _application(db, enrollment)
    if application.status == ApplicationStatus.UNDER_REVIEW:
        await application_service.change_status(
            db, application, to_status=ApplicationStatus.SCREENING, actor_user_id=actor.id
        )
    elif application.status != ApplicationStatus.SCREENING:
        raise ConflictError(
            "Only an application under review can be invited to the program's test.",
            code="invalid_program_transition",
        )
    assert actor.organization_id is not None
    return await assessment_service.invite_candidate(
        db,
        organization_id=actor.organization_id,
        assessment_id=program.assessment_id,
        application_id=application.id,
        invited_by_user_id=actor.id,
    )


async def mark_interview_completed(
    db: AsyncSession, *, enrollment: ProgramEnrollment, actor: User
) -> ProgramEnrollment:
    application = await _application(db, enrollment)
    if application.status != ApplicationStatus.INTERVIEW:
        raise ConflictError(
            "Move the application to Interview (and hold the interview) before recording it "
            "as completed.",
            code="interview_not_scheduled",
        )
    if enrollment.interview_completed_at is not None:
        return enrollment
    enrollment.interview_completed_at = _now()
    enrollment.interview_completed_by_user_id = actor.id
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_INTERVIEW_COMPLETED",
        description="Program interview recorded as completed.",
    )
    return enrollment


async def decide_scholarship_eligibility(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    decision: ScholarshipEligibility,
    note: str | None,
    actor: User,
) -> ProgramEnrollment:
    """Eligibility for scholarship *consideration* — only for a candidate
    selected through the program's process. Never an approval."""
    application = await _application(db, enrollment)
    if application.status not in program_workflow.SELECTED_STATUSES:
        raise ConflictError(
            "Scholarship eligibility is decided only for candidates selected through the "
            "program's selection process.",
            code="selection_required",
        )
    if enrollment.scholarship_status in (ScholarshipStatus.APPROVED, ScholarshipStatus.DECLINED):
        raise ConflictError("The scholarship has already been decided.", code="scholarship_decided")
    require_transition(
        program_workflow.SCHOLARSHIP_ELIGIBILITY_TRANSITIONS,
        enrollment.scholarship_eligibility,
        decision,
        what="scholarship eligibility",
    )
    enrollment.scholarship_eligibility = decision
    enrollment.scholarship_eligibility_decided_by_user_id = actor.id
    enrollment.scholarship_eligibility_decided_at = _now()
    enrollment.scholarship_status = (
        ScholarshipStatus.PENDING
        if decision == ScholarshipEligibility.ELIGIBLE
        else ScholarshipStatus.NOT_STARTED
    )
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_SCHOLARSHIP_ELIGIBILITY_DECIDED",
        description=(
            "Marked eligible for scholarship consideration (decision pending)."
            if decision == ScholarshipEligibility.ELIGIBLE
            else "Marked not eligible for scholarship consideration."
        ),
        reason=note,
    )
    return enrollment


async def decide_scholarship(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    enrollment: ProgramEnrollment,
    decision: ScholarshipStatus,
    amount: Decimal | None,
    currency: str | None,
    note: str | None,
    actor: User,
) -> ProgramEnrollment:
    if enrollment.scholarship_eligibility != ScholarshipEligibility.ELIGIBLE:
        raise ConflictError(
            "Mark the candidate eligible for scholarship consideration first.",
            code="scholarship_eligibility_required",
        )
    require_transition(
        program_workflow.SCHOLARSHIP_STATUS_TRANSITIONS, enrollment.scholarship_status, decision,
        what="scholarship decision",
    )
    if decision == ScholarshipStatus.APPROVED:
        assert amount is not None and currency is not None
        if program.scholarship_currency and currency != program.scholarship_currency:
            raise UnprocessableError(
                f"This program's scholarship is in {program.scholarship_currency}."
            )
        if program.scholarship_amount_min is not None and amount < program.scholarship_amount_min:
            raise UnprocessableError("The amount is below the program's approved scholarship range.")
        if program.scholarship_amount_max is not None and amount > program.scholarship_amount_max:
            raise UnprocessableError("The amount is above the program's approved scholarship range.")
        enrollment.approved_scholarship_amount = amount
        enrollment.approved_scholarship_currency = currency
        if program.requires_agreement and enrollment.agreement_status == AgreementStatus.NOT_REQUIRED:
            enrollment.agreement_status = AgreementStatus.PENDING
        if enrollment.bond_duration_months is None:
            enrollment.bond_duration_months = program.bond_duration_months
    enrollment.scholarship_status = decision
    enrollment.scholarship_decided_by_user_id = actor.id
    enrollment.scholarship_decided_at = _now()
    enrollment.scholarship_decision_note = note.strip() if note and note.strip() else None
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_SCHOLARSHIP_DECIDED",
        description=(
            f"Scholarship approved: {_money(amount, currency)} (not yet disbursed)."
            if decision == ScholarshipStatus.APPROVED
            else "Scholarship declined."
        ),
        reason=enrollment.scholarship_decision_note,
    )
    return enrollment


async def _disbursement_total(db: AsyncSession, enrollment_id: uuid.UUID) -> Decimal:
    total = await db.scalar(
        select(func.coalesce(func.sum(ScholarshipDisbursement.amount), 0)).where(
            ScholarshipDisbursement.enrollment_id == enrollment_id,
            ScholarshipDisbursement.status != DisbursementStatus.CANCELLED,
        )
    )
    return Decimal(total or 0)


async def record_disbursement(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    amount: Decimal,
    status: DisbursementStatus,
    scheduled_on: date | None,
    paid_on: date | None,
    reference: str | None,
    note: str | None,
    actor: User,
) -> ScholarshipDisbursement:
    if enrollment.scholarship_status != ScholarshipStatus.APPROVED:
        raise ConflictError("Disbursements can be recorded only for an approved scholarship.")
    assert enrollment.approved_scholarship_amount is not None
    if await _disbursement_total(db, enrollment.id) + amount > enrollment.approved_scholarship_amount:
        raise UnprocessableError(
            "Disbursements can't add up to more than the approved scholarship amount.",
            code="disbursement_exceeds_approved",
        )
    disbursement = ScholarshipDisbursement(
        organization_id=enrollment.organization_id,
        enrollment_id=enrollment.id,
        amount=amount,
        currency=enrollment.approved_scholarship_currency or "",
        status=status,
        scheduled_on=scheduled_on,
        paid_on=paid_on if status == DisbursementStatus.PAID else None,
        reference=reference.strip() if reference and reference.strip() else None,
        note=note,
        recorded_by_user_id=actor.id,
    )
    db.add(disbursement)
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_SCHOLARSHIP_DISBURSEMENT_RECORDED",
        description=(
            f"Scholarship disbursement of {_money(amount, disbursement.currency)} recorded as "
            f"{status.value.lower()}."
        ),
    )
    return disbursement


async def update_disbursement(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    disbursement_id: uuid.UUID,
    status: DisbursementStatus,
    paid_on: date | None,
    note: str | None,
    actor: User,
) -> ScholarshipDisbursement:
    disbursement = await db.get(ScholarshipDisbursement, disbursement_id)
    if disbursement is None or disbursement.enrollment_id != enrollment.id:
        raise NotFoundError("Disbursement not found.")
    if disbursement.status != DisbursementStatus.SCHEDULED:
        raise ConflictError(
            f"This disbursement is already {disbursement.status.value.lower()} and can't be changed."
        )
    disbursement.status = status
    if status == DisbursementStatus.PAID:
        disbursement.paid_on = paid_on
    if note:
        disbursement.note = note
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_SCHOLARSHIP_DISBURSEMENT_UPDATED",
        description=(
            f"Scholarship disbursement of {_money(disbursement.amount, disbursement.currency)} "
            f"marked {status.value.lower()}."
        ),
        reason=note,
    )
    return disbursement


async def record_fee_payment(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    enrollment: ProgramEnrollment,
    amount: Decimal,
    currency: str,
    paid_on: date,
    method: Any,
    reference: str | None,
    note: str | None,
    actor: User,
) -> ProgramFeePayment:
    """Manual record of a payment the candidate made outside the platform
    (no gateway; nothing is charged or stored beyond a receipt reference)."""
    if not program.tracks_fee_payments:
        raise ConflictError("This program doesn't track training-fee payments.")
    if program.training_fee_currency and currency != program.training_fee_currency:
        raise UnprocessableError(f"This program's fee is in {program.training_fee_currency}.")
    payment = ProgramFeePayment(
        organization_id=enrollment.organization_id,
        enrollment_id=enrollment.id,
        amount=amount,
        currency=currency,
        paid_on=paid_on,
        method=method,
        reference=reference.strip() if reference and reference.strip() else None,
        note=note,
        recorded_by_user_id=actor.id,
    )
    db.add(payment)
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_FEE_PAYMENT_RECORDED",
        description=f"Training-fee payment of {_money(amount, currency)} recorded ({method.value}).",
    )
    return payment


async def void_fee_payment(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    payment_id: uuid.UUID,
    reason: str,
    actor: User,
) -> ProgramFeePayment:
    payment = await db.get(ProgramFeePayment, payment_id)
    if payment is None or payment.enrollment_id != enrollment.id:
        raise NotFoundError("Payment record not found.")
    if payment.status == PaymentRecordStatus.VOIDED:
        raise ConflictError("This payment record is already voided.")
    payment.status = PaymentRecordStatus.VOIDED
    payment.void_reason = reason.strip()
    payment.voided_by_user_id = actor.id
    payment.voided_at = _now()
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_FEE_PAYMENT_VOIDED",
        description=f"Training-fee payment record of {_money(payment.amount, payment.currency)} voided.",
        reason=payment.void_reason,
    )
    return payment


async def update_training(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    enrollment: ProgramEnrollment,
    status: TrainingStatus,
    duration_months: int | None,
    start_date: date | None,
    end_date: date | None,
    actor: User,
) -> ProgramEnrollment:
    if not program.tracks_training:
        raise ConflictError("This program doesn't track training.")
    application = await _application(db, enrollment)
    if application.status not in program_workflow.SELECTED_STATUSES:
        raise ConflictError("Only a selected candidate can be enrolled in training.", code="selection_required")
    if status != enrollment.training_status:
        require_transition(
            program_workflow.TRAINING_TRANSITIONS, enrollment.training_status, status, what="training"
        )
    duration = duration_months or enrollment.training_duration_months
    if status == TrainingStatus.ENROLLED:
        if duration is None:
            raise UnprocessableError("Choose the training duration.")
        if duration not in program.training_duration_options:
            raise UnprocessableError(
                "That duration isn't one of this program's training options."
            )
    if duration is not None:
        enrollment.training_duration_months = duration
    if start_date is not None:
        enrollment.training_start_date = start_date
    if end_date is not None:
        enrollment.training_end_date = end_date
    if (
        enrollment.training_start_date
        and enrollment.training_end_date
        and enrollment.training_end_date < enrollment.training_start_date
    ):
        raise UnprocessableError("Training can't end before it starts.")
    previous = enrollment.training_status
    enrollment.training_status = status
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_TRAINING_UPDATED",
        description=(
            f"Training {previous.value.replace('_', ' ').lower()} -> "
            f"{status.value.replace('_', ' ').lower()}"
            + (f" ({enrollment.training_duration_months} months)." if enrollment.training_duration_months else ".")
        ),
    )
    return enrollment


async def update_offer(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    enrollment: ProgramEnrollment,
    status: OfferStatus,
    note: str | None,
    actor: User,
) -> ProgramEnrollment:
    if not program.tracks_employment:
        raise ConflictError("This program doesn't track employment offers.")
    application = await _application(db, enrollment)
    if status == OfferStatus.OFFERED and application.status != ApplicationStatus.SELECTED:
        raise ConflictError(
            "A full-time offer can be recorded only for a selected candidate.", code="selection_required"
        )
    require_transition(
        program_workflow.OFFER_TRANSITIONS, enrollment.offer_status, status, what="employment offer"
    )
    enrollment.offer_status = status
    if status == OfferStatus.OFFERED:
        enrollment.offer_made_at = _now()
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_OFFER_UPDATED",
        description=f"Full-time employment offer {status.value.lower()}.",
        reason=note,
    )
    return enrollment


async def record_joining(
    db: AsyncSession, *, enrollment: ProgramEnrollment, joined_on: date, actor: User
) -> ProgramEnrollment:
    if enrollment.offer_status != OfferStatus.ACCEPTED:
        raise ConflictError("Record an accepted offer before the joining date.", code="program_offer_required")
    application = await _application(db, enrollment)
    enrollment.joined_on = joined_on
    await db.flush()
    if application.status == ApplicationStatus.SELECTED:
        await application_service.change_status(
            db, application, to_status=ApplicationStatus.HIRED, actor_user_id=actor.id
        )
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_JOINED",
        description=f"Joined full-time employment on {joined_on.isoformat()}.",
    )
    return enrollment


async def update_agreement(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    status: AgreementStatus,
    reference: str | None,
    signed_on: date | None,
    actor: User,
) -> ProgramEnrollment:
    """Tracks the company-approved agreement by reference — the platform
    never generates or edits agreement wording."""
    if status != enrollment.agreement_status:
        require_transition(
            program_workflow.AGREEMENT_TRANSITIONS, enrollment.agreement_status, status, what="agreement"
        )
    if reference is not None:
        enrollment.agreement_reference = reference.strip() or None
    if status == AgreementStatus.SIGNED:
        if signed_on is None:
            raise UnprocessableError("Enter the date the agreement was signed.")
        if not enrollment.agreement_reference and not enrollment.agreement_document_storage_path:
            raise UnprocessableError(
                "Record the approved agreement's reference or attach the signed copy first."
            )
        enrollment.agreement_signed_on = signed_on
    previous = enrollment.agreement_status
    enrollment.agreement_status = status
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_AGREEMENT_UPDATED",
        description=f"Agreement {previous.value.replace('_', ' ').lower()} -> {status.value.replace('_', ' ').lower()}.",
    )
    return enrollment


async def set_bond(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    start_date: date,
    duration_months: int | None,
    actor: User,
) -> ProgramEnrollment:
    """The bond's start is not finalized as a business rule, so HR records
    the actual start date; the end follows from the duration."""
    if enrollment.scholarship_status != ScholarshipStatus.APPROVED:
        raise ConflictError("A bond applies only to an approved scholarship recipient.")
    if enrollment.agreement_status != AgreementStatus.SIGNED:
        raise ConflictError("Record the signed agreement before the bond dates.", code="agreement_required")
    duration = duration_months or enrollment.bond_duration_months
    if not duration:
        raise UnprocessableError("Enter the bond duration.")
    enrollment.bond_duration_months = duration
    enrollment.bond_start_date = start_date
    enrollment.bond_end_date = reapply_service.add_months(
        datetime.combine(start_date, time(), tzinfo=UTC), duration
    ).date()
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_BOND_RECORDED",
        description=(
            f"Bond recorded: {duration} months from {start_date.isoformat()} to "
            f"{enrollment.bond_end_date.isoformat()}."
        ),
    )
    return enrollment


async def attach_agreement_document(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    storage_provider: str,
    storage_path: str,
    filename: str,
    actor: User,
) -> str | None:
    """Points the enrollment at a newly stored signed copy; returns the
    previous copy's storage path (for the caller to delete once committed)."""
    previous = enrollment.agreement_document_storage_path
    enrollment.agreement_document_storage_provider = storage_provider
    enrollment.agreement_document_storage_path = storage_path
    enrollment.agreement_document_filename = filename
    enrollment.agreement_document_uploaded_at = _now()
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_AGREEMENT_DOCUMENT_UPLOADED",
        description=f'Signed agreement document "{filename}" attached.',
    )
    return previous


# --- Reporting ---


async def program_report(db: AsyncSession, program: RecruitmentProgram) -> ProgramReport:
    views = await list_enrollments(db, program)
    status_counts = Counter(view.application.status for view in views)
    passed = failed = pending_review = 0
    invited = 0
    for view in views:
        invitation = view.latest_invitation
        if invitation is None:
            continue
        invited += 1
        result = invitation.result
        if result is None:
            continue
        if result.passed is None:
            pending_review += 1
        elif result.passed:
            passed += 1
        else:
            failed += 1

    def count(predicate: Any) -> int:
        return sum(1 for view in views if predicate(view.enrollment))

    return ProgramReport(
        applications=len(views),
        eligibility_pending=count(lambda e: e.eligibility_status == EligibilityStatus.PENDING),
        eligible=count(lambda e: e.eligibility_status == EligibilityStatus.ELIGIBLE),
        not_eligible=count(lambda e: e.eligibility_status == EligibilityStatus.NOT_ELIGIBLE),
        assessment_invited=invited,
        assessment_passed=passed,
        assessment_failed=failed,
        assessment_pending_review=pending_review,
        interview=status_counts[ApplicationStatus.INTERVIEW],
        interview_completed=count(lambda e: e.interview_completed_at is not None),
        selected=status_counts[ApplicationStatus.SELECTED] + status_counts[ApplicationStatus.HIRED],
        not_selected=status_counts[ApplicationStatus.REJECTED],
        scholarship_eligible=count(lambda e: e.scholarship_eligibility == ScholarshipEligibility.ELIGIBLE),
        scholarship_pending=count(lambda e: e.scholarship_status == ScholarshipStatus.PENDING),
        scholarship_approved=count(lambda e: e.scholarship_status == ScholarshipStatus.APPROVED),
        scholarship_declined=count(lambda e: e.scholarship_status == ScholarshipStatus.DECLINED),
        training_enrolled=count(lambda e: e.training_status == TrainingStatus.ENROLLED),
        training_in_progress=count(lambda e: e.training_status == TrainingStatus.IN_PROGRESS),
        training_completed=count(lambda e: e.training_status == TrainingStatus.COMPLETED),
        offers_made=count(lambda e: e.offer_status != OfferStatus.NOT_OFFERED),
        offers_accepted=count(lambda e: e.offer_status == OfferStatus.ACCEPTED),
        joined=count(lambda e: e.joined_on is not None),
        agreements_signed=count(lambda e: e.agreement_status == AgreementStatus.SIGNED),
    )
