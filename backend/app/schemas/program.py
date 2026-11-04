import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.text import collapse_whitespace
from app.models.application import ApplicationStatus
from app.models.assessment import ResultGradingStatus
from app.models.candidate import EducationLevel
from app.models.institution import AffiliationStatus, AffiliationVerificationMethod
from app.models.interview import InterviewOutcome, InterviewStatus
from app.models.program import (
    MAX_DURATION_MONTHS,
    MIN_DURATION_MONTHS,
    AgreementStatus,
    DisbursementStatus,
    EligibilityStatus,
    EmploymentReviewStatus,
    FundingRevisionKind,
    FundingRevisionStatus,
    FundingType,
    InternshipMode,
    InternshipOutcome,
    InternshipStatus,
    MilestoneStatus,
    OfferStatus,
    PaymentMethod,
    PaymentRecordStatus,
    ProgramStatus,
    ScholarshipEligibility,
    ScholarshipStatus,
    TrainingStatus,
)
from app.schemas.institution import InstitutionSummary


def _money() -> Decimal | None:
    return Field(default=None, ge=0, max_digits=12, decimal_places=2)


def _currency(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    code = value.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError("Use a three-letter ISO currency code, e.g. INR.")
    return code


def check_durations(value: list[int] | None) -> list[int] | None:
    """Training-duration options in months: any whole number in the
    configured bounds — which ones a program offers is HR configuration."""
    if value is None:
        return None
    if any(not MIN_DURATION_MONTHS <= v <= MAX_DURATION_MONTHS for v in value):
        raise ValueError(
            f"Each duration must be between {MIN_DURATION_MONTHS} and {MAX_DURATION_MONTHS} months."
        )
    return sorted(set(value))


def check_study_year_rules(value: dict[str, list[int]] | None) -> dict[str, list[int]] | None:
    """{"MTECH": [2], "PHD": []}: keys are education levels, values the
    eligible study years for that level (empty = any year)."""
    if value is None:
        return None
    cleaned: dict[str, list[int]] = {}
    for level, years in value.items():
        try:
            EducationLevel(level)
        except ValueError as exc:
            raise ValueError(f"Unknown education level in study-year rules: {level}.") from exc
        if any(not 1 <= y <= 10 for y in years):
            raise ValueError("Study years must be between 1 and 10.")
        cleaned[level] = sorted(set(years))
    return cleaned


def _clean_list(values: list[str] | None, *, limit: int) -> list[str] | None:
    if values is None:
        return None
    cleaned: list[str] = []
    for value in values:
        item = collapse_whitespace(value)[:limit]
        if item and item.lower() not in {c.lower() for c in cleaned}:
            cleaned.append(item)
    return cleaned


class _ProgramFields(BaseModel):
    """Every field is optional so a draft can be saved incomplete; what a
    *published* program must have is checked by
    program_service.publication_problems, not here."""

    eligible_education_levels: list[EducationLevel] | None = Field(default=None, max_length=10)
    eligible_study_years: list[int] | None = Field(default=None, max_length=10)
    eligible_specializations: list[str] | None = Field(default=None, max_length=100)
    eligible_institution_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)
    expected_completion_from: date | None = None
    expected_completion_to: date | None = None
    eligibility_notes: str | None = Field(default=None, max_length=5000)
    study_year_rules: dict[str, list[int]] | None = None
    minimum_cgpa: Decimal | None = Field(default=None, ge=0, le=10, max_digits=4, decimal_places=2)
    minimum_percentage: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2
    )

    training_duration_options: list[int] | None = Field(default=None, max_length=12)
    training_fee_amount: Decimal | None = _money()
    training_fee_currency: str | None = None
    fee_payment_terms: str | None = Field(default=None, max_length=5000)
    fee_disclosure: str | None = Field(default=None, max_length=5000)

    scholarship_amount_min: Decimal | None = _money()
    scholarship_amount_max: Decimal | None = _money()
    scholarship_currency: str | None = None
    scholarship_eligibility_rules: str | None = Field(default=None, max_length=5000)
    scholarship_disbursement_timing: str | None = Field(default=None, max_length=5000)
    scholarship_disclosure: str | None = Field(default=None, max_length=5000)

    employment_pathway: str | None = Field(default=None, max_length=5000)
    employment_job_id: uuid.UUID | None = None
    bond_required: bool | None = None
    bond_duration_months: int | None = Field(default=None, ge=1, le=120)
    bond_terms_summary: str | None = Field(default=None, max_length=10000)
    bond_start_rule: str | None = Field(default=None, max_length=5000)

    application_opens_at: datetime | None = None
    application_closes_at: datetime | None = None
    assessment_id: uuid.UUID | None = None
    interview_stages: list[str] | None = Field(default=None, max_length=10)
    required_documents: list[str] | None = Field(default=None, max_length=20)
    candidate_disclosures: str | None = Field(default=None, max_length=20000)
    terms_version: str | None = Field(default=None, max_length=50)
    requires_interview: bool | None = None
    capacity: int | None = Field(default=None, ge=1, le=100000)
    max_applications: int | None = Field(default=None, ge=1, le=1000000)

    tracks_fee_payments: bool | None = None
    tracks_training: bool | None = None
    tracks_employment: bool | None = None
    requires_agreement: bool | None = None
    tracks_internship: bool | None = None
    internship_duration_months: int | None = Field(
        default=None, ge=MIN_DURATION_MONTHS, le=MAX_DURATION_MONTHS
    )
    internship_stipend_amount: Decimal | None = _money()
    internship_stipend_currency: str | None = None
    internship_terms: str | None = Field(default=None, max_length=5000)
    internship_required_for_employment: bool | None = None

    company_funding_type: FundingType | None = None
    #: 0 = the program's standard is no company funding.
    company_funding_value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    funding_requires_independent_approval: bool | None = None
    affiliation_verification_method: AffiliationVerificationMethod | None = None

    @field_validator("affiliation_verification_method")
    @classmethod
    def _program_method(
        cls, value: AffiliationVerificationMethod | None
    ) -> AffiliationVerificationMethod | None:
        # Programs have no college-provided student list, and the evidence
        # methods are recorded per decision, not configured.
        if value is not None and value not in (
            AffiliationVerificationMethod.NONE,
            AffiliationVerificationMethod.EMAIL_DOMAIN,
            AffiliationVerificationMethod.MANUAL,
        ):
            raise ValueError("Choose not required, institution email domain, or manual review.")
        return value

    @field_validator("training_fee_currency", "scholarship_currency", "internship_stipend_currency")
    @classmethod
    def _check_currency(cls, value: str | None) -> str | None:
        return _currency(value)

    @field_validator("training_duration_options")
    @classmethod
    def _check_durations(cls, value: list[int] | None) -> list[int] | None:
        return check_durations(value)

    @field_validator("study_year_rules")
    @classmethod
    def _check_study_year_rules(
        cls, value: dict[str, list[int]] | None
    ) -> dict[str, list[int]] | None:
        return check_study_year_rules(value)

    @field_validator("eligible_study_years")
    @classmethod
    def _check_years(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if any(not 1 <= v <= 10 for v in value):
            raise ValueError("Study years must be between 1 and 10.")
        return sorted(set(value))

    @field_validator("eligible_specializations")
    @classmethod
    def _clean_specializations(cls, value: list[str] | None) -> list[str] | None:
        return _clean_list(value, limit=150)

    @field_validator("interview_stages", "required_documents")
    @classmethod
    def _clean_names(cls, value: list[str] | None) -> list[str] | None:
        return _clean_list(value, limit=150)

    @field_validator("terms_version")
    @classmethod
    def _clean_version(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def _ranges(self) -> "_ProgramFields":
        if (
            self.expected_completion_from
            and self.expected_completion_to
            and self.expected_completion_from > self.expected_completion_to
        ):
            raise ValueError("The expected-completion window ends before it starts.")
        if (
            self.scholarship_amount_min is not None
            and self.scholarship_amount_max is not None
            and self.scholarship_amount_max < self.scholarship_amount_min
        ):
            raise ValueError("The maximum scholarship amount is below the minimum.")
        if (
            self.application_opens_at
            and self.application_closes_at
            and self.application_closes_at <= self.application_opens_at
        ):
            raise ValueError("Applications must close after they open.")
        if self.internship_stipend_amount is not None and not self.internship_stipend_currency:
            raise ValueError("Enter the currency of the internship stipend.")
        return self


class ProgramCreateRequest(_ProgramFields):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=20000)
    #: Start from the organization's default program settings for every
    #: value not given here (docs/programs.md § 1a).
    apply_organization_defaults: bool = True


class ProgramUpdateRequest(_ProgramFields):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, min_length=1, max_length=20000)


