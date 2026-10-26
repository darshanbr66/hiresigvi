import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class InstitutionType(StrEnum):
    UNIVERSITY = "UNIVERSITY"
    COLLEGE = "COLLEGE"
    INSTITUTE = "INSTITUTE"
    OTHER = "OTHER"


class Institution(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """The organization's registry of colleges/universities (docs/
    campus-hiring.md § 1a) — one row per institution, referenced by campus
    drives and recruitment-program eligibility, so the same college is never
    spelled three different ways across drives.

    `normalized_name` (lower-cased, whitespace-collapsed — set by
    app/services/institution_service.py) backs the per-organization
    uniqueness rule, so "IIT  Madras" and "iit madras" are one institution.

    `email_domains` is optional: many institutions have no official student
    email domain, and the registry never requires one. When present it is
    what EMAIL_DOMAIN affiliation verification matches against.
    """

    __tablename__ = "institutions"
    __table_args__ = (
        Index("uq_institutions_org_normalized_name", "organization_id", "normalized_name", unique=True),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    institution_type: Mapped[InstitutionType | None] = mapped_column(
        Enum(InstitutionType, name="institution_type", native_enum=True), nullable=True
    )
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email_domains: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, default=list, server_default="{}"
    )
    placement_contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    placement_contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    placement_contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class AffiliationVerificationMethod(StrEnum):
    """How a campus drive checks that an applicant really belongs to its
    institution. Opening the drive link never proves affiliation on its own
    (docs/campus-hiring.md § 3a) — NONE means "not required for this drive",
    never "verified"."""

    NONE = "NONE"
    EMAIL_DOMAIN = "EMAIL_DOMAIN"
    STUDENT_LIST = "STUDENT_LIST"
    MANUAL = "MANUAL"


class AffiliationStatus(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class CampusAffiliationVerification(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Whether one campus-drive application's candidate is confirmed as a
    member of the drive's institution. One row per application (created only
    when the drive requires verification); `method` records *how* it was
    decided, `reviewed_by_user_id` is NULL for an automatic (email-domain /
    student-list) match. Every change is also recorded in the activity log.
    """

    __tablename__ = "campus_affiliation_verifications"

    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    campus_drive_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("campus_drives.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    institution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("institutions.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[AffiliationStatus] = mapped_column(
        Enum(AffiliationStatus, name="affiliation_status", native_enum=True),
        nullable=False,
        default=AffiliationStatus.PENDING,
        server_default=AffiliationStatus.PENDING.value,
    )
    method: Mapped[AffiliationVerificationMethod | None] = mapped_column(
        Enum(
            AffiliationVerificationMethod,
            name="affiliation_verification_method",
            native_enum=True,
            create_type=False,
        ),
        nullable=True,
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class CampusDriveRosterEntry(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """One student on the college-provided list for a drive (STUDENT_LIST
    verification). Deliberately minimal — email (normalized), an optional
    roll/registration number and name — never identity documents."""

    __tablename__ = "campus_drive_roster_entries"
    __table_args__ = (
        Index("uq_campus_drive_roster_drive_email", "campus_drive_id", "email", unique=True),
    )

    campus_drive_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campus_drives.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    student_identifier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
