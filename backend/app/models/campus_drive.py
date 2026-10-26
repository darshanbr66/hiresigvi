import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.assessment import Assessment
from app.models.institution import AffiliationVerificationMethod, Institution
from app.models.job import Job


class CampusDriveStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"


#: {current_status: {allowed_next statuses}} — same "workflow state != ad
#: hoc strings" rule as app/workflows/application_workflow.py, at this
#: domain's much smaller scale (no separate module warranted).
CAMPUS_DRIVE_TRANSITIONS: dict[CampusDriveStatus, frozenset[CampusDriveStatus]] = {
    CampusDriveStatus.DRAFT: frozenset({CampusDriveStatus.ACTIVE}),
    CampusDriveStatus.ACTIVE: frozenset({CampusDriveStatus.PAUSED, CampusDriveStatus.CLOSED}),
    CampusDriveStatus.PAUSED: frozenset({CampusDriveStatus.ACTIVE, CampusDriveStatus.CLOSED}),
    CampusDriveStatus.CLOSED: frozenset({CampusDriveStatus.ACTIVE}),  # reopen
}


class CampusDrive(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Composes an existing Job with campus-specific scheduling metadata —
    does not duplicate anything Job or Application already model
    (docs/campus-hiring.md § 1). Candidate participation is just
    `Application.campus_drive_id`, not a separate membership table (§ 2).

    `link_token_hash` is a dedicated public application link, independent
    of the general career-site apply flow — same hashed-opaque-token
    pattern as `AssessmentInvitation.token_hash` (never stored/logged in
    plaintext, CLAUDE.md § 4). `default_assessment_id`, when set, is used
    to auto-invite a candidate who applies through this link (see
    app/services/public_campus_drive_service.py).

    Simplified from docs/campus-hiring.md: no `eligibility_criteria` jsonb
    filter and no `candidate_limit` enforcement — both real gaps, noted
    here rather than silently dropped, not blocking the core
    create-drive -> share-link -> track-applications flow. The drive's
    institution comes from the registry (`institution_id`), and the
    application's drive is always resolved server-side from the link token,
    never from a candidate-submitted identifier.
    """

    __tablename__ = "campus_drives"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # The drive's institution, from the organization's registry
    # (app/models/institution.py). `college_name` is kept as a display
    # snapshot of the institution's name (synchronized by
    # campus_drive_service) so existing readers keep working; the FK is the
    # source of truth for filtering and reporting.
    institution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("institutions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    college_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # How applicants' membership of the institution is confirmed
    # (docs/campus-hiring.md § 3a). NONE = not required for this drive.
    affiliation_verification_method: Mapped[AffiliationVerificationMethod] = mapped_column(
        Enum(AffiliationVerificationMethod, name="affiliation_verification_method", native_enum=True),
        nullable=False,
        default=AffiliationVerificationMethod.NONE,
        server_default=AffiliationVerificationMethod.NONE.value,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    batch_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    registration_deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    default_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessments.id", ondelete="SET NULL"), nullable=True
    )
    link_token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[CampusDriveStatus] = mapped_column(
        Enum(CampusDriveStatus, name="campus_drive_status", native_enum=True),
        nullable=False,
        default=CampusDriveStatus.DRAFT,
        server_default=CampusDriveStatus.DRAFT.value,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    # Soft delete/archive — same rationale/shape as Candidate's (app/models/
    # candidate.py). Deleted drives drop out of
    # `campus_drive_service.list_campus_drives`, but every Application
    # sourced from them (`Application.campus_drive_id`) stays intact.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deletion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[Job] = relationship(Job, lazy="raise", viewonly=True)
    institution: Mapped[Institution] = relationship(Institution, lazy="raise", viewonly=True)
    default_assessment: Mapped[Assessment | None] = relationship(
        Assessment, lazy="raise", viewonly=True
    )
