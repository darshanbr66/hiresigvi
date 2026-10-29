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
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.models.application import Application, ApplicationStatus
from app.models.assessment import Assessment, AssessmentInvitation, AssessmentStatus
from app.models.candidate import Candidate, EducationLevel
from app.models.institution import (
    AffiliationStatus,
    AffiliationVerificationMethod,
    CampusAffiliationVerification,
    Institution,
)
from app.models.interview import ApplicationInterview
from app.models.job import EmploymentType, Job, JobStatus
from app.models.program import (
    AgreementStatus,
    DisbursementStatus,
    EligibilityStatus,
    EmploymentReviewStatus,
    FundingRevisionKind,
    FundingRevisionStatus,
    FundingType,
    MilestoneStatus,
    OfferStatus,
    PaymentRecordStatus,
    ProgramEnrollment,
    ProgramFeePayment,
    ProgramFundingRevision,
    ProgramStatus,
    RecruitmentProgram,
    RecruitmentProgramInstitution,
    ScholarshipDisbursement,
    ScholarshipEligibility,
    ScholarshipStatus,
    TrainingMilestone,
    TrainingStatus,
)
from app.models.team_hierarchy import Department, Employee, EmploymentStatus
from app.models.user import User
from app.schemas.program import (
    ProgramCreateRequest,
    ProgramEducationDeclaration,
    ProgramReport,
    ProgramUpdateRequest,
)
from app.schemas.team_hierarchy import EmployeeCreateRequest
from app.services import (
    activity_service,
    application_service,
    campus_affiliation_service,
    employee_service,
    institution_service,
    interview_service,
    program_finance,
    reapply_service,
)
from app.services.program_finance import FundingError, money
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
    "company_funding_type",
    "company_funding_value",
    "funding_requires_independent_approval",
    "affiliation_verification_method",
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
    "company_funding_type",
    "company_funding_value",
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
    for flag in (
        "tracks_fee_payments",
        "tracks_training",
        "tracks_employment",
        "requires_agreement",
        "funding_requires_independent_approval",
    ):
        if flag in values and values[flag] is None:
            setattr(program, flag, True)
    if "affiliation_verification_method" in values and values["affiliation_verification_method"] is None:
        program.affiliation_verification_method = AffiliationVerificationMethod.MANUAL


def funding_config_problems(program: RecruitmentProgram) -> list[str]:
    """The program's standard funding basis must be internally consistent:
    both type and value or neither, a valid percentage, a fixed amount no
    larger than a configured fee and inside the configured range."""
    problems: list[str] = []
    if (program.company_funding_type is None) != (program.company_funding_value is None):
        problems.append("Enter both the company funding basis and its value, or neither.")
        return problems
    if program.company_funding_type is None or program.company_funding_value is None:
        return problems
    fee = program.training_fee_amount
    try:
        if fee is not None:
            split = program_finance.split_fee(
                fee, program.company_funding_type, program.company_funding_value
            )
            program_finance.check_contribution_range(
                split.company_contribution,
                program.scholarship_amount_min,
                program.scholarship_amount_max,
            )
        else:
            program_finance.split_fee(
                program.company_funding_value
                if program.company_funding_type == FundingType.FIXED_AMOUNT
                else Decimal("100"),
                program.company_funding_type,
                program.company_funding_value,
            )
    except FundingError as exc:
        problems.append(str(exc))
    if (
        program.company_funding_type == FundingType.FIXED_AMOUNT
        and program.scholarship_currency
        and program.training_fee_currency
        and program.scholarship_currency != program.training_fee_currency
    ):
        problems.append("Company funding and the training fee must be in the same currency.")
    return problems


def _require_valid_funding_config(program: RecruitmentProgram) -> None:
    problems = funding_config_problems(program)
    if problems:
        raise UnprocessableError(" ".join(problems), code="invalid_funding_config", data={"problems": problems})


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
    _require_valid_funding_config(program)
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
    _require_valid_funding_config(program)
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
    if program.company_funding_type is not None and program.company_funding_value is not None:
        basis = (
            f"{program.company_funding_value.normalize():f}% of the training fee"
            if program.company_funding_type == FundingType.PERCENTAGE
            else _money(program.company_funding_value, program.training_fee_currency)
            or f"{program.company_funding_value:,.2f}"
        )
        lines.append(
            f"Company funding (scholarship) for eligible selected candidates: {basis}, only if "
            "approved; the candidate pays the remaining fee."
        )
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
    await campus_affiliation_service.open_program_verification(
        db,
        organization_id=program.organization_id,
        program_id=program.id,
        program_title=program.title,
        method=program.affiliation_verification_method,
        institution_id=declaration.institution_id,
        institution_name=institution_name,
        application=application,
        candidate=candidate,
    )
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
    #: Sum of RECORDED (non-voided) fee payments — also filled in lists.
    fee_paid_total: Decimal = Decimal("0")
    affiliation: CampusAffiliationVerification | None = None
    milestones: list[TrainingMilestone] = field(default_factory=list)
    # Detail only.
    funding_revisions: list[ProgramFundingRevision] = field(default_factory=list)
    interviews: list[ApplicationInterview] = field(default_factory=list)