class ProgramStatusRequest(BaseModel):
    status: ProgramStatus


class ProgramResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    status: ProgramStatus
    job_id: uuid.UUID
    eligible_education_levels: list[EducationLevel]
    eligible_study_years: list[int]
    eligible_specializations: list[str]
    eligible_institutions: list[InstitutionSummary]
    expected_completion_from: date | None
    expected_completion_to: date | None
    eligibility_notes: str | None
    study_year_rules: dict[str, list[int]] = {}
    minimum_cgpa: Decimal | None = None
    minimum_percentage: Decimal | None = None
    training_duration_options: list[int]
    training_fee_amount: Decimal | None
    training_fee_currency: str | None
    fee_payment_terms: str | None
    fee_disclosure: str | None
    scholarship_amount_min: Decimal | None
    scholarship_amount_max: Decimal | None
    scholarship_currency: str | None
    scholarship_eligibility_rules: str | None
    scholarship_disbursement_timing: str | None
    scholarship_disclosure: str | None
    employment_pathway: str | None
    employment_job_id: uuid.UUID | None = None
    employment_job_title: str | None = None
    bond_required: bool = True
    bond_duration_months: int | None
    bond_terms_summary: str | None
    bond_start_rule: str | None
    application_opens_at: datetime | None
    application_closes_at: datetime | None
    assessment_id: uuid.UUID | None
    assessment_title: str | None = None
    interview_stages: list[str]
    required_documents: list[str]
    candidate_disclosures: str | None
    terms_version: str | None
    requires_interview: bool = True
    capacity: int | None = None
    max_applications: int | None = None
    tracks_fee_payments: bool
    tracks_training: bool
    tracks_employment: bool
    requires_agreement: bool
    tracks_internship: bool = False
    internship_duration_months: int | None = None
    internship_stipend_amount: Decimal | None = None
    internship_stipend_currency: str | None = None
    internship_terms: str | None = None
    internship_required_for_employment: bool = False
    company_funding_type: FundingType | None = None
    company_funding_value: Decimal | None = None
    funding_requires_independent_approval: bool = True
    affiliation_verification_method: AffiliationVerificationMethod = AffiliationVerificationMethod.NONE
    #: The latest recorded configuration version (None = never published).
    current_version: int | None = None
    #: Candidates currently selected (SELECTED/HIRED), against `capacity`.
    selected_count: int = 0
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    #: What still blocks publishing (empty = ready / already published).
    publication_problems: list[str] = []
    enrollment_count: int = 0


