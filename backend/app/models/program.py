"""Recruitment programs — e.g. the MTech/PhD Scholarship & Paid Training
Program (docs/programs.md).

A `RecruitmentProgram` composes an ordinary `Job` (created with it, never
shown on the careers job list) exactly the way a `CampusDrive` does, so every
program application is an ordinary `Application` and goes through the same
candidate identity rules, AI screening, assessment, interview and reporting
machinery as any other. What is genuinely program-specific — eligibility
review, the interview-completion gate, scholarship eligibility vs approval
vs disbursement, training, fee payments, the employment offer, the agreement
and the bond — lives on one `ProgramEnrollment` per application, with every
state governed by app/workflows/program_workflow.py.

Business values the company has not finalized (training fee, scholarship
amount, disbursement timing, bond start rule, agreement wording) are all
nullable: nothing here invents them. A program can only be published once
HR has supplied either the value or business-approved disclosure wording
for each (app/services/program_service.py::publication_problems).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.assessment import Assessment
from app.models.candidate import EducationLevel
from app.models.institution import AffiliationVerificationMethod, Institution
from app.models.job import Job

#: The only training durations the business has approved (months).
APPROVED_TRAINING_DURATIONS: frozenset[int] = frozenset({6, 12})
#: The approved default commitment for scholarship recipients (months);
#: HR can change it per program, it is never applied silently.
DEFAULT_BOND_DURATION_MONTHS = 36


class FundingType(StrEnum):
    """How company funding (the scholarship) is expressed: a fixed amount,
    or a percentage of the candidate's training fee. The value itself is a
    business decision HR configures — the platform has no default."""

    FIXED_AMOUNT = "FIXED_AMOUNT"
    PERCENTAGE = "PERCENTAGE"


class ProgramStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"
    ARCHIVED = "ARCHIVED"


class RecruitmentProgram(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "recruitment_programs"
    __table_args__ = (
        CheckConstraint(
            "training_duration_options <@ ARRAY[6, 12]",
            name="ck_recruitment_programs_training_durations_approved",
        ),
        CheckConstraint(
            "training_fee_amount IS NULL OR training_fee_amount >= 0",
            name="ck_recruitment_programs_fee_non_negative",
        ),
        CheckConstraint(
            "scholarship_amount_min IS NULL OR scholarship_amount_min >= 0",
            name="ck_recruitment_programs_scholarship_min_non_negative",
        ),
        CheckConstraint(
            "scholarship_amount_max IS NULL OR scholarship_amount_min IS NULL "
            "OR scholarship_amount_max >= scholarship_amount_min",
            name="ck_recruitment_programs_scholarship_range",
        ),
        CheckConstraint(
            "bond_duration_months IS NULL OR bond_duration_months > 0",
            name="ck_recruitment_programs_bond_duration_positive",
        ),
        CheckConstraint(
            "(company_funding_type IS NULL) = (company_funding_value IS NULL)",
            name="ck_recruitment_programs_funding_complete",
        ),
        CheckConstraint(
            "company_funding_value IS NULL OR (company_funding_value > 0 AND "
            "(company_funding_type <> 'PERCENTAGE' OR company_funding_value <= 100))",
            name="ck_recruitment_programs_funding_value_valid",
        ),
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ProgramStatus] = mapped_column(
        Enum(ProgramStatus, name="program_status", native_enum=True),
        nullable=False,
        default=ProgramStatus.DRAFT,
        server_default=ProgramStatus.DRAFT.value,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )

    # --- Eligibility (empty list = no restriction on that dimension) ---
    eligible_education_levels: Mapped[list[str]] = mapped_column(
        ARRAY(String(30)), nullable=False, default=list, server_default="{}"
    )
    eligible_study_years: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, default=list, server_default="{}"
    )
    eligible_specializations: Mapped[list[str]] = mapped_column(
        ARRAY(String(150)), nullable=False, default=list, server_default="{}"
    )
    expected_completion_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_completion_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    eligibility_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Training ---
    training_duration_options: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, default=list, server_default="{}"
    )
    training_fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    training_fee_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    fee_payment_terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Business-approved wording shown instead of an amount that isn't final.
    fee_disclosure: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Scholarship ---
    scholarship_amount_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    scholarship_amount_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    scholarship_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    scholarship_eligibility_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    scholarship_disbursement_timing: Mapped[str | None] = mapped_column(Text, nullable=True)
    scholarship_disclosure: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The program's standard company-funding terms, pre-filled into each
    #: proposal (a proposal may differ, within the scholarship range). NULL
    #: until the business decides — never defaulted by the platform.
    company_funding_type: Mapped[FundingType | None] = mapped_column(
        Enum(FundingType, name="program_funding_type", native_enum=True), nullable=True
    )
    company_funding_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: Separation of duties: whoever approves a funding proposal must not be
    #: the person who proposed it.
    funding_requires_independent_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # --- Employment pathway and commitment ---
    employment_pathway: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Optional: the ordinary FULL_TIME job graduates are intended to join.
    #: A reference only — enrollment never creates an application on it, and
    #: the bond stays a program term, never a property of that job.
    employment_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    bond_duration_months: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=DEFAULT_BOND_DURATION_MONTHS
    )
    bond_terms_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    bond_start_rule: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Application window, selection and disclosures ---
    application_opens_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    application_closes_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessments.id", ondelete="RESTRICT"), nullable=True
    )
    interview_stages: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    required_documents: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    candidate_disclosures: Mapped[str | None] = mapped_column(Text, nullable=True)
    terms_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: How an applicant's membership of their institution is checked
    #: (docs/programs.md § 3a). NONE = not required, never "verified".
    #: Programs that existed before this column keep NONE (server default);
    #: new ones default to a manual check.
    affiliation_verification_method: Mapped[AffiliationVerificationMethod] = mapped_column(
        Enum(
            AffiliationVerificationMethod,
            name="affiliation_verification_method",
            native_enum=True,
            create_type=False,
        ),
        nullable=False,
        default=AffiliationVerificationMethod.MANUAL,
        server_default=AffiliationVerificationMethod.NONE.value,
    )

    # --- Which optional stages this program tracks ---
    tracks_fee_payments: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    tracks_training: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    tracks_employment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    requires_agreement: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    job: Mapped[Job] = relationship(Job, lazy="raise", viewonly=True, foreign_keys=[job_id])
    employment_job: Mapped[Job | None] = relationship(
        Job, lazy="raise", viewonly=True, foreign_keys=[employment_job_id]
    )
    assessment: Mapped[Assessment | None] = relationship(Assessment, lazy="raise", viewonly=True)
    eligible_institutions: Mapped[list[Institution]] = relationship(
        Institution,
        secondary="recruitment_program_institutions",
        lazy="raise",
        viewonly=True,
        order_by="Institution.name",
    )


class RecruitmentProgramInstitution(Base):
    """A program restricted to specific institutions has one row per
    institution; a program with no rows accepts any institution."""

    __tablename__ = "recruitment_program_institutions"

    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recruitment_programs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    institution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("institutions.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )


class EligibilityStatus(StrEnum):
    PENDING = "PENDING"
    ELIGIBLE = "ELIGIBLE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class ScholarshipEligibility(StrEnum):
    """Whether the candidate *may be considered* for the scholarship —
    decided only after selection. Never the same thing as approval."""

    NOT_DETERMINED = "NOT_DETERMINED"
    ELIGIBLE = "ELIGIBLE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class ScholarshipStatus(StrEnum):
    """The scholarship *decision*. APPROVED means awarded, not paid —
    payment is tracked separately as disbursements."""

    NOT_STARTED = "NOT_STARTED"
    #: Eligible for consideration; no funding terms proposed yet.
    PENDING = "PENDING"
    #: Terms proposed (a `ProgramFundingRevision` in PROPOSED), awaiting an
    #: authorized approver.
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"


class TrainingStatus(StrEnum):
    NOT_ENROLLED = "NOT_ENROLLED"
    ENROLLED = "ENROLLED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    #: The company ended the training (terminated).
    DISCONTINUED = "DISCONTINUED"
    #: The candidate left the training.
    WITHDRAWN = "WITHDRAWN"


class OfferStatus(StrEnum):
    NOT_OFFERED = "NOT_OFFERED"
    OFFERED = "OFFERED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    WITHDRAWN = "WITHDRAWN"


class AgreementStatus(StrEnum):
    """NOT_REQUIRED -> PENDING (being prepared) -> SENT (awaiting the
    candidate's acceptance) -> SIGNED (accepted) / DECLINED. VOID cancels an
    unsigned agreement; a signed one ends as EXPIRED (its term ran out) or
    TERMINATED (ended early, reason recorded)."""

    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    SENT = "SENT"
    SIGNED = "SIGNED"
    DECLINED = "DECLINED"
    VOID = "VOID"
    EXPIRED = "EXPIRED"
    TERMINATED = "TERMINATED"


class EmploymentReviewStatus(StrEnum):
    """The company's decision whether to progress a participant to
    full-time employment — separate from, and required before, an offer."""

    NOT_STARTED = "NOT_STARTED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    NOT_PROCEEDING = "NOT_PROCEEDING"


class ProgramEnrollment(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One candidate's participation in one program — the program-specific
    workflow state for exactly one `Application` (selection itself stays on
    `Application.status`, the platform-wide pipeline).

    The education fields are the declaration *as submitted with this
    application* (the candidate profile keeps the latest copy), and the
    disclosure text/terms version are exactly what the candidate agreed to.
    """

    __tablename__ = "program_enrollments"
    __table_args__ = (
        Index("ix_program_enrollments_program_id", "program_id"),
        CheckConstraint(
            "training_duration_months IS NULL OR training_duration_months IN (6, 12)",
            name="ck_program_enrollments_training_duration_approved",
        ),
        CheckConstraint(
            "approved_scholarship_amount IS NULL OR approved_scholarship_amount >= 0",
            name="ck_program_enrollments_scholarship_amount_non_negative",
        ),
        CheckConstraint(
            "bond_end_date IS NULL OR bond_start_date IS NULL OR bond_end_date > bond_start_date",
            name="ck_program_enrollments_bond_dates_ordered",
        ),
        CheckConstraint(
            "fee_amount IS NULL OR (fee_amount >= 0 AND fee_currency IS NOT NULL)",
            name="ck_program_enrollments_fee_obligation_valid",
        ),
        CheckConstraint(
            "company_contribution_amount IS NULL OR (company_contribution_amount >= 0 "
            "AND fee_amount IS NOT NULL AND company_contribution_amount <= fee_amount)",
            name="ck_program_enrollments_contribution_within_fee",
        ),
        CheckConstraint(
            "candidate_payable_amount IS NULL OR (fee_amount IS NOT NULL AND "
            "candidate_payable_amount = fee_amount - COALESCE(company_contribution_amount, 0))",
            name="ck_program_enrollments_payable_consistent",
        ),
        CheckConstraint(
            "training_completed_on IS NULL OR training_start_date IS NULL "
            "OR training_completed_on >= training_start_date",
            name="ck_program_enrollments_training_completion_after_start",
        ),
        CheckConstraint(
            "agreement_expiry_date IS NULL OR agreement_effective_date IS NULL "
            "OR agreement_expiry_date > agreement_effective_date",
            name="ck_program_enrollments_agreement_dates_ordered",
        ),
        CheckConstraint(
            "(agreement_document_storage_path IS NULL) = "
            "(agreement_document_storage_provider IS NULL) AND "
            "(agreement_document_storage_path IS NULL) = (agreement_document_filename IS NULL) AND "
            "(agreement_document_storage_path IS NULL) = (agreement_document_uploaded_at IS NULL)",
            name="ck_program_enrollments_agreement_document_complete",
        ),
    )

    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recruitment_programs.id", ondelete="RESTRICT"), nullable=False
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Declared at application time.
    education_level: Mapped[EducationLevel] = mapped_column(
        Enum(EducationLevel, name="education_level", native_enum=True, create_type=False),
        nullable=False,
    )
    study_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    specialization: Mapped[str] = mapped_column(String(150), nullable=False)
    institution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("institutions.id", ondelete="RESTRICT"), nullable=True
    )
    institution_name: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    terms_version_accepted: Mapped[str] = mapped_column(String(50), nullable=False)
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disclosure_snapshot: Mapped[str] = mapped_column(Text, nullable=False)

    # Eligibility review.
    eligibility_status: Mapped[EligibilityStatus] = mapped_column(
        Enum(EligibilityStatus, name="program_eligibility_status", native_enum=True),
        nullable=False,
        default=EligibilityStatus.PENDING,
        server_default=EligibilityStatus.PENDING.value,
    )
    eligibility_reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    eligibility_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    eligibility_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Interview gate before a selection decision.
    interview_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    interview_completed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Scholarship: eligibility, then a separate decision, then payments.
    scholarship_eligibility: Mapped[ScholarshipEligibility] = mapped_column(
        Enum(ScholarshipEligibility, name="scholarship_eligibility", native_enum=True),
        nullable=False,
        default=ScholarshipEligibility.NOT_DETERMINED,
        server_default=ScholarshipEligibility.NOT_DETERMINED.value,
    )
    scholarship_eligibility_decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    scholarship_eligibility_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scholarship_status: Mapped[ScholarshipStatus] = mapped_column(
        Enum(ScholarshipStatus, name="scholarship_status", native_enum=True),
        nullable=False,
        default=ScholarshipStatus.NOT_STARTED,
        server_default=ScholarshipStatus.NOT_STARTED.value,
    )
    approved_scholarship_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    approved_scholarship_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    scholarship_decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    scholarship_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scholarship_decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The candidate's training-fee obligation, fixed (snapshotted from the
    # program) the first time it is needed, so later program edits never
    # rewrite it. `company_contribution_amount` is the approved funding
    # applied to the fee; `candidate_payable_amount` = fee - contribution.
    fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    fee_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    fee_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    company_contribution_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    candidate_payable_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Which approved funding revision the candidate accepted, and when.
    # Changing accepted terms needs an approved AMENDMENT revision.
    funding_terms_accepted_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    funding_terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Training.
    training_status: Mapped[TrainingStatus] = mapped_column(
        Enum(TrainingStatus, name="training_status", native_enum=True),
        nullable=False,
        default=TrainingStatus.NOT_ENROLLED,
        server_default=TrainingStatus.NOT_ENROLLED.value,
    )
    training_duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    training_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Planned end (start + duration unless HR enters a date).
    training_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    training_enrolled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    training_completed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    training_outcome_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Employment review: the separate, approved decision to progress a
    # participant to full-time employment (before any offer).
    employment_review_status: Mapped[EmploymentReviewStatus] = mapped_column(
        Enum(EmploymentReviewStatus, name="program_employment_review_status", native_enum=True),
        nullable=False,
        default=EmploymentReviewStatus.NOT_STARTED,
        server_default=EmploymentReviewStatus.NOT_STARTED.value,
    )
    employment_role_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employment_department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    employment_proposed_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    employment_decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    employment_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    employment_decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The team-hierarchy employee record — linked (or created) only when the
    #: candidate actually joins, never earlier.
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )

    # Full-time employment.
    offer_status: Mapped[OfferStatus] = mapped_column(
        Enum(OfferStatus, name="program_offer_status", native_enum=True),
        nullable=False,
        default=OfferStatus.NOT_OFFERED,
        server_default=OfferStatus.NOT_OFFERED.value,
    )
    offer_made_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    joined_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Agreement / bond. The wording is the company's approved document —
    # tracked here by reference and (optionally) the signed copy, never
    # generated by the platform.
    agreement_status: Mapped[AgreementStatus] = mapped_column(
        Enum(AgreementStatus, name="program_agreement_status", native_enum=True),
        nullable=False,
        default=AgreementStatus.NOT_REQUIRED,
        server_default=AgreementStatus.NOT_REQUIRED.value,
    )
    agreement_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The company-approved agreement/template version HR sent.
    agreement_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: The terms as they stood when the agreement was sent (fee, approved
    #: contribution, payable amount, bond duration, funding revision), so a
    #: later program or template edit never alters what was agreed.
    agreement_terms_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    agreement_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    agreement_signed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    agreement_effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    agreement_expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Why the agreement was declined, voided or ended early.
    agreement_status_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Why the bond applies to this participant (recorded when funding is
    #: approved) — the bond is never imposed without company funding.
    bond_applies_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    agreement_document_storage_provider: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    agreement_document_storage_path: Mapped[str | None] = mapped_column(
        String(1000), nullable=True
    )
    agreement_document_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    agreement_document_uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    bond_duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bond_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    bond_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    program: Mapped[RecruitmentProgram] = relationship(
        RecruitmentProgram, lazy="raise", viewonly=True
    )


class FundingRevisionKind(StrEnum):
    INITIAL = "INITIAL"
    #: Changed terms before the candidate accepted them.
    REVISION = "REVISION"
    #: Changed terms after the candidate accepted them — needs a reason and
    #: the candidate's renewed acceptance.
    AMENDMENT = "AMENDMENT"


class FundingRevisionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    #: An approved revision replaced by a later approved one.
    SUPERSEDED = "SUPERSEDED"


class ProgramFundingRevision(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One proposal of company-funding terms for one participant, and its
    decision. Append-only history: the fee, the funding basis and the
    computed contribution / payable amounts are kept as they were, so a
    later fee or policy change never rewrites a past decision."""

    __tablename__ = "program_funding_revisions"
    __table_args__ = (
        Index(
            "uq_program_funding_revisions_enrollment_number",
            "enrollment_id",
            "revision_number",
            unique=True,
        ),
        # At most one open proposal per participant.
        Index(
            "uq_program_funding_revisions_one_proposed",
            "enrollment_id",
            unique=True,
            postgresql_where="status = 'PROPOSED'",
        ),
        CheckConstraint("fee_amount >= 0", name="ck_program_funding_revisions_fee_non_negative"),
        CheckConstraint(
            "funding_value > 0 AND (funding_type <> 'PERCENTAGE' OR funding_value <= 100)",
            name="ck_program_funding_revisions_value_valid",
        ),
        CheckConstraint(
            "company_contribution_amount >= 0 AND company_contribution_amount <= fee_amount",
            name="ck_program_funding_revisions_contribution_within_fee",
        ),
        CheckConstraint(
            "candidate_payable_amount = fee_amount - company_contribution_amount",
            name="ck_program_funding_revisions_payable_consistent",
        ),
    )

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("program_enrollments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[FundingRevisionKind] = mapped_column(
        Enum(FundingRevisionKind, name="program_funding_revision_kind", native_enum=True),
        nullable=False,
    )
    status: Mapped[FundingRevisionStatus] = mapped_column(
        Enum(FundingRevisionStatus, name="program_funding_revision_status", native_enum=True),
        nullable=False,
        default=FundingRevisionStatus.PROPOSED,
        server_default=FundingRevisionStatus.PROPOSED.value,
    )
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    funding_type: Mapped[FundingType] = mapped_column(
        Enum(FundingType, name="program_funding_type", native_enum=True, create_type=False),
        nullable=False,
    )
    funding_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    company_contribution_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    candidate_payable_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class MilestoneStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    MISSED = "MISSED"


class TrainingMilestone(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A checkpoint in one participant's training (a module, a progress
    review, an internal assessment, an attendance review…) that staff track
    to completion. "Overdue" = PENDING past its due date — derived, never
    stored."""

    __tablename__ = "training_milestones"
    __table_args__ = (
        CheckConstraint(
            "(status = 'COMPLETED') = (completed_on IS NOT NULL)",
            name="ck_training_milestones_completion_date",
        ),
    )

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("program_enrollments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[MilestoneStatus] = mapped_column(
        Enum(MilestoneStatus, name="training_milestone_status", native_enum=True),
        nullable=False,
        default=MilestoneStatus.PENDING,
        server_default=MilestoneStatus.PENDING.value,
    )
    completed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class DisbursementStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class ScholarshipDisbursement(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One scholarship payment, as recorded by HR. The platform moves no
    money; `reference` is the finance team's own transaction/voucher id —
    never bank account or card details."""

    __tablename__ = "scholarship_disbursements"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_scholarship_disbursements_amount_positive"),
    )

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("program_enrollments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[DisbursementStatus] = mapped_column(
        Enum(DisbursementStatus, name="scholarship_disbursement_status", native_enum=True),
        nullable=False,
        default=DisbursementStatus.SCHEDULED,
        server_default=DisbursementStatus.SCHEDULED.value,
    )
    scheduled_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class PaymentMethod(StrEnum):
    BANK_TRANSFER = "BANK_TRANSFER"
    UPI = "UPI"
    CHEQUE = "CHEQUE"
    CASH = "CASH"
    OTHER = "OTHER"


class PaymentRecordStatus(StrEnum):
    RECORDED = "RECORDED"
    VOIDED = "VOIDED"


class ProgramFeePayment(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """A training-fee payment the candidate made, recorded manually by HR
    (there is no payment-gateway integration). Append-only: a mistaken
    entry is VOIDED with a reason, never edited or deleted, so the history
    stays auditable. No card or bank credentials are ever stored."""

    __tablename__ = "program_fee_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_program_fee_payments_amount_positive"),
    )

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("program_enrollments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="program_payment_method", native_enum=True), nullable=False
    )
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PaymentRecordStatus] = mapped_column(
        Enum(PaymentRecordStatus, name="program_payment_record_status", native_enum=True),
        nullable=False,
        default=PaymentRecordStatus.RECORDED,
        server_default=PaymentRecordStatus.RECORDED.value,
    )
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    voided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

