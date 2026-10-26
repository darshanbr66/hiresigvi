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
from app.models.institution import Institution
from app.models.job import Job

#: The only training durations the business has approved (months).
APPROVED_TRAINING_DURATIONS: frozenset[int] = frozenset({6, 12})
#: The approved default commitment for scholarship recipients (months);
#: HR can change it per program, it is never applied silently.
DEFAULT_BOND_DURATION_MONTHS = 36


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
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"


class TrainingStatus(StrEnum):
    NOT_ENROLLED = "NOT_ENROLLED"
    ENROLLED = "ENROLLED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    DISCONTINUED = "DISCONTINUED"


class OfferStatus(StrEnum):
    NOT_OFFERED = "NOT_OFFERED"
    OFFERED = "OFFERED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    WITHDRAWN = "WITHDRAWN"


class AgreementStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    SENT = "SENT"
    SIGNED = "SIGNED"
    DECLINED = "DECLINED"
    VOID = "VOID"


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

    # Training.
    training_status: Mapped[TrainingStatus] = mapped_column(
        Enum(TrainingStatus, name="training_status", native_enum=True),
        nullable=False,
        default=TrainingStatus.NOT_ENROLLED,
        server_default=TrainingStatus.NOT_ENROLLED.value,
    )
    training_duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    training_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    training_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

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
    agreement_signed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
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