class ProgramSummary(BaseModel):
    id: uuid.UUID
    title: str
    status: ProgramStatus
    training_duration_options: list[int]
    application_opens_at: datetime | None
    application_closes_at: datetime | None
    enrollment_count: int
    capacity: int | None = None
    tracks_internship: bool = False
    created_at: datetime


# --- Candidate-facing ---


class PublicProgramSummary(BaseModel):
    id: uuid.UUID
    title: str
    training_duration_options: list[int]
    eligible_education_levels: list[EducationLevel]
    application_closes_at: datetime | None
    accepting_applications: bool


class PublicProgramView(BaseModel):
    """Only business-approved, finalized information: an amount appears
    only if HR set it; otherwise only HR's approved disclosure wording."""

    kind: Literal["program"] = "program"
    id: uuid.UUID
    title: str
    description: str
    organization_name: str
    careers_contact_email: str | None
    eligible_education_levels: list[EducationLevel]
    eligible_study_years: list[int]
    eligible_specializations: list[str]
    eligible_institutions: list[InstitutionSummary]
    expected_completion_from: date | None
    expected_completion_to: date | None
    eligibility_notes: str | None
    study_year_rules: dict[str, list[int]] = {}
    minimum_cgpa: Decimal | None = None
    minimum_percentage: Decimal | None = None
    training_duration_options: list[int]
    training_fee_amount: Decimal | None
    training_fee_currency: str | None
    fee_payment_terms: str | None
    fee_disclosure: str | None
    scholarship_amount_min: Decimal | None
    scholarship_amount_max: Decimal | None
    scholarship_currency: str | None
    scholarship_eligibility_rules: str | None
    scholarship_disbursement_timing: str | None
    scholarship_disclosure: str | None
    employment_pathway: str | None
    bond_required: bool = True
    bond_duration_months: int | None
    bond_terms_summary: str | None
    tracks_internship: bool = False
    internship_duration_months: int | None = None
    internship_stipend_amount: Decimal | None = None
    internship_stipend_currency: str | None = None
    internship_terms: str | None = None
    company_funding_type: FundingType | None = None
    company_funding_value: Decimal | None = None
    has_assessment: bool
    interview_stages: list[str]
    required_documents: list[str]
    candidate_disclosures: str
    terms_version: str
    #: The exact text the candidate agrees to (stored with the application).
    disclosure_text: str
    application_opens_at: datetime | None
    application_closes_at: datetime | None
    accepting_applications: bool
    not_accepting_reason: str | None = None


