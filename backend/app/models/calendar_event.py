import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User


class CalendarEventType(StrEnum):
    INTERVIEW = "INTERVIEW"
    HR_MEETING = "HR_MEETING"
    TEAM_MEETING = "TEAM_MEETING"
    ASSESSMENT_DEADLINE = "ASSESSMENT_DEADLINE"
    FOLLOW_UP = "FOLLOW_UP"
    RECRUITMENT_EVENT = "RECRUITMENT_EVENT"
    CAMPUS_EVENT = "CAMPUS_EVENT"
    GENERAL_REMINDER = "GENERAL_REMINDER"


class CalendarEventStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class CalendarEvent(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """An HR/recruitment calendar entry — interviews, meetings, deadlines,
    follow-ups. `start_at`/`end_at` are stored as timezone-aware instants
    (`DateTime(timezone=True)`, Postgres `timestamptz` — the same "never an
    ambiguous local timestamp" convention every other timestamp column in
    this codebase already uses); `timezone` additionally records the IANA
    zone the organizer created it in (e.g. "Asia/Kolkata"), purely for
    display — the instant itself is unambiguous regardless.

    Optionally linked to a candidate/job/application, independently and
    nullably, mirroring `Note`'s linking pattern — an event need not
    reference any of them (a plain "Team meeting").

    Reminders are fired by a DB-backed poll (app/services/
    calendar_reminder_service.py), not a queue worker (no Redis/Celery is
    provisioned — see docs/architecture.md § 12 and the free-tier
    deployment constraint). `reminder_fired_at` is the idempotency guard:
    once set, that reminder never fires again for this event, however many
    times the poll runs.
    """

    __tablename__ = "calendar_events"
    __table_args__ = (
        # The reminder poll's query: due, unfired reminders across all orgs
        # (migration a8b9c0d1e2f3).
        Index(
            "ix_calendar_events_due_reminders",
            "start_at",
            postgresql_where=text("reminder_minutes_before IS NOT NULL AND reminder_fired_at IS NULL"),
        ),
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[CalendarEventType] = mapped_column(
        Enum(CalendarEventType, name="calendar_event_type", native_enum=True),
        nullable=False,
        default=CalendarEventType.GENERAL_REMINDER,
        server_default=CalendarEventType.GENERAL_REMINDER.value,
    )
    status: Mapped[CalendarEventStatus] = mapped_column(
        Enum(CalendarEventStatus, name="calendar_event_status", native_enum=True),
        nullable=False,
        default=CalendarEventStatus.SCHEDULED,
        server_default=CalendarEventStatus.SCHEDULED.value,
    )
    start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    all_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # IANA zone name (e.g. "Asia/Kolkata") the organizer created this in —
    # display-only; start_at/end_at are already unambiguous UTC instants.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)

    organizer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # NULL = no reminder configured. Otherwise minutes before `start_at` the
    # reminder notification should fire.
    reminder_minutes_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # NULL = not yet fired (or no reminder configured). Set exactly once,
    # atomically (`UPDATE ... WHERE reminder_fired_at IS NULL`), by
    # calendar_reminder_service.process_due_reminders.
    reminder_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organizer: Mapped[User] = relationship(
        User, foreign_keys=[organizer_user_id], lazy="raise", viewonly=True
    )


class CalendarEventAttendee(Base):
    """Additional internal recipients beyond the organizer — a plain
    many-to-many join, no `organization_id` of its own (tenancy is derived
    through the event, the same indirect-RLS pattern `role_permissions`/
    `user_roles` already use — see app/db/rls.py)."""

    __tablename__ = "calendar_event_attendees"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calendar_events.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
