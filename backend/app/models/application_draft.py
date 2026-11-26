import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ApplicationDraftStatus(StrEnum):
    OPEN = "OPEN"
    SUBMITTED = "SUBMITTED"


class EducationCheckStatus(StrEnum):
    """Where a draft's academic-document check stands. Only VERIFIED and
    MANUAL_REVIEW let the candidate past the Education step."""

    NOT_STARTED = "NOT_STARTED"
    VERIFIED = "VERIFIED"
    MISMATCH = "MISMATCH"
    UNCLEAR = "UNCLEAR"
    FAILED = "FAILED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class EducationVerificationOutcome(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNCLEAR = "UNCLEAR"
    FAILED = "FAILED"


class ApplicationDraft(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A self-service application in progress — NOT an Application.

    Created once the candidate has verified their email, it holds what each
    step saved (server-side, so the final submission builds the Application
    from data the server validated, not from whatever the browser sends at
    the end) and the academic-document check, whose outcome the browser can
    never assert. Every read or write requires a valid email verification
    token for `email` (app/services/application_draft_service.py).

    At most one OPEN draft per (organization, email, job): returning to the
    apply page after re-verifying resumes it. Submitting turns it SUBMITTED
    and links the Application it produced; an abandoned draft expires
    (`expires_at`) and is purged with its document
    (`python -m app.cli purge-expired-drafts`).
    """

    __tablename__ = "application_drafts"
    __table_args__ = (
        Index(
            "uq_application_drafts_open_per_job",
            "organization_id",
            "email",
            "job_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campus_drive_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campus_drives.id", ondelete="CASCADE"), nullable=True
    )
    #: Lower-cased, the verified address.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[ApplicationDraftStatus] = mapped_column(
        Enum(ApplicationDraftStatus, name="application_draft_status", native_enum=True),
        nullable=False,
        default=ApplicationDraftStatus.OPEN,
        server_default=ApplicationDraftStatus.OPEN.value,
    )
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    #: E.164, normalized.
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The Professional background step, as validated (NULL = not saved).
    background: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: The Education step's details, as validated (NULL = not saved).
    education: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    education_status: Mapped[EducationCheckStatus] = mapped_column(
        Enum(EducationCheckStatus, name="education_check_status", native_enum=True),
        nullable=False,
        default=EducationCheckStatus.NOT_STARTED,
        server_default=EducationCheckStatus.NOT_STARTED.value,
    )
    #: The job requirements' source hash the education check was made
    #: against; a different hash at submission = the JD changed since, so
    #: eligibility is re-checked.
    qualification_source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    academic_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academic_documents.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    verification_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    submitted_application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    academic_document: Mapped["AcademicDocument | None"] = relationship(
        foreign_keys=[academic_document_id], lazy="selectin"
    )


class AcademicDocument(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """The marks card / certificate a candidate uploaded as proof of the
    qualification they selected. The bytes live in the storage abstraction
    (GridFS in production) — this row is metadata only, and the storage
    path is never returned to a client (CLAUDE.md § 2).

    While the application is a draft only `draft_id` is set; submission
    links it to the Candidate and the Application. A replaced document (the
    candidate uploaded a different one) is deleted with its file.
    """

    __tablename__ = "academic_documents"

    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("application_drafts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=True
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    qualification_key: Mapped[str] = mapped_column(String(50), nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(150), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class EducationVerificationRun(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One AI consistency check of an academic document against the
    selected qualification and the job's requirements. Its own traceable
    domain (CLAUDE.md § 2: "AI != Candidate"): a new upload or a retry adds a
    row, never overwrites one. `analysis` holds the model's observations for
    HR; `candidate_message` is the plain-language text the candidate saw,
    composed by the service from structured fields (never model free text).

    An advisory consistency check only — never proof that a document is
    genuine.
    """

    __tablename__ = "education_verification_runs"

    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("application_drafts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    academic_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_documents.id", ondelete="SET NULL"), nullable=True
    )
    qualification_key: Mapped[str] = mapped_column(String(50), nullable=False)
    #: The specialization category + specialization the document was
    #: checked against (NULL for checks run before they were recorded):
    #: the candidate's text, or a catalogue key on older runs.
    specialization_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    specialization: Mapped[str | None] = mapped_column(String(150), nullable=True)
    outcome: Mapped[EducationVerificationOutcome] = mapped_column(
        Enum(EducationVerificationOutcome, name="education_verification_outcome", native_enum=True),
        nullable=False,
    )
    #: A short machine-readable reason (e.g. `level_mismatch`, `illegible`).
    reason_code: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_message: Mapped[str] = mapped_column(Text, nullable=False)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualification_source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