class PublicProgramUnavailable(BaseModel):
    kind: Literal["unavailable"] = "unavailable"
    message: str = "This program is no longer accepting applications."


class ProgramEducationDeclaration(BaseModel):
    """The program-specific part of an application: the candidate's
    current education (self-declared), and their agreement to the terms."""

    education_level: EducationLevel
    study_year: int | None = Field(default=None, ge=1, le=10)
    specialization: str = Field(min_length=1, max_length=150)
    #: A registered institution, when the program lists them (required
    #: then); otherwise `institution_name`.
    institution_id: uuid.UUID | None = None
    institution_name: str | None = Field(default=None, max_length=255)
    expected_completion_date: date | None = None
    #: Declared academic standing (10-point CGPA and/or percentage), checked
    #: against the program's minimums when it sets them.
    cgpa: Decimal | None = Field(default=None, ge=0, le=10, max_digits=4, decimal_places=2)
    percentage_score: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2
    )
    terms_version: str = Field(min_length=1, max_length=50)
    terms_accepted: bool

    @field_validator("specialization", "institution_name")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return collapse_whitespace(value) or None

    @model_validator(mode="after")
    def _accepted(self) -> "ProgramEducationDeclaration":
        if not self.terms_accepted:
            raise ValueError("You need to accept the program terms to apply.")
        if self.institution_id is None and not self.institution_name:
            raise ValueError("Tell us which institution you study at.")
        return self


class PublicProgramApplicationResult(BaseModel):
    id: uuid.UUID
    program_title: str
    candidate_email: str
    confirmation_email_sent: bool
    careers_contact_email: str | None
    submitted_at: datetime


# --- Enrollments (recruiter) ---


class FeePaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    amount: Decimal
    currency: str
    paid_on: date
    method: PaymentMethod
    reference: str | None
    note: str | None
    status: PaymentRecordStatus
    void_reason: str | None
    voided_at: datetime | None
    created_at: datetime


class DisbursementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    amount: Decimal
    currency: str
    status: DisbursementStatus
    scheduled_on: date | None
    paid_on: date | None
    reference: str | None
    note: str | None
    created_at: datetime
    updated_at: datetime


class FundingRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    revision_number: int
    kind: FundingRevisionKind
    status: FundingRevisionStatus
    fee_amount: Decimal
    currency: str
    funding_type: FundingType
    funding_value: Decimal
    company_contribution_amount: Decimal
    candidate_payable_amount: Decimal
    reason: str | None
    proposed_by_user_id: uuid.UUID | None
    decided_by_user_id: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime


class MilestoneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    due_on: date | None
    status: MilestoneStatus
    completed_on: date | None
    note: str | None
    overdue: bool = False
    created_at: datetime


class AffiliationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: AffiliationStatus
    method: AffiliationVerificationMethod | None
    institution_id: uuid.UUID | None
    reviewed_by_user_id: uuid.UUID | None
    reviewed_at: datetime | None
    review_note: str | None


class InterviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    stage: str
    scheduled_at: datetime
    duration_minutes: int | None
    location: str | None
    interviewer_user_id: uuid.UUID | None
    interviewer_name: str | None = None
    status: InterviewStatus
    outcome: InterviewOutcome | None
    rating: int | None
    #: Withheld (None, `feedback_restricted` True) from users who may not
    #: read it.
    feedback: str | None
    feedback_restricted: bool = False
    cancellation_reason: str | None
    recorded_at: datetime | None
    created_at: datetime


