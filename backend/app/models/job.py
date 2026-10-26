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
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class JobStatus(StrEnum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    ON_HOLD = "ON_HOLD"
    CLOSED = "CLOSED"
    WITHDRAWN = "WITHDRAWN"


class EmploymentType(StrEnum):
    """What kind of opportunity a job is. One recruitment pipeline serves
    all three — the type only decides which descriptive fields apply
    (`EMPLOYMENT_TYPE_FIELDS`). A recruitment program's carrier job has no
    employment type (NULL): the scholarship/training program is its own
    workflow (docs/programs.md), never an ordinary internship or job."""

    INTERNSHIP = "INTERNSHIP"
    PART_TIME = "PART_TIME"
    FULL_TIME = "FULL_TIME"


EMPLOYMENT_TYPE_LABELS: dict[EmploymentType, str] = {
    EmploymentType.INTERNSHIP: "Internship",
    EmploymentType.PART_TIME: "Part-time",
    EmploymentType.FULL_TIME: "Full-time",
}


class WorkMode(StrEnum):
    ON_SITE = "ON_SITE"
    HYBRID = "HYBRID"
    REMOTE = "REMOTE"


class CompensationPeriod(StrEnum):
    HOURLY = "HOURLY"
    MONTHLY = "MONTHLY"
    ANNUAL = "ANNUAL"


#: Fields every employment type may use.
_COMMON_TYPE_FIELDS = frozenset(
    {
        "work_mode",
        "compensation_min",
        "compensation_max",
        "compensation_currency",
        "compensation_period",
        "eligibility_criteria",
    }
)

#: Which optional, type-specific fields apply to each employment type. A
#: field outside a type's set must stay empty for that type — so an
#: internship never carries a joining date and a full-time job never carries
#: a stipend status. None of these are required: the business decides what
#: to publish (an unpaid internship has no stipend; a salary range is shown
#: only once approved).
EMPLOYMENT_TYPE_FIELDS: dict[EmploymentType, frozenset[str]] = {
    EmploymentType.INTERNSHIP: _COMMON_TYPE_FIELDS
    | {"duration_months", "is_paid", "conversion_to_full_time"},
    EmploymentType.PART_TIME: _COMMON_TYPE_FIELDS
    | {
        "duration_months",
        "hours_per_week",
        "work_schedule",
        "min_experience_years",
        "max_experience_years",
        "employment_terms",
    },
    EmploymentType.FULL_TIME: _COMMON_TYPE_FIELDS
    | {"min_experience_years", "max_experience_years", "expected_joining_date", "employment_terms"},
}

TYPE_SPECIFIC_FIELDS: frozenset[str] = frozenset().union(*EMPLOYMENT_TYPE_FIELDS.values())


class Job(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A hiring opportunity. `JobRequirement` (structured, weighted
    requirements for AI screening) is deliberately not modeled yet — it has
    no consumer until Phase 5/6's retrieval pipeline exists; adding it now
    would be exactly the speculative-for-later-phases modeling
    docs/architecture.md § 14 warns against. `description` free text is
    enough for Phase 3's recruiter-facing CRUD.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "compensation_min IS NULL OR compensation_max IS NULL "
            "OR compensation_min <= compensation_max",
            name="ck_jobs_compensation_range",
        ),
        CheckConstraint(
            "min_experience_years IS NULL OR max_experience_years IS NULL "
            "OR min_experience_years <= max_experience_years",
            name="ck_jobs_experience_range",
        ),
        CheckConstraint(
            "duration_months IS NULL OR duration_months BETWEEN 1 AND 60",
            name="ck_jobs_duration_months_range",
        ),
        CheckConstraint(
            "hours_per_week IS NULL OR hours_per_week BETWEEN 1 AND 168",
            name="ck_jobs_hours_per_week_range",
        ),
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: NULL only for a recruitment program's carrier job (see EmploymentType);
    #: job_service always sets it for an ordinary job.
    employment_type: Mapped[EmploymentType | None] = mapped_column(
        Enum(EmploymentType, name="employment_type", native_enum=True), nullable=True
    )

    # --- Type-specific details (EMPLOYMENT_TYPE_FIELDS; all optional) ---
    work_mode: Mapped[WorkMode | None] = mapped_column(
        Enum(WorkMode, name="work_mode", native_enum=True), nullable=True
    )
    #: Internship / fixed-term part-time length.
    duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Internship only: paid or unpaid (NULL = not stated).
    is_paid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: One pay structure for every type: an internship stipend, a part-time
    #: pay range or a full-time salary range. A single value is min == max.
    compensation_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    compensation_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    compensation_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    compensation_period: Mapped[CompensationPeriod | None] = mapped_column(
        Enum(CompensationPeriod, name="compensation_period", native_enum=True), nullable=True
    )
    hours_per_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    work_schedule: Mapped[str | None] = mapped_column(Text, nullable=True)
    min_experience_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_experience_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Eligibility / educational requirements shown to candidates.
    eligibility_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Internship only: may convert to a full-time role (NULL = not stated).
    conversion_to_full_time: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    expected_joining_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    employment_terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # UI-visibility only — the JD text is never deleted when hidden (CLAUDE.md
    # § 2: "Workflow state != ad hoc strings" applies equally here: this is a
    # real column, not an ad hoc frontend-only flag). Enforced server-side on
    # the public job-detail endpoint (app/api/v1/public/jobs.py), not just
    # hidden in the UI.
    description_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", native_enum=True),
        nullable=False,
        default=JobStatus.DRAFT,
        server_default=JobStatus.DRAFT.value,
    )
    openings_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    # Soft delete — same rationale/shape as Candidate's (app/models/candidate.py):
    # a hard DELETE would also be blocked by applications.job_id's ON DELETE
    # RESTRICT the moment the job has any application, so this makes that the
    # deliberate behavior everywhere. Deleted jobs are excluded from
    # `job_service.list_jobs` but every Application/CampusDrive pointing at
    # them stays intact.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deletion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