async def _paid_totals(db: AsyncSession, enrollment_ids: list[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    if not enrollment_ids:
        return {}
    result = await db.execute(
        select(ProgramFeePayment.enrollment_id, func.sum(ProgramFeePayment.amount))
        .where(
            ProgramFeePayment.enrollment_id.in_(enrollment_ids),
            ProgramFeePayment.status == PaymentRecordStatus.RECORDED,
        )
        .group_by(ProgramFeePayment.enrollment_id)
    )
    return {eid: Decimal(total) for eid, total in result.all()}


async def _affiliations(
    db: AsyncSession, application_ids: list[uuid.UUID]
) -> dict[uuid.UUID, CampusAffiliationVerification]:
    if not application_ids:
        return {}
    result = await db.execute(
        select(CampusAffiliationVerification).where(
            CampusAffiliationVerification.application_id.in_(application_ids),
            CampusAffiliationVerification.program_id.is_not(None),
        )
    )
    return {record.application_id: record for record in result.scalars().all()}


async def _milestones(
    db: AsyncSession, enrollment_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[TrainingMilestone]]:
    grouped: dict[uuid.UUID, list[TrainingMilestone]] = {eid: [] for eid in enrollment_ids}
    if not enrollment_ids:
        return grouped
    result = await db.execute(
        select(TrainingMilestone)
        .where(TrainingMilestone.enrollment_id.in_(enrollment_ids))
        .order_by(TrainingMilestone.due_on.asc().nulls_last(), TrainingMilestone.created_at)
    )
    for milestone in result.scalars().all():
        grouped[milestone.enrollment_id].append(milestone)
    return grouped


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
    application_ids = [application.id for _, application, _ in rows]
    enrollment_ids = [enrollment.id for enrollment, _, _ in rows]
    invitations = await _latest_invitations(db, application_ids)
    paid = await _paid_totals(db, enrollment_ids)
    affiliations = await _affiliations(db, application_ids)
    milestones = await _milestones(db, enrollment_ids)
    return [
        EnrollmentView(
            enrollment=enrollment,
            application=application,
            candidate=candidate,
            latest_invitation=invitations.get(application.id),
            fee_payments=[],
            disbursements=[],
            fee_paid_total=paid.get(enrollment.id, Decimal("0")),
            affiliation=affiliations.get(application.id),
            milestones=milestones.get(enrollment.id, []),
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
    fee_payments = list(payments.scalars().all())
    revisions = await db.execute(
        select(ProgramFundingRevision)
        .where(ProgramFundingRevision.enrollment_id == enrollment.id)
        .order_by(ProgramFundingRevision.revision_number)
    )
    return EnrollmentView(
        enrollment=enrollment,
        application=application,
        candidate=application.candidate,
        latest_invitation=invitations.get(application.id),
        fee_payments=fee_payments,
        disbursements=list(disbursements.scalars().all()),
        fee_paid_total=sum(
            (p.amount for p in fee_payments if p.status == PaymentRecordStatus.RECORDED), Decimal("0")
        ),
        affiliation=(await _affiliations(db, [application.id])).get(application.id),
        milestones=(await _milestones(db, [enrollment.id]))[enrollment.id],
        funding_revisions=list(revisions.scalars().all()),
        interviews=await interview_service.list_for_application(
            db, enrollment.organization_id, application.id
        ),
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
    if status == EligibilityStatus.ELIGIBLE:
        affiliation = (await _affiliations(db, [application.id])).get(application.id)
        if affiliation is not None and affiliation.status != AffiliationStatus.VERIFIED:
            raise ConflictError(
                "Verify the candidate's institution membership before marking them eligible.",
                code="affiliation_verification_required",
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


async def review_affiliation(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    enrollment: ProgramEnrollment,
    status: AffiliationStatus,
    method: AffiliationVerificationMethod | None,
    note: str | None,
    actor: User,
) -> ProgramEnrollment:
    application = await _application(db, enrollment)
    record = (await _affiliations(db, [application.id])).get(application.id)
    if record is None:
        raise ConflictError(
            "This program doesn't require institution verification for this application.",
            code="affiliation_not_required",
        )
    if enrollment.eligibility_status == EligibilityStatus.ELIGIBLE and status != AffiliationStatus.VERIFIED:
        raise ConflictError(
            "The candidate was already found eligible. Mark them not eligible first if their "
            "institution membership no longer holds.",
            code="eligibility_locked",
        )
    await campus_affiliation_service.review_program_verification(
        db, record=record, status=status, method=method, note=note, actor=actor,
        context_label=program.title,
    )
    return enrollment


async def mark_interview_completed(
    db: AsyncSession, *, program: RecruitmentProgram, enrollment: ProgramEnrollment, actor: User
) -> ProgramEnrollment:
    """The selection gate: every configured interview stage has a recorded
    outcome and nothing is still scheduled."""
    application = await _application(db, enrollment)
    if application.status != ApplicationStatus.INTERVIEW:
        raise ConflictError(
            "Move the application to Interview (and hold the interview) before recording it "
            "as completed.",
            code="interview_not_scheduled",
        )
    if enrollment.interview_completed_at is not None:
        return enrollment
    interviews = await interview_service.list_for_application(
        db, enrollment.organization_id, application.id
    )
    problems = interview_service.completion_problems(interviews, list(program.interview_stages))
    if problems:
        raise ConflictError(
            " ".join(problems), code="interview_outcomes_required", data={"problems": problems}
        )
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
    """Eligibility for company-funding *consideration* — only for a
    candidate selected through the program's process. Never an approval."""
    application = await _application(db, enrollment)
    if application.status not in program_workflow.SELECTED_STATUSES:
        raise ConflictError(
            "Funding eligibility is decided only for candidates selected through the "
            "program's selection process.",
            code="selection_required",
        )
    if enrollment.scholarship_status in (ScholarshipStatus.APPROVED, ScholarshipStatus.DECLINED):
        raise ConflictError("The funding has already been decided.", code="scholarship_decided")
    if enrollment.scholarship_status == ScholarshipStatus.PROPOSED:
        raise ConflictError(
            "A funding proposal is awaiting approval. Withdraw it first.", code="funding_proposed"
        )
    require_transition(
        program_workflow.SCHOLARSHIP_ELIGIBILITY_TRANSITIONS,
        enrollment.scholarship_eligibility,
        decision,
        what="funding eligibility",
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
            "Marked eligible for company-funding consideration (no funding approved yet)."
            if decision == ScholarshipEligibility.ELIGIBLE
            else "Marked not eligible for company-funding consideration."
        ),
        reason=note,
    )
    return enrollment


# --- Fee obligation and company funding (docs/programs.md § 5a) ---


def _require_fee_configured(program: RecruitmentProgram) -> tuple[Decimal, str]:
    if program.training_fee_amount is None or not program.training_fee_currency:
        raise ConflictError(
            "Enter the program's finalized training fee and currency first.",
            code="training_fee_not_configured",
        )
    return program.training_fee_amount, program.training_fee_currency


def _fix_obligation(program: RecruitmentProgram, enrollment: ProgramEnrollment) -> None:
    """Snapshots the program's fee onto the participant the first time it
    is needed. Once fixed, later program fee edits never change it."""
    if enrollment.fee_amount is not None:
        return
    fee, currency = _require_fee_configured(program)
    enrollment.fee_amount = money(fee)
    enrollment.fee_currency = currency
    enrollment.company_contribution_amount = None
    enrollment.candidate_payable_amount = money(fee)


async def set_fee_obligation(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    enrollment: ProgramEnrollment,
    due_date: date | None,
    actor: User,
) -> ProgramEnrollment:
    """Fixes the candidate's training-fee obligation (fee snapshot) and its
    due date. Only for a selected candidate on a program that tracks fees."""
    if not program.tracks_fee_payments:
        raise ConflictError("This program doesn't track training-fee payments.")
    application = await _application(db, enrollment)
    if application.status not in program_workflow.SELECTED_STATUSES:
        raise ConflictError(
            "A fee obligation is recorded only for a selected candidate.", code="selection_required"
        )
    newly_fixed = enrollment.fee_amount is None
    _fix_obligation(program, enrollment)
    enrollment.fee_due_date = due_date
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_FEE_OBLIGATION_SET",
        description=(
            f"Training fee of {_money(enrollment.fee_amount, enrollment.fee_currency)} "
            + ("fixed for this participant" if newly_fixed else "obligation updated")
            + (f", due {due_date.isoformat()}." if due_date else ".")
        ),
    )
    return enrollment


async def _next_revision_number(db: AsyncSession, enrollment_id: uuid.UUID) -> int:
    current = await db.scalar(
        select(func.max(ProgramFundingRevision.revision_number)).where(
            ProgramFundingRevision.enrollment_id == enrollment_id
        )
    )
    return (current or 0) + 1


async def _lock(db: AsyncSession, enrollment: ProgramEnrollment) -> None:
    """Serializes money decisions on one participant (row lock until the
    transaction ends), so two concurrent approvals or payments can't both
    pass a check that only one of them should."""
    await db.execute(
        select(ProgramEnrollment.id).where(ProgramEnrollment.id == enrollment.id).with_for_update()
    )


async def _revision(
    db: AsyncSession, enrollment: ProgramEnrollment, revision_id: uuid.UUID
) -> ProgramFundingRevision:
    await _lock(db, enrollment)
    revision = await db.scalar(
        select(ProgramFundingRevision)
        .where(ProgramFundingRevision.id == revision_id)
        .execution_options(populate_existing=True)
    )
    if (
        revision is None
        or revision.enrollment_id != enrollment.id
        or revision.organization_id != enrollment.organization_id
    ):
        raise NotFoundError("Funding proposal not found.")
    return revision


async def _approved_revision(
    db: AsyncSession, enrollment_id: uuid.UUID
) -> ProgramFundingRevision | None:
    revision: ProgramFundingRevision | None = await db.scalar(
        select(ProgramFundingRevision).where(
            ProgramFundingRevision.enrollment_id == enrollment_id,
            ProgramFundingRevision.status == FundingRevisionStatus.APPROVED,
        )
    )
    return revision


def _describe_basis(funding_type: FundingType, value: Decimal, currency: str) -> str:
    if funding_type == FundingType.PERCENTAGE:
        return f"{value.normalize():f}% of the fee"
    return f"fixed {_money(value, currency)}"


async def propose_funding(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    enrollment: ProgramEnrollment,
    funding_type: FundingType,
    value: Decimal,
    reason: str | None,
    actor: User,
) -> ProgramFundingRevision:
    """Proposes company funding for an eligible participant. The amounts are
    computed from the participant's fixed fee (snapshotted now if needed)
    and stored on the proposal. Nothing is approved here.

    Before the candidate accepted any terms a new proposal is a REVISION
    (or the INITIAL one); after acceptance it is an AMENDMENT and needs a
    reason. An already-approved decision stays in force until a new
    revision is approved."""
    if enrollment.scholarship_eligibility != ScholarshipEligibility.ELIGIBLE:
        raise ConflictError(
            "Mark the candidate eligible for funding consideration first.",
            code="scholarship_eligibility_required",
        )
    if enrollment.scholarship_status == ScholarshipStatus.DECLINED:
        raise ConflictError(
            "Funding was declined. Reopen funding eligibility to propose new terms.",
            code="scholarship_decided",
        )
    _fix_obligation(program, enrollment)
    assert enrollment.fee_amount is not None and enrollment.fee_currency is not None
    try:
        split = program_finance.split_fee(enrollment.fee_amount, funding_type, value)
        program_finance.check_contribution_range(
            split.company_contribution, program.scholarship_amount_min, program.scholarship_amount_max
        )
    except FundingError as exc:
        raise UnprocessableError(str(exc), code="invalid_funding") from exc
    if program.scholarship_currency and program.scholarship_currency != enrollment.fee_currency:
        raise UnprocessableError(
            "Company funding and the training fee must be in the same currency.",
            code="invalid_funding",
        )

    accepted = enrollment.funding_terms_accepted_at is not None
    has_history = await db.scalar(
        select(ProgramFundingRevision.id).where(ProgramFundingRevision.enrollment_id == enrollment.id).limit(1)
    )
    kind = (
        FundingRevisionKind.AMENDMENT
        if accepted
        else (FundingRevisionKind.REVISION if has_history else FundingRevisionKind.INITIAL)
    )
    if kind == FundingRevisionKind.AMENDMENT and _blank(reason):
        raise UnprocessableError(
            "The candidate already accepted the current terms. Give the reason for this amendment.",
            code="reason_required",
        )

    revision = ProgramFundingRevision(
        organization_id=enrollment.organization_id,
        enrollment_id=enrollment.id,
        revision_number=await _next_revision_number(db, enrollment.id),
        kind=kind,
        status=FundingRevisionStatus.PROPOSED,
        fee_amount=split.fee,
        currency=enrollment.fee_currency,
        funding_type=funding_type,
        funding_value=money(value),
        company_contribution_amount=split.company_contribution,
        candidate_payable_amount=split.candidate_payable,
        reason=reason.strip() if reason and reason.strip() else None,
        proposed_by_user_id=actor.id,
    )
    db.add(revision)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "A funding proposal for this participant is already awaiting approval.",
            code="funding_proposal_open",
        ) from exc
    if enrollment.scholarship_status == ScholarshipStatus.PENDING:
        require_transition(
            program_workflow.SCHOLARSHIP_STATUS_TRANSITIONS, enrollment.scholarship_status,
            ScholarshipStatus.PROPOSED, what="funding decision",
        )
        enrollment.scholarship_status = ScholarshipStatus.PROPOSED
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_FUNDING_PROPOSED",
        description=(
            f"Funding revision {revision.revision_number} ({kind.value.lower()}) proposed: "
            f"{_describe_basis(funding_type, revision.funding_value, revision.currency)} -> company "
            f"{_money(split.company_contribution, revision.currency)}, candidate "
            f"{_money(split.candidate_payable, revision.currency)} of "
            f"{_money(split.fee, revision.currency)}. Awaiting approval."
        ),
        reason=revision.reason,
    )
    return revision


async def decide_funding(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    enrollment: ProgramEnrollment,
    revision_id: uuid.UUID,
    decision: FundingRevisionStatus,
    note: str | None,
    actor: User,
) -> ProgramFundingRevision:
    """APPROVED or REJECTED by a finance approver. With independent approval
    configured, the approver can't be the proposer; nobody can approve
    funding on their own application."""
    revision = await _revision(db, enrollment, revision_id)
    if decision not in (FundingRevisionStatus.APPROVED, FundingRevisionStatus.REJECTED):
        raise UnprocessableError("A funding proposal is either approved or rejected.")
    require_transition(
        program_workflow.FUNDING_REVISION_TRANSITIONS, revision.status, decision,
        what="funding proposal",
    )
    if program.funding_requires_independent_approval and revision.proposed_by_user_id == actor.id:
        raise ForbiddenError(
            "This program requires funding to be approved by someone other than the person "
            "who proposed it."
        )
    candidate = await db.get(Candidate, enrollment.candidate_id)
    if candidate is not None and candidate.email.strip().lower() == actor.email.strip().lower():
        raise ForbiddenError("You can't decide funding on your own application.")
    if decision == FundingRevisionStatus.REJECTED and _blank(note):
        raise UnprocessableError("Give a reason for rejecting the proposal.", code="note_required")

    previous = await _approved_revision(db, enrollment.id)
    if decision == FundingRevisionStatus.APPROVED:
        # The new terms must still cover what was already applied/disbursed
        # (including under an approval recorded before funding revisions).
        if await _disbursement_total(db, enrollment.id) > revision.company_contribution_amount:
            raise ConflictError(
                "More funding has already been applied or scheduled than this revision approves.",
                code="disbursement_exceeds_approved",
            )
        if previous is not None:
            previous.status = FundingRevisionStatus.SUPERSEDED

    revision.status = decision
    revision.decided_by_user_id = actor.id
    revision.decided_at = _now()
    revision.decision_note = note.strip() if note and note.strip() else None

    if decision == FundingRevisionStatus.APPROVED:
        enrollment.scholarship_status = ScholarshipStatus.APPROVED
        enrollment.approved_scholarship_amount = revision.company_contribution_amount
        enrollment.approved_scholarship_currency = revision.currency
        enrollment.company_contribution_amount = revision.company_contribution_amount
        enrollment.candidate_payable_amount = revision.candidate_payable_amount
        enrollment.scholarship_decided_by_user_id = actor.id
        enrollment.scholarship_decided_at = revision.decided_at
        enrollment.scholarship_decision_note = revision.decision_note
        if revision.kind == FundingRevisionKind.AMENDMENT:
            # Amended terms need the candidate's renewed acceptance.
            enrollment.funding_terms_accepted_at = None
            enrollment.funding_terms_accepted_revision = None
        if program.requires_agreement and enrollment.agreement_status == AgreementStatus.NOT_REQUIRED:
            enrollment.agreement_status = AgreementStatus.PENDING
        if enrollment.bond_duration_months is None:
            enrollment.bond_duration_months = program.bond_duration_months
        if enrollment.bond_duration_months:
            enrollment.bond_applies_reason = (
                f"Company funding approved (revision {revision.revision_number}: "
                f"{_money(revision.company_contribution_amount, revision.currency)} of the "
                f"{_money(revision.fee_amount, revision.currency)} training fee); the program's "
                f"{enrollment.bond_duration_months}-month commitment applies under the "
                "company's approved agreement."
            )
    elif enrollment.scholarship_status == ScholarshipStatus.PROPOSED:
        # A rejected first proposal is the funding decision; a rejected
        # revision of an approved decision leaves that decision in force.
        enrollment.scholarship_status = ScholarshipStatus.DECLINED
        enrollment.scholarship_decided_by_user_id = actor.id
        enrollment.scholarship_decided_at = revision.decided_at
        enrollment.scholarship_decision_note = revision.decision_note
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor,
        action="PROGRAM_FUNDING_APPROVED" if decision == FundingRevisionStatus.APPROVED else "PROGRAM_FUNDING_REJECTED",
        description=(
            f"Funding revision {revision.revision_number} approved: company "
            f"{_money(revision.company_contribution_amount, revision.currency)}, candidate payable "
            f"{_money(revision.candidate_payable_amount, revision.currency)} (not yet applied)."
            if decision == FundingRevisionStatus.APPROVED
            else f"Funding revision {revision.revision_number} rejected."
        ),
        reason=revision.decision_note,
    )
    return revision


async def withdraw_funding_proposal(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    revision_id: uuid.UUID,
    note: str | None,
    actor: User,
) -> ProgramFundingRevision:
    revision = await _revision(db, enrollment, revision_id)
    require_transition(
        program_workflow.FUNDING_REVISION_TRANSITIONS, revision.status, FundingRevisionStatus.WITHDRAWN,
        what="funding proposal",
    )
    revision.status = FundingRevisionStatus.WITHDRAWN
    revision.decided_by_user_id = actor.id
    revision.decided_at = _now()
    revision.decision_note = note.strip() if note and note.strip() else None
    if enrollment.scholarship_status == ScholarshipStatus.PROPOSED:
        enrollment.scholarship_status = ScholarshipStatus.PENDING
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_FUNDING_WITHDRAWN",
        description=f"Funding proposal (revision {revision.revision_number}) withdrawn.",
        reason=revision.decision_note,
    )
    return revision


