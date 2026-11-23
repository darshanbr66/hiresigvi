import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class JobQualificationStatus(StrEnum):
    #: The AI read the JD; `requirements` holds the structured result.
    READY = "READY"
    #: The AI could not be reached / isn't configured; retried after a
    #: short back-off. The apply flow treats the job as unrestricted.
    UNAVAILABLE = "UNAVAILABLE"


class JobQualificationProfile(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A job's qualification requirements, derived from its own description
    and eligibility text (app/services/education/job_qualification_service.py)
    — a cache, never hand-edited, and never a replacement for the JD.

    One row per job. `source_hash` is the SHA-256 of the text the analysis
    was computed from: when the recruiter edits the JD the hash no longer
    matches and the next read recomputes it, so the AI runs once per JD
    version, not once per candidate. Kept out of `Job` itself so jobs are
    untouched and the cache can be dropped and rebuilt freely.
    """

    __tablename__ = "job_qualification_profiles"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[JobQualificationStatus] = mapped_column(
        Enum(JobQualificationStatus, name="job_qualification_status", native_enum=True),
        nullable=False,
    )
    #: The normalized requirements (see job_qualification_service.
    #: QualificationRequirements) — NULL when UNAVAILABLE.
    requirements: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: Our own exception text for an UNAVAILABLE row (never upstream bodies).
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
