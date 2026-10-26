"""The organization's reusable question bank (docs/assessment.md § 10).

A bank question is an *item* with immutable, numbered *versions*. Editing a
question's content adds a new version; nothing ever rewrites an old one.
An assessment never points at the bank live — adding a bank question to a
test snapshots that exact version into the assessment's own `questions`
rows (`Question.source_bank_version_id` records where it came from), so a
later bank edit can never change a test candidates have already taken.

Answer keys live in their own table (`question_bank_answer_keys`), one row
per version, so nothing that only needs question content ever loads the
key. Candidate-facing code never reads any table in this module.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.assessment import QuestionType


class QuestionDifficulty(StrEnum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"


class QuestionBankStatus(StrEnum):
    """DRAFT items can be edited and previewed but never added to a test;
    PUBLISHED items are selectable in the test builder; ARCHIVED items drop
    out of the picker but every test that already snapshotted them is
    untouched."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


QUESTION_BANK_TRANSITIONS: dict[QuestionBankStatus, frozenset[QuestionBankStatus]] = {
    QuestionBankStatus.DRAFT: frozenset({QuestionBankStatus.PUBLISHED, QuestionBankStatus.ARCHIVED}),
    QuestionBankStatus.PUBLISHED: frozenset({QuestionBankStatus.DRAFT, QuestionBankStatus.ARCHIVED}),
    QuestionBankStatus.ARCHIVED: frozenset({QuestionBankStatus.DRAFT}),
}


class QuestionCategory(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A subject/field, or a topic/subtopic under one (`parent_id`). HR
    creates these — "Python", "Mechatronics", "Aptitude" are data, never a
    hardcoded list. Names are unique among siblings (two partial indexes,
    because NULL parents never compare equal in a plain unique index)."""

    __tablename__ = "question_categories"
    __table_args__ = (
        Index(
            "uq_question_categories_root_name",
            "organization_id",
            "normalized_name",
            unique=True,
            postgresql_where="parent_id IS NULL",
        ),
        Index(
            "uq_question_categories_child_name",
            "organization_id",
            "parent_id",
            "normalized_name",
            unique=True,
            postgresql_where="parent_id IS NOT NULL",
        ),
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(150), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("question_categories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class QuestionTag(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "question_tags"
    __table_args__ = (
        Index("uq_question_tags_org_normalized_name", "organization_id", "normalized_name", unique=True),
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QuestionBankItemTag(Base):
    __tablename__ = "question_bank_item_tags"

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_bank_items.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class QuestionBankItem(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """The stable identity of one bank question. `current_version_number`
    names its newest `QuestionBankItemVersion`; organization (category,
    difficulty, tags, status) lives here, content lives on the versions."""

    __tablename__ = "question_bank_items"
    __table_args__ = (
        Index("ix_question_bank_items_org_status", "organization_id", "status"),
    )

    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("question_categories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    difficulty: Mapped[QuestionDifficulty] = mapped_column(
        Enum(QuestionDifficulty, name="question_difficulty", native_enum=True),
        nullable=False,
        default=QuestionDifficulty.MEDIUM,
        server_default=QuestionDifficulty.MEDIUM.value,
    )
    status: Mapped[QuestionBankStatus] = mapped_column(
        Enum(QuestionBankStatus, name="question_bank_status", native_enum=True),
        nullable=False,
        default=QuestionBankStatus.DRAFT,
        server_default=QuestionBankStatus.DRAFT.value,
    )
    current_version_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    category: Mapped[QuestionCategory | None] = relationship(
        QuestionCategory, lazy="raise", viewonly=True
    )
    tags: Mapped[list[QuestionTag]] = relationship(
        QuestionTag,
        secondary="question_bank_item_tags",
        lazy="raise",
        viewonly=True,
        order_by="QuestionTag.name",
    )
    versions: Mapped[list["QuestionBankItemVersion"]] = relationship(
        "QuestionBankItemVersion",
        lazy="raise",
        viewonly=True,
        order_by="QuestionBankItemVersion.version_number",
    )


class QuestionBankItemVersion(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """Immutable question content. `options` is `[{"key": str, "label":
    str}]` in display order (empty for text-answer types); which keys are
    correct is on the separate answer-key row."""

    __tablename__ = "question_bank_item_versions"
    __table_args__ = (
        Index("uq_question_bank_item_versions_item_version", "item_id", "version_number", unique=True),
        CheckConstraint("points >= 1", name="ck_question_bank_item_versions_points_positive"),
        CheckConstraint(
            "negative_points >= 0 AND negative_points <= points",
            name="ck_question_bank_item_versions_negative_points_range",
        ),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_bank_items.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[QuestionType] = mapped_column(
        Enum(QuestionType, name="question_type", native_enum=True, create_type=False), nullable=False
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    negative_points: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    options: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    answer_key: Mapped["QuestionBankAnswerKey"] = relationship(
        "QuestionBankAnswerKey", lazy="raise", uselist=False, viewonly=True
    )


class QuestionBankAnswerKey(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """The private grading data for one version: correct option keys
    (choice types), accepted answers (short answer), and HR-only grading
    notes/explanation. Never part of any candidate-facing schema."""

    __tablename__ = "question_bank_answer_keys"

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("question_bank_item_versions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    correct_option_keys: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    accepted_answers: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    case_sensitive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    grading_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
