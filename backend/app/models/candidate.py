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
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class CandidateSource(StrEnum):
    """How this Candidate record came to exist — see
    docs/recruitment-workflow.md § 4. `PORTAL` (candidate self-registration)
    is unused until Phase 4 builds candidate auth; recruiter-facing creation
    in Phase 3 always uses `RECRUITER_ADDED`.
    """

    PORTAL = "PORTAL"
    RECRUITER_ADDED = "RECRUITER_ADDED"
    CAMPUS_IMPORT = "CAMPUS_IMPORT"
    REFERRAL = "REFERRAL"
    OTHER = "OTHER"


class CandidateType(StrEnum):
    """Self-declared on the public application form — drives which of the
    experience fields are meaningful (a FRESHER has no notice period)."""

    FRESHER = "FRESHER"
    EXPERIENCED = "EXPERIENCED"


class Candidate(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Reusable candidate profile data — never merged with Application, which
    carries per-opportunity workflow state (CLAUDE.md § 2: "Candidate !=
    Application"). Tenant-scoped: the same person applying to two
    organizations on this platform is two independent Candidate rows (see
    docs/architecture.md § 3.3).

    `phone` is always stored E.164-normalized (app/core/phone.py) and is
    unique per organization, like `email`.

    `hashed_password` stays NULL until the candidate self-registers a portal
    login (Phase 4) — a recruiter-added candidate may never do so.
    """

    __tablename__ = "candidates"
    __table_args__ = (
        Index("uq_candidates_org_email", "organization_id", "email", unique=True),
        # The mobile number is a person's identity key alongside email (one
        # profile per person per organization). Always stored E.164-normalized
        # (app/core/phone.py) so formatting differences can't dodge this.
        Index(
            "uq_candidates_org_phone",
            "organization_id",
            "phone",
            unique=True,
            postgresql_where="phone IS NOT NULL",
        ),
        Index("ix_candidates_languages", "languages", postgresql_using="gin"),
        # A candidate who came in through the email-verified self-service flow
        # always has the identity fields that flow makes mandatory — enforced
        # here too, not only in the API, so no later edit can blank them.
        # Recruiter-added/imported candidates (never email-verified) are
        # unaffected.
        CheckConstraint(
            "email_verified_at IS NULL OR (phone IS NOT NULL AND date_of_birth IS NOT NULL "
            "AND place_of_birth IS NOT NULL AND cardinality(languages) > 0)",
            name="ck_candidates_verified_identity_complete",
        ),
        CheckConstraint(
            "tenth_marks_percentage IS NULL OR "
            "(tenth_marks_percentage >= 0 AND tenth_marks_percentage <= 100)",
            name="ck_candidates_tenth_marks_percentage_range",
        ),
        # A marks-card reference is either complete or absent — never a
        # path without its provider (which could not be resolved or cleaned
        # up).
        CheckConstraint(
            "(tenth_marks_card_storage_path IS NULL) = "
            "(tenth_marks_card_storage_provider IS NULL) AND "
            "(tenth_marks_card_storage_path IS NULL) = (tenth_marks_card_filename IS NULL) AND "
            "(tenth_marks_card_storage_path IS NULL) = "
            "(tenth_marks_card_content_type IS NULL) AND "
            "(tenth_marks_card_storage_path IS NULL) = (tenth_marks_card_size_bytes IS NULL) AND "
            "(tenth_marks_card_storage_path IS NULL) = (tenth_marks_card_uploaded_at IS NULL)",
            name="ck_candidates_tenth_marks_card_complete",
        ),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Unbounded: the self-service form imposes no length limit on a name
    # (some legal names are long); see migration f9a0b1c2d3e4.
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    years_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Reusable profile data captured by the public application form. All
    # nullable: recruiter-added and imported candidates may not have them,
    # and this is candidate *profile* data, not per-application state
    # (CLAUDE.md § 2: "Candidate != Application"). `location` above is the
    # candidate's current location.
    candidate_type: Mapped[CandidateType | None] = mapped_column(
        Enum(CandidateType, name="candidate_type", native_enum=True), nullable=True
    )
    current_company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    preferred_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notice_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    immediate_joiner: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    qualification: Mapped[str | None] = mapped_column(String(255), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    place_of_birth: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Validated, de-duplicated, display-cased language names
    # (app/schemas/candidate.py::normalize_languages) — a typed text[] with a
    # GIN index so "who speaks Tamil" is an indexed containment query, not a
    # free-text blob. Empty for candidates who never supplied it.
    languages: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), nullable=False, default=list, server_default="{}"
    )
    # Optional education data from the self-service form: the 10th-standard
    # (SSLC/secondary) result as a percentage, and a reference to the
    # uploaded marks card. The card's *bytes* live in the file-storage
    # abstraction (app/integrations/storage — MongoDB GridFS in production),
    # never in Postgres (CLAUDE.md § 2: "Resume storage != DB blob"); these
    # columns are metadata only. `tenth_marks_card_storage_path` is opaque,
    # resolved against `tenth_marks_card_storage_provider`, and never
    # returned to any client. All-or-nothing: see the check constraint.
    tenth_marks_percentage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    tenth_marks_card_storage_provider: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    tenth_marks_card_storage_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    tenth_marks_card_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tenth_marks_card_content_type: Mapped[str | None] = mapped_column(String(150), nullable=True)
    tenth_marks_card_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tenth_marks_card_uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set once the candidate proved ownership of `email` with a one-time code
    # (app/services/email_verification_service.py). NULL for recruiter-added
    # and imported candidates, whose email was never verified by the platform.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source: Mapped[CandidateSource] = mapped_column(
        Enum(CandidateSource, name="candidate_source", native_enum=True),
        nullable=False,
        default=CandidateSource.RECRUITER_ADDED,
        server_default=CandidateSource.RECRUITER_ADDED.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Soft delete (CLAUDE.md § "Reports != hardcoded numbers" sibling rule:
    # never silently destroy recruitment history). A hard DELETE would also
    # be blocked by applications.candidate_id's ON DELETE RESTRICT the
    # moment the candidate has any application — this makes that the
    # deliberate behavior everywhere, not just where the FK happens to
    # enforce it. Deleted candidates are excluded from
    # `candidate_service.list_candidates` but remain reachable by id (e.g.
    # from an Activities log entry) and keep every Application/Note/
    # AssessmentInvitation pointing at them intact.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deletion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
