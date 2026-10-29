"""Interviews held for an application (docs/recruitment-workflow.md § 2a).

An `ApplicationInterview` is one interview round — its stage, schedule,
interviewer, status, and the interviewer's recorded outcome and feedback.
It belongs to the platform-wide application domain (any application can
have interviews), not to recruitment programs; programs use it as the
evidence behind their "interview completed" gate. The outcome of an
interview is advisory input to a selection decision, never the decision
itself — selection stays an explicit `Application.status` change.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class InterviewStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"


class InterviewOutcome(StrEnum):
    """The interviewer's recommendation — not a selection decision."""

    PASS = "PASS"
    FAIL = "FAIL"
    ON_HOLD = "ON_HOLD"


class ApplicationInterview(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "application_interviews"
    __table_args__ = (
        Index("ix_application_interviews_application_id", "application_id"),
        # One open (scheduled) interview per stage of an application, so the
        # same round can't be booked twice by accident.
        Index(
            "uq_application_interviews_one_scheduled_per_stage",
            "application_id",
            "stage",
            unique=True,
            postgresql_where="status = 'SCHEDULED'",
        ),
        CheckConstraint(
            "(status = 'COMPLETED') = (outcome IS NOT NULL)",
            name="ck_application_interviews_outcome_when_completed",
        ),
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="ck_application_interviews_rating_range",
        ),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(150), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    interviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[InterviewStatus] = mapped_column(
        Enum(InterviewStatus, name="interview_status", native_enum=True),
        nullable=False,
        default=InterviewStatus.SCHEDULED,
        server_default=InterviewStatus.SCHEDULED.value,
    )
    outcome: Mapped[InterviewOutcome | None] = mapped_column(
        Enum(InterviewOutcome, name="interview_outcome", native_enum=True), nullable=True
    )
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Staff-only. Never shown to the candidate or to other interviewers
    #: without the interview-management permission.
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