class InterviewScheduleRequest(BaseModel):
    stage: str = Field(min_length=1, max_length=150)
    #: Must carry a timezone (e.g. ...Z) so it is stored as the right instant.
    scheduled_at: AwareDatetime
    duration_minutes: int | None = Field(default=None, ge=5, le=600)
    location: str | None = Field(default=None, max_length=500)
    interviewer_user_id: uuid.UUID | None = None


class InterviewOutcomeRequest(BaseModel):
    outcome: InterviewOutcome
    rating: int | None = Field(default=None, ge=1, le=5)
    feedback: str | None = Field(default=None, max_length=10000)


class InterviewListResponse(BaseModel):
    items: list[InterviewResponse]
    #: The viewer's interview permission codes, so the UI offers only
    #: actions the server accepts (it still re-checks every one).
    viewer_permissions: list[str]


class InterviewCloseRequest(BaseModel):
    status: Literal[InterviewStatus.CANCELLED, InterviewStatus.NO_SHOW]
    reason: str | None = Field(default=None, max_length=2000)


class InternshipEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event: str
    from_status: str | None
    to_status: str
    note: str | None
    details: dict[str, object]
    actor_user_id: uuid.UUID | None
    created_at: datetime


class InternshipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: InternshipStatus
    mode: InternshipMode | None
    role_title: str | None
    duration_months: int
    start_date: date | None
    end_date: date | None
    original_end_date: date | None
    extension_count: int
    #: Withheld (None) from users without program.finance.manage.
    stipend_amount: Decimal | None
    stipend_currency: str | None
    supervisor_employee_id: uuid.UUID | None
    supervisor_name: str | None = None
    completed_on: date | None
    ended_on: date | None
    outcome: InternshipOutcome | None
    evaluation_rating: int | None
    evaluation_note: str | None
    end_reason: str | None
    events: list[InternshipEventResponse] = []
    created_at: datetime


class InternshipPlanRequest(BaseModel):
    """Plans the internship, or edits one that hasn't started. The stipend
    comes from the program; only finance users may set a different one."""

    duration_months: int | None = Field(default=None, ge=MIN_DURATION_MONTHS, le=MAX_DURATION_MONTHS)
    start_date: date | None = None
    mode: InternshipMode | None = None
    role_title: str | None = Field(default=None, max_length=255)
    supervisor_employee_id: uuid.UUID | None = None
    stipend_amount: Decimal | None = _money()
    stipend_currency: str | None = None

    @field_validator("stipend_currency")
    @classmethod
    def _check_currency(cls, value: str | None) -> str | None:
        return _currency(value)


class InternshipStatusRequest(BaseModel):
    """ACTIVE (start), COMPLETED (with the evaluation), TERMINATED or
    WITHDRAWN (with a reason)."""

    status: Literal[
        InternshipStatus.ACTIVE,
        InternshipStatus.COMPLETED,
        InternshipStatus.TERMINATED,
        InternshipStatus.WITHDRAWN,
    ]
    on_date: date | None = None
    outcome: InternshipOutcome | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    note: str | None = Field(default=None, max_length=2000)


class InternshipExtensionRequest(BaseModel):
    new_end_date: date
    reason: str = Field(min_length=1, max_length=2000)


class ProgramVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_number: int
    event: str
    changed_fields: list[str]
    snapshot: dict[str, object]
    created_by_user_id: uuid.UUID | None
    created_by_name: str | None = None
    created_at: datetime


class FundingPreviewRequest(BaseModel):
    """Asks the authoritative fee split (app/services/program_finance.py)."""

    fee: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    funding_type: FundingType | None = None
    value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class FundingPreviewResponse(BaseModel):
    fee: Decimal
    company_contribution: Decimal
    candidate_payable: Decimal


class EnrollmentResultSummary(BaseModel):
    """Staff-only test outcome (never on a candidate-facing schema)."""

    percentage: int
    passed: bool | None
    grading_status: ResultGradingStatus
    attempt_number: int
    pass_score_applied: int | None = None


