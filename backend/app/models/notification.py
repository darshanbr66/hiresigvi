import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User


class NotificationType(StrEnum):
    """Explicit event types — the frontend and tests key off these, never off
    the human-readable message text."""

    ASSESSMENT_STARTED = "ASSESSMENT_STARTED"
    ASSESSMENT_SUBMITTED = "ASSESSMENT_SUBMITTED"
    #: Sent by an ORG_ADMIN to everyone / a department / selected employees
    #: (app/services/in_app_notification_service.py::create_announcement).
    ANNOUNCEMENT = "ANNOUNCEMENT"
    #: One authorized portal user messaging another directly — internal
    #: portal communication, never email (create_direct_message).
    DIRECT_MESSAGE = "DIRECT_MESSAGE"
    #: A calendar event's reminder firing (Phase E) — kept in the same enum
    #: rather than a parallel notification concept, per "do not duplicate".
    CALENDAR_REMINDER = "CALENDAR_REMINDER"
    #: A user was assigned as interviewer on an application's interview
    #: (app/services/interview_service.py::schedule).
    INTERVIEW_ASSIGNED = "INTERVIEW_ASSIGNED"


class Notification(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """An in-app notification addressed to exactly one staff user (the
    recruiter/admin who sent an assessment invitation). Distinct from
    `notification_service`, which is outbound *email* — this is stored, read
    back by the recipient's own session and acknowledged (`read_at`).

    Once-only per event is a database guarantee, not application logic: the
    unique index on `(assessment_invitation_id, type)` means an invitation can
    produce at most one ASSESSMENT_STARTED and one ASSESSMENT_SUBMITTED row,
    however many requests race to create it (a retest is a new invitation row,
    so it notifies again — correctly).
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index(
            "uq_notifications_invitation_type",
            "assessment_invitation_id",
            "type",
            unique=True,
        ),
        # The poller's query: one user's unread rows, newest first.
        Index(
            "ix_notifications_recipient_unread",
            "recipient_user_id",
            "created_at",
            postgresql_where="read_at IS NULL",
        ),
        # Groups one announcement's fan-out rows for the sender's "Sent" tab.
        Index("ix_notifications_broadcast_group_id", "broadcast_group_id"),
    )

    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type", native_enum=True), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    assessment_invitation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_invitations.id", ondelete="CASCADE"),
        nullable=True,
    )
    # NULL = a system-generated notification (assessment events, calendar
    # reminders) rather than one sent by a person — the UI shows "System".
    sender_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # A generic entity reference so "open the related item" works for any
    # notification type without a dedicated FK column per feature — the same
    # polymorphic pattern app/models/activity.py already uses. No DB-level FK
    # (the referenced tables vary), so it is never trusted for tenant
    # scoping by itself — always re-verified through the normal
    # organization_id-scoped lookup for that entity type before use.
    related_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # NULL = not yet acknowledged by the recipient.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Shared by every fan-out row created from one `create_announcement` call
    # (NULL for a direct message, which is already a single row) — lets the
    # sender's "Sent" tab show one entry per broadcast instead of one per
    # recipient, without a separate "outbound message" table.
    broadcast_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # A human-readable snapshot of who an announcement targeted (e.g.
    # "Everyone in the organization", "Department: Engineering", "3 employees")
    # — denormalized onto every row in the broadcast so the sender's "Sent"
    # tab never needs to re-resolve the original target (a department can be
    # renamed or deleted after the fact; this stays accurate to what was
    # actually sent). NULL for a direct message and for system notifications.
    target_description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Read-only convenience relationships — eager-loaded by
    # in_app_notification_service so the Notification Center can show
    # "from <name>" / "to <name>" without a per-row lookup.
    sender: Mapped[User | None] = relationship(
        User, foreign_keys=[sender_user_id], lazy="raise", viewonly=True
    )
    recipient: Mapped[User] = relationship(
        User, foreign_keys=[recipient_user_id], lazy="raise", viewonly=True
    )