async def accept_funding_terms(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    enrollment: ProgramEnrollment,
    accepted_on: date,
    actor: User,
) -> ProgramEnrollment:
    """Records that the candidate accepted the approved funding terms (for a
    program without a formal agreement; with one, signing records it)."""
    if program.requires_agreement:
        raise ConflictError(
            "This program records acceptance through the signed agreement.",
            code="agreement_required",
        )
    approved = await _approved_revision(db, enrollment.id)
    if approved is None:
        raise ConflictError("There are no approved funding terms to accept.", code="funding_not_approved")
    if enrollment.funding_terms_accepted_revision == approved.revision_number:
        raise ConflictError("The candidate already accepted these terms.", code="already_accepted")
    enrollment.funding_terms_accepted_revision = approved.revision_number
    enrollment.funding_terms_accepted_at = datetime.combine(accepted_on, time(), tzinfo=UTC)
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_FUNDING_TERMS_ACCEPTED",
        description=(
            f"Candidate's acceptance of funding revision {approved.revision_number} recorded "
            f"({accepted_on.isoformat()})."
        ),
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
    """Company funding applied to the fee / paid out, as recorded by
    finance — never more than the approved contribution."""
    if enrollment.scholarship_status != ScholarshipStatus.APPROVED:
        raise ConflictError("Funding can be applied only once it is approved.")
    await _lock(db, enrollment)
    assert enrollment.approved_scholarship_amount is not None
    if await _disbursement_total(db, enrollment.id) + amount > enrollment.approved_scholarship_amount:
        raise UnprocessableError(
            "Applied funding can't add up to more than the approved company contribution.",
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
            f"Company funding of {_money(amount, disbursement.currency)} recorded as "
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
            f"Company funding of {_money(disbursement.amount, disbursement.currency)} "
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
    (no gateway; nothing is charged or stored beyond a receipt reference).
    Needs a fixed obligation, matches its currency, and can't take the
    total paid past the candidate's payable amount."""
    if not program.tracks_fee_payments:
        raise ConflictError("This program doesn't track training-fee payments.")
    if enrollment.fee_amount is None or enrollment.candidate_payable_amount is None:
        raise ConflictError(
            "Record the candidate's fee obligation before recording payments.",
            code="fee_obligation_required",
        )
    if currency != enrollment.fee_currency:
        raise UnprocessableError(f"This participant's fee is in {enrollment.fee_currency}.")
    if _blank(reference):
        raise UnprocessableError(
            "Enter the receipt or transaction reference that confirms this payment.",
            code="reference_required",
        )
    await _lock(db, enrollment)
    paid = (await _paid_totals(db, [enrollment.id])).get(enrollment.id, Decimal("0"))
    if paid + amount > enrollment.candidate_payable_amount:
        raise UnprocessableError(
            "This payment would take the total paid past the candidate's payable amount "
            f"({_money(enrollment.candidate_payable_amount, enrollment.fee_currency)}; "
            f"already recorded {_money(paid, enrollment.fee_currency)}).",
            code="payment_exceeds_payable",
        )
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


# --- Training ---


async def update_training(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    enrollment: ProgramEnrollment,
    status: TrainingStatus,
    duration_months: int | None,
    start_date: date | None,
    end_date: date | None,
    completed_on: date | None,
    note: str | None,
    actor: User,
) -> ProgramEnrollment:
    """Full-time training (6 or 12 months). Enrolling fixes the fee
    obligation when the program tracks fees; the planned end follows from
    start + duration unless HR enters one; completion needs its date;
    withdrawal/termination needs a reason."""
    if not program.tracks_training:
        raise ConflictError("This program doesn't track training.")
    application = await _application(db, enrollment)
    if application.status not in program_workflow.SELECTED_STATUSES:
        raise ConflictError("Only a selected candidate can be enrolled in training.", code="selection_required")
    if enrollment.scholarship_status == ScholarshipStatus.PROPOSED:
        raise ConflictError(
            "Decide the pending funding proposal before changing training, so the candidate's "
            "fee obligation is settled.",
            code="funding_proposed",
        )
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
        if (
            program.tracks_fee_payments
            and program.training_fee_amount is not None
            and program.training_fee_currency
        ):
            # Fixed now when the fee is finalized; while only approved fee
            # wording exists, enrollment proceeds and finance fixes the
            # obligation once the amount is decided.
            _fix_obligation(program, enrollment)
    if status == TrainingStatus.IN_PROGRESS and (start_date or enrollment.training_start_date) is None:
        raise UnprocessableError("Enter the date training started.")
    if status == TrainingStatus.COMPLETED and completed_on is None:
        raise UnprocessableError("Enter the date training was completed.")
    if status in program_workflow.TRAINING_ENDED_EARLY and _blank(note):
        raise UnprocessableError("Give the reason training ended early.", code="note_required")

    if duration is not None:
        enrollment.training_duration_months = duration
    if start_date is not None:
        enrollment.training_start_date = start_date
    if end_date is not None:
        enrollment.training_end_date = end_date
    elif enrollment.training_start_date and enrollment.training_duration_months and (
        start_date is not None or enrollment.training_end_date is None
    ):
        enrollment.training_end_date = reapply_service.add_months(
            datetime.combine(enrollment.training_start_date, time(), tzinfo=UTC),
            enrollment.training_duration_months,
        ).date()
    if (
        enrollment.training_start_date
        and enrollment.training_end_date
        and enrollment.training_end_date < enrollment.training_start_date
    ):
        raise UnprocessableError("Training can't end before it starts.")
    if completed_on is not None:
        if enrollment.training_start_date and completed_on < enrollment.training_start_date:
            raise UnprocessableError("Training can't be completed before it starts.")
        enrollment.training_completed_on = completed_on
    if status == TrainingStatus.ENROLLED and enrollment.training_enrolled_at is None:
        enrollment.training_enrolled_at = _now()
    if note and note.strip():
        enrollment.training_outcome_note = note.strip()
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
        reason=note.strip() if note and note.strip() else None,
    )
    return enrollment