class EnrollmentResponse(BaseModel):
    id: uuid.UUID
    program_id: uuid.UUID
    application_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_full_name: str
    candidate_email: str
    application_status: ApplicationStatus
    stage: str
    applied_at: datetime
    education_level: EducationLevel
    study_year: int | None
    specialization: str
    institution_id: uuid.UUID | None
    institution_name: str
    expected_completion_date: date | None
    cgpa: Decimal | None = None
    percentage_score: Decimal | None = None
    program_version_number: int | None = None
    terms_version_accepted: str
    terms_accepted_at: datetime
    eligibility_status: EligibilityStatus
    eligibility_reviewed_at: datetime | None
    eligibility_note: str | None
    latest_result: EnrollmentResultSummary | None
    interview_completed_at: datetime | None
    scholarship_eligibility: ScholarshipEligibility
    scholarship_eligibility_decided_at: datetime | None
    scholarship_status: ScholarshipStatus
    approved_scholarship_amount: Decimal | None
    approved_scholarship_currency: str | None
    scholarship_decided_at: datetime | None
    scholarship_decision_note: str | None
    affiliation: AffiliationSummary | None = None
    training_status: TrainingStatus
    training_duration_months: int | None
    training_start_date: date | None
    training_end_date: date | None
    training_enrolled_at: datetime | None = None
    training_completed_on: date | None = None
    training_outcome_note: str | None = None
    milestones: list[MilestoneResponse] = []
    milestones_overdue: int = 0
    next_milestone_due: date | None = None
    employment_review_status: EmploymentReviewStatus = EmploymentReviewStatus.NOT_STARTED
    employment_role_title: str | None = None
    employment_department_id: uuid.UUID | None = None
    employment_proposed_start_date: date | None = None
    employment_decided_at: datetime | None = None
    employment_decision_note: str | None = None
    employee_id: uuid.UUID | None = None
    offer_status: OfferStatus
    offer_made_at: datetime | None
    joined_on: date | None
    agreement_status: AgreementStatus
    agreement_reference: str | None
    agreement_version: str | None = None
    agreement_terms_snapshot: dict[str, object] | None = None
    #: False when the approved funding changed after the agreement was sent.
    agreement_terms_current: bool | None = None
    agreement_sent_at: datetime | None = None
    agreement_signed_on: date | None
    agreement_effective_date: date | None = None
    agreement_expiry_date: date | None = None
    agreement_status_note: str | None = None
    agreement_document_filename: str | None
    agreement_document_uploaded_at: datetime | None
    bond_duration_months: int | None
    bond_start_date: date | None
    bond_end_date: date | None
    bond_applies_reason: str | None = None
    internship: InternshipResponse | None = None
    interviews: list[InterviewResponse] = []
    #: The program/interview permission codes the *viewer* holds, so the UI
    #: offers only actions the server will accept (it still re-checks).
    viewer_permissions: list[str] = []
    # Finance: left empty / None for users without program.finance.manage.
    finance_visible: bool = False
    fee_amount: Decimal | None = None
    fee_currency: str | None = None
    fee_due_date: date | None = None
    company_contribution_amount: Decimal | None = None
    candidate_payable_amount: Decimal | None = None
    balance_due: Decimal | None = None
    payment_status: str | None = None
    funding_terms_accepted_revision: int | None = None
    funding_terms_accepted_at: datetime | None = None
    fee_paid_total: Decimal = Decimal("0")
    disbursed_total: Decimal = Decimal("0")
    fee_payments: list[FeePaymentResponse] = []
    disbursements: list[DisbursementResponse] = []
    funding_revisions: list[FundingRevisionResponse] = []


class EligibilityReviewRequest(BaseModel):
    status: Literal[EligibilityStatus.ELIGIBLE, EligibilityStatus.NOT_ELIGIBLE]
    note: str | None = Field(default=None, max_length=2000)


class ScholarshipEligibilityRequest(BaseModel):
    decision: Literal[ScholarshipEligibility.ELIGIBLE, ScholarshipEligibility.NOT_ELIGIBLE]
    note: str | None = Field(default=None, max_length=2000)


class AffiliationReviewRequest(BaseModel):
    status: AffiliationStatus
    #: The evidence checked, for VERIFIED.
    method: AffiliationVerificationMethod | None = None
    note: str | None = Field(default=None, max_length=2000)


class FundingProposalRequest(BaseModel):
    funding_type: FundingType
    value: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _percentage(self) -> "FundingProposalRequest":
        if self.funding_type == FundingType.PERCENTAGE and self.value > 100:
            raise ValueError("A funding percentage can't be more than 100%.")
        return self