_OPEN_TRAINING = frozenset({TrainingStatus.ENROLLED, TrainingStatus.IN_PROGRESS})


async def add_milestone(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    title: str,
    description: str | None,
    due_on: date | None,
    actor: User,
) -> TrainingMilestone:
    if enrollment.training_status not in _OPEN_TRAINING:
        raise ConflictError("Milestones can be added only while training is enrolled or in progress.")
    milestone = TrainingMilestone(
        organization_id=enrollment.organization_id,
        enrollment_id=enrollment.id,
        title=title.strip(),
        description=description.strip() if description and description.strip() else None,
        due_on=due_on,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(milestone)
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_TRAINING_MILESTONE_ADDED",
        description=f'Training milestone "{milestone.title}" added'
        + (f", due {due_on.isoformat()}." if due_on else "."),
    )
    return milestone


async def update_milestone(
    db: AsyncSession,
    *,
    enrollment: ProgramEnrollment,
    milestone_id: uuid.UUID,
    status: MilestoneStatus,
    completed_on: date | None,
    note: str | None,
    actor: User,
) -> TrainingMilestone:
    milestone = await db.get(TrainingMilestone, milestone_id)
    if milestone is None or milestone.enrollment_id != enrollment.id:
        raise NotFoundError("Milestone not found.")
    if enrollment.training_status not in _OPEN_TRAINING:
        raise ConflictError("Training has ended; its milestones can no longer change.")
    if status == MilestoneStatus.COMPLETED and completed_on is None:
        raise UnprocessableError("Enter the date the milestone was completed.")
    milestone.status = status
    milestone.completed_on = completed_on if status == MilestoneStatus.COMPLETED else None
    if note is not None:
        milestone.note = note.strip() or None
    milestone.updated_by_user_id = actor.id
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_TRAINING_MILESTONE_UPDATED",
        description=f'Training milestone "{milestone.title}" marked {status.value.lower()}.',
    )
    return milestone