class FundingDecisionRequest(BaseModel):
    decision: Literal[FundingRevisionStatus.APPROVED, FundingRevisionStatus.REJECTED]
    note: str | None = Field(default=None, max_length=2000)


class FundingWithdrawRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class FundingAcceptanceRequest(BaseModel):
    accepted_on: date


class FeeObligationRequest(BaseModel):
    due_date: date | None = None


class MilestoneCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    due_on: date | None = None


class MilestoneUpdateRequest(BaseModel):
    status: MilestoneStatus
    completed_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)


class EmploymentReviewRequest(BaseModel):
    status: EmploymentReviewStatus
    role_title: str | None = Field(default=None, max_length=255)
    department_id: uuid.UUID | None = None
    proposed_start_date: date | None = None
    note: str | None = Field(default=None, max_length=2000)


class DisbursementCreateRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    status: Literal[DisbursementStatus.SCHEDULED, DisbursementStatus.PAID] = DisbursementStatus.SCHEDULED
    scheduled_on: date | None = None
    paid_on: date | None = None
    reference: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _dates(self) -> "DisbursementCreateRequest":
        if self.status == DisbursementStatus.PAID and self.paid_on is None:
            raise ValueError("Enter the date the disbursement was paid.")
        return self


class DisbursementUpdateRequest(BaseModel):
    status: Literal[DisbursementStatus.PAID, DisbursementStatus.CANCELLED]
    paid_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _dates(self) -> "DisbursementUpdateRequest":
        if self.status == DisbursementStatus.PAID and self.paid_on is None:
            raise ValueError("Enter the date the disbursement was paid.")
        if self.status == DisbursementStatus.CANCELLED and not (self.note and self.note.strip()):
            raise ValueError("Give a reason for cancelling the disbursement.")
        return self


class FeePaymentCreateRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str
    paid_on: date
    method: PaymentMethod
    #: The receipt / transaction id from finance — never card or account
    #: numbers.
    reference: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, value: str) -> str:
        code = _currency(value)
        if code is None:
            raise ValueError("Currency is required.")
        return code


class FeePaymentVoidRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class TrainingUpdateRequest(BaseModel):
    status: TrainingStatus
    duration_months: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    completed_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("duration_months")
    @classmethod
    def _in_range(cls, value: int | None) -> int | None:
        if value is not None:
            check_durations([value])
        return value


class OfferUpdateRequest(BaseModel):
    status: OfferStatus
    note: str | None = Field(default=None, max_length=2000)


class JoiningRequest(BaseModel):
    joined_on: date


class AgreementUpdateRequest(BaseModel):
    status: AgreementStatus
    reference: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=100)
    signed_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)


class BondUpdateRequest(BaseModel):
    start_date: date
    duration_months: int | None = Field(default=None, ge=1, le=120)


class ProgramReport(BaseModel):
    """Every count is computed live from this program's enrollments and
    their applications (CLAUDE.md § 2: "Reports != hardcoded numbers")."""

    applications: int
    eligibility_pending: int
    eligible: int
    not_eligible: int
    assessment_invited: int
    assessment_passed: int
    assessment_failed: int
    assessment_pending_review: int
    interview: int
    interview_completed: int
    selected: int
    not_selected: int
    scholarship_eligible: int
    scholarship_pending: int
    scholarship_approved: int
    scholarship_declined: int
    training_enrolled: int
    training_in_progress: int
    training_completed: int
    offers_made: int
    offers_accepted: int
    joined: int
    agreements_signed: int
    affiliation_pending: int = 0
    funding_proposed: int = 0
    #: Money totals only when every fixed fee is in one currency.
    fee_currency: str | None = None
    total_fees: Decimal | None = None
    total_company_contribution: Decimal | None = None
    total_candidate_payable: Decimal | None = None
    total_fees_paid: Decimal | None = None
    payments_overdue: int = 0
    payments_completed: int = 0
    training_withdrawn: int = 0
    training_terminated: int = 0
    milestones_overdue: int = 0
    employment_in_review: int = 0
    employment_approved: int = 0
    employment_not_proceeding: int = 0
    internship_planned: int = 0
    internship_active: int = 0
    internship_completed: int = 0
    internship_qualified: int = 0
    internship_ended_early: int = 0