# --- Employment progression ---


async def update_employment_review(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    enrollment: ProgramEnrollment,
    status: EmploymentReviewStatus,
    role_title: str | None,
    department_id: uuid.UUID | None,
    proposed_start_date: date | None,
    note: str | None,
    actor: User,
    can_approve: bool,
) -> ProgramEnrollment:
    """IN_REVIEW starts the company's employment decision (after training is
    completed, when the program tracks training). APPROVED / NOT_PROCEEDING
    is the decision itself and needs `program.employment.approve`; approval
    needs the intended role and proposed start date. Never automatic."""
    if not program.tracks_employment:
        raise ConflictError("This program doesn't track employment progression.")
    application = await _application(db, enrollment)
    if application.status not in program_workflow.SELECTED_STATUSES:
        raise ConflictError("Only a selected candidate can be considered for employment.", code="selection_required")
    require_transition(
        program_workflow.EMPLOYMENT_REVIEW_TRANSITIONS, enrollment.employment_review_status, status,
        what="employment review",
    )
    if status == EmploymentReviewStatus.IN_REVIEW and program.tracks_training and (
        enrollment.training_status != TrainingStatus.COMPLETED
    ):
        raise ConflictError(
            "Record the training as completed before starting the employment review.",
            code="training_completion_required",
        )
    if status in (EmploymentReviewStatus.APPROVED, EmploymentReviewStatus.NOT_PROCEEDING):
        if not can_approve:
            raise ForbiddenError("You don't have permission to decide employment progression.")
    if role_title is not None:
        enrollment.employment_role_title = role_title.strip() or None
    if department_id is not None:
        department = await db.get(Department, department_id)
        if department is None or department.organization_id != enrollment.organization_id:
            raise NotFoundError("Department not found.")
        enrollment.employment_department_id = department_id
    if proposed_start_date is not None:
        enrollment.employment_proposed_start_date = proposed_start_date
    if status == EmploymentReviewStatus.APPROVED and (
        not enrollment.employment_role_title or enrollment.employment_proposed_start_date is None
    ):
        raise UnprocessableError("Enter the intended role and proposed start date to approve employment.")
    if status == EmploymentReviewStatus.NOT_PROCEEDING:
        if _blank(note):
            raise UnprocessableError("Give the reason for not proceeding.", code="note_required")
        if enrollment.offer_status in (OfferStatus.OFFERED, OfferStatus.ACCEPTED):
            raise ConflictError("Withdraw the employment offer first.", code="offer_outstanding")
    if status in (EmploymentReviewStatus.APPROVED, EmploymentReviewStatus.NOT_PROCEEDING):
        enrollment.employment_decided_by_user_id = actor.id
        enrollment.employment_decided_at = _now()
        enrollment.employment_decision_note = note.strip() if note and note.strip() else None
    previous = enrollment.employment_review_status
    enrollment.employment_review_status = status
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_EMPLOYMENT_REVIEW_UPDATED",
        description=(
            f"Employment review {previous.value.replace('_', ' ').lower()} -> "
            f"{status.value.replace('_', ' ').lower()}"
            + (
                f" ({enrollment.employment_role_title}, from "
                f"{enrollment.employment_proposed_start_date.isoformat()})."
                if status == EmploymentReviewStatus.APPROVED and enrollment.employment_proposed_start_date
                else "."
            )
        ),
        reason=note.strip() if note and note.strip() else None,
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
    if status == OfferStatus.OFFERED and enrollment.employment_review_status != EmploymentReviewStatus.APPROVED:
        raise ConflictError(
            "Approve the employment review (role and start date) before making an offer.",
            code="employment_approval_required",
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


async def _link_employee(
    db: AsyncSession, *, enrollment: ProgramEnrollment, candidate: Candidate, joined_on: date, actor: User
) -> Employee:
    """The team-hierarchy record for a participant who joined: an existing
    active employee with the same email is linked (never duplicated);
    otherwise one is created from the approved role and department."""
    existing = await db.scalar(
        select(Employee).where(
            Employee.organization_id == enrollment.organization_id,
            func.lower(Employee.email) == candidate.email.strip().lower(),
            Employee.deleted_at.is_(None),
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
    )
    if existing is not None:
        return existing
    try:
        payload = EmployeeCreateRequest(
            full_name=candidate.full_name,
            email=candidate.email,
            designation=enrollment.employment_role_title,
            department_id=enrollment.employment_department_id,
            joining_date=joined_on,
        )
    except ValidationError as exc:
        raise UnprocessableError(
            "The candidate's details can't be used for an employee record "
            "(check their email address), so joining wasn't recorded.",
            code="employee_record_invalid",
        ) from exc
    return await employee_service.create_employee(
        db, organization_id=enrollment.organization_id, actor=actor, payload=payload
    )


async def record_joining(
    db: AsyncSession, *, enrollment: ProgramEnrollment, joined_on: date, actor: User
) -> ProgramEnrollment:
    if enrollment.offer_status != OfferStatus.ACCEPTED:
        raise ConflictError("Record an accepted offer before the joining date.", code="program_offer_required")
    if enrollment.joined_on is not None:
        raise ConflictError("The joining date is already recorded.", code="already_joined")
    application = await _application(db, enrollment)
    enrollment.joined_on = joined_on
    employee = await _link_employee(
        db, enrollment=enrollment, candidate=application.candidate, joined_on=joined_on, actor=actor
    )
    enrollment.employee_id = employee.id
    await db.flush()
    if application.status == ApplicationStatus.SELECTED:
        await application_service.change_status(
            db, application, to_status=ApplicationStatus.HIRED, actor_user_id=actor.id
        )
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_JOINED",
        description=f"Joined full-time employment on {joined_on.isoformat()} (employee record linked).",
    )
    return enrollment


# --- Agreement and bond ---


async def _terms_snapshot(
    db: AsyncSession, program: RecruitmentProgram, enrollment: ProgramEnrollment
) -> dict[str, Any]:
    approved = await _approved_revision(db, enrollment.id)
    if approved is None:
        # Funding approved before funding revisions existed (migration
        # 5e6f70819203): only the approved amount was recorded.
        return {
            "funding_revision": None,
            "currency": enrollment.approved_scholarship_currency,
            "company_contribution": (
                str(enrollment.approved_scholarship_amount)
                if enrollment.approved_scholarship_amount is not None
                else None
            ),
            "bond_duration_months": enrollment.bond_duration_months,
            "program_terms_version": enrollment.terms_version_accepted,
            "training_duration_months": enrollment.training_duration_months,
        }
    return {
        "funding_revision": approved.revision_number,
        "currency": approved.currency,
        "training_fee": str(approved.fee_amount),
        "funding_type": approved.funding_type.value,
        "funding_value": str(approved.funding_value),
        "company_contribution": str(approved.company_contribution_amount),
        "candidate_payable": str(approved.candidate_payable_amount),
        "bond_duration_months": enrollment.bond_duration_months,
        "program_terms_version": enrollment.terms_version_accepted,
        "training_duration_months": enrollment.training_duration_months,
    }


async def update_agreement(
    db: AsyncSession,
    *,
    program: RecruitmentProgram,
    enrollment: ProgramEnrollment,
    status: AgreementStatus,
    reference: str | None,
    version: str | None,
    signed_on: date | None,
    note: str | None,
    actor: User,
) -> ProgramEnrollment:
    """Tracks the company-approved agreement by reference and version — the
    platform never generates or edits agreement wording, and recording
    SIGNED is a record of a signature that happened elsewhere, not an
    electronic signature.

    SENT freezes the terms being agreed (snapshot); SIGNED requires those
    terms to still be the approved ones and records the candidate's
    acceptance of them. A signed agreement ends as EXPIRED (its term has
    run out) or TERMINATED (reason required)."""
    if status != enrollment.agreement_status:
        require_transition(
            program_workflow.AGREEMENT_TRANSITIONS, enrollment.agreement_status, status, what="agreement"
        )
    if reference is not None:
        enrollment.agreement_reference = reference.strip() or None
    if version is not None:
        enrollment.agreement_version = version.strip() or None
    if status == AgreementStatus.SENT and enrollment.agreement_status != AgreementStatus.SENT:
        if enrollment.scholarship_status != ScholarshipStatus.APPROVED:
            raise ConflictError(
                "The agreement applies only to a participant with approved company funding.",
                code="funding_not_approved",
            )
        if not enrollment.agreement_version:
            raise UnprocessableError("Enter the approved agreement version being sent.")
        enrollment.agreement_terms_snapshot = await _terms_snapshot(db, program, enrollment)
        enrollment.agreement_sent_at = _now()
    if status == AgreementStatus.SIGNED and enrollment.agreement_status != AgreementStatus.SIGNED:
        if signed_on is None:
            raise UnprocessableError("Enter the date the agreement was signed.")
        if not enrollment.agreement_reference and not enrollment.agreement_document_storage_path:
            raise UnprocessableError(
                "Record the approved agreement's reference or attach the signed copy first."
            )
        approved = await _approved_revision(db, enrollment.id)
        current = approved.revision_number if approved is not None else None
        # An agreement sent before funding revisions existed has no snapshot;
        # it can be signed only while no revision has been approved since.
        sent_for = (enrollment.agreement_terms_snapshot or {}).get("funding_revision")
        if sent_for != current:
            raise ConflictError(
                "The approved funding terms changed after this agreement was sent. Void it and "
                "send the agreement for the current terms.",
                code="agreement_terms_outdated",
            )
        enrollment.agreement_signed_on = signed_on
        enrollment.funding_terms_accepted_revision = current
        enrollment.funding_terms_accepted_at = datetime.combine(signed_on, time(), tzinfo=UTC)
    if status == AgreementStatus.EXPIRED and status != enrollment.agreement_status:
        expiry = enrollment.agreement_expiry_date or enrollment.bond_end_date
        if expiry is None or expiry > date.today():
            raise ConflictError(
                "An agreement can be marked expired only after its recorded end date.",
                code="agreement_not_expired",
            )
    if status in (AgreementStatus.TERMINATED, AgreementStatus.VOID, AgreementStatus.DECLINED) and (
        status != enrollment.agreement_status
    ):
        if _blank(note):
            raise UnprocessableError("Give the reason for this agreement change.", code="note_required")
    if note and note.strip():
        enrollment.agreement_status_note = note.strip()
    previous = enrollment.agreement_status
    enrollment.agreement_status = status
    await db.flush()
    await _audit(
        db, enrollment=enrollment, actor=actor, action="PROGRAM_AGREEMENT_UPDATED",
        description=(
            f"Agreement {previous.value.replace('_', ' ').lower()} -> {status.value.replace('_', ' ').lower()}"
            + (f" (version {enrollment.agreement_version})." if enrollment.agreement_version else ".")
        ),
        reason=note.strip() if note and note.strip() else None,
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
    the actual start date; the end (and the agreement's effective/expiry
    dates) follow from the duration."""
    if enrollment.scholarship_status != ScholarshipStatus.APPROVED:
        raise ConflictError("A bond applies only to a participant with approved company funding.")
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
    enrollment.agreement_effective_date = start_date
    enrollment.agreement_expiry_date = enrollment.bond_end_date
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

    today = date.today()
    statuses = Counter(
        program_finance.payment_status(
            view.enrollment.candidate_payable_amount, view.fee_paid_total, view.enrollment.fee_due_date, today
        )
        for view in views
    )
    currencies = {v.enrollment.fee_currency for v in views if v.enrollment.fee_currency}
    single_currency = next(iter(currencies)) if len(currencies) == 1 else None

    def total(values: list[Decimal | None]) -> Decimal | None:
        # Money is only summed within one currency.
        if single_currency is None:
            return None
        return sum((v for v in values if v is not None), Decimal("0"))

    return ProgramReport(
        funding_proposed=count(lambda e: e.scholarship_status == ScholarshipStatus.PROPOSED),
        fee_currency=single_currency,
        total_fees=total([v.enrollment.fee_amount for v in views]),
        total_company_contribution=total([v.enrollment.company_contribution_amount for v in views]),
        total_candidate_payable=total([v.enrollment.candidate_payable_amount for v in views]),
        total_fees_paid=total([v.fee_paid_total for v in views]),
        payments_overdue=statuses[program_finance.PaymentStatus.OVERDUE],
        payments_completed=statuses[program_finance.PaymentStatus.PAID]
        + statuses[program_finance.PaymentStatus.FULLY_FUNDED],
        training_withdrawn=count(lambda e: e.training_status == TrainingStatus.WITHDRAWN),
        training_terminated=count(lambda e: e.training_status == TrainingStatus.DISCONTINUED),
        milestones_overdue=sum(
            1
            for view in views
            for m in view.milestones
            if m.status == MilestoneStatus.PENDING and m.due_on is not None and m.due_on < today
        ),
        employment_in_review=count(lambda e: e.employment_review_status == EmploymentReviewStatus.IN_REVIEW),
        employment_approved=count(lambda e: e.employment_review_status == EmploymentReviewStatus.APPROVED),
        employment_not_proceeding=count(
            lambda e: e.employment_review_status == EmploymentReviewStatus.NOT_PROCEEDING
        ),
        affiliation_pending=sum(
            1 for v in views if v.affiliation is not None and v.affiliation.status == AffiliationStatus.PENDING
        ),
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
