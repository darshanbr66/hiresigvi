import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class QuestionType(StrEnum):
    """Choice types (MCQ_SINGLE, MCQ_MULTI, TRUE_FALSE) are always graded
    automatically from `QuestionOption.is_correct`. SHORT_ANSWER is graded
    automatically against `QuestionAnswerKey.accepted_answers` when any are
    configured, otherwise manually. DESCRIPTIVE and CODING are always graded
    manually by HR. CODING answers are stored as text and reviewed by a
    person: the platform has no code-execution engine and never pretends to
    run them (docs/assessment.md § 10)."""

    MCQ_SINGLE = "MCQ_SINGLE"
    MCQ_MULTI = "MCQ_MULTI"
    TRUE_FALSE = "TRUE_FALSE"
    SHORT_ANSWER = "SHORT_ANSWER"
    DESCRIPTIVE = "DESCRIPTIVE"
    CODING = "CODING"


#: Types answered by picking options (`CandidateAnswer.selected_option_ids`).
CHOICE_QUESTION_TYPES: frozenset[QuestionType] = frozenset(
    {QuestionType.MCQ_SINGLE, QuestionType.MCQ_MULTI, QuestionType.TRUE_FALSE}
)
#: Types answered with free text (`CandidateAnswer.text_answer`).
TEXT_QUESTION_TYPES: frozenset[QuestionType] = frozenset(
    {QuestionType.SHORT_ANSWER, QuestionType.DESCRIPTIVE, QuestionType.CODING}
)
#: Types that always need a person to award marks.
MANUAL_QUESTION_TYPES: frozenset[QuestionType] = frozenset(
    {QuestionType.DESCRIPTIVE, QuestionType.CODING}
)


class AssessmentStatus(StrEnum):
    """DRAFT assessments can be built and previewed but not assigned to a
    candidate, a campus drive or a program; PUBLISHED ones can. Archiving
    is the existing soft delete (`deleted_at`)."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class GradingMethod(StrEnum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


class ResultGradingStatus(StrEnum):
    """FINAL: every answer has marks, so `score`/`percentage`/`passed` are
    the outcome. PENDING_MANUAL_REVIEW: at least one answer still waits for
    a grader, and `passed` is NULL until it is graded."""

    FINAL = "FINAL"
    PENDING_MANUAL_REVIEW = "PENDING_MANUAL_REVIEW"


class InvitationStatus(StrEnum):
    SENT = "SENT"
    STARTED = "STARTED"
    SUBMITTED = "SUBMITTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class Assessment(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """An org-owned, reusable assessment definition (docs/assessment.md § 1)
    — independent of why a candidate is invited (normal recruitment or a
    campus drive). MVP scope: MCQ only, matching docs/assessment.md's
    stated initial build."""

    __tablename__ = "assessments"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    pass_score: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    status: Mapped[AssessmentStatus] = mapped_column(
        Enum(AssessmentStatus, name="assessment_status", native_enum=True),
        nullable=False,
        default=AssessmentStatus.PUBLISHED,
        server_default=AssessmentStatus.PUBLISHED.value,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    # Soft delete/archive — same rationale/shape as Candidate's (app/models/
    # candidate.py). AssessmentInvitation.assessment_id is ON DELETE
    # RESTRICT, so a hard DELETE is already impossible once any invitation
    # exists; this makes "archive, never destroy" the deliberate behavior
    # everywhere rather than only where the FK happens to enforce it.
    # Deleted assessments drop out of `assessment_service.list_assessments`
    # and out of the assessment picker, but every AssessmentInvitation/
    # AssessmentResult that already used them stays intact and readable.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deletion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    questions: Mapped[list["Question"]] = relationship(
        "Question", lazy="raise", order_by="Question.order_index", viewonly=True
    )


class Question(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Belongs to exactly one Assessment. A question added from the
    organization's question bank is a *snapshot* of one bank version
    (`source_bank_version_id` records which), never a live reference, so
    editing the bank question later can never change this assessment or any
    attempt at it (docs/assessment.md § 10). Tenancy is derived through
    assessment_id, same indirect-RLS pattern as application_status_history.
    """

    __tablename__ = "questions"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[QuestionType] = mapped_column(
        Enum(QuestionType, name="question_type", native_enum=True), nullable=False
    )
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Deducted for a wrong (non-blank) answer to an automatically graded
    # question; 0 = no negative marking. Never more than `points`.
    negative_points: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_bank_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("question_bank_item_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    options: Mapped[list["QuestionOption"]] = relationship(
        "QuestionOption", lazy="raise", order_by="QuestionOption.order_index", viewonly=True
    )
    answer_key: Mapped["QuestionAnswerKey | None"] = relationship(
        "QuestionAnswerKey", lazy="raise", uselist=False, viewonly=True
    )


class QuestionAnswerKey(UUIDPrimaryKeyMixin, Base):
    """Private grading data for a text-answer question (accepted short
    answers, grader notes, explanation), kept out of `questions` so the
    candidate-facing loaders never touch it. Choice questions keep their key
    on `QuestionOption.is_correct`, which no public schema exposes.
    Tenancy is derived through question -> assessment."""

    __tablename__ = "question_answer_keys"

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    accepted_answers: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    case_sensitive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    grading_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuestionOption(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "question_options"

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    # Never sent to the candidate (docs/assessment.md § 2) — excluded from
    # the public-facing schema in app/schemas/public_assessment.py.
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AssessmentInvitation(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """The secure, single-purpose link from one Application to one
    Assessment (docs/assessment.md § 3-4). Token is shown once and stored
    only as a hash (`app.core.security.hash_opaque_token`) — never in
    plaintext (CLAUDE.md § 4).

    Simplified from docs/assessment.md's full lifecycle: no
    DELIVERED/OPENED webhook states — a real gap noted here rather than
    silently designed around, not silently dropped.

    Multiple rows per application are allowed (`application_id` is
    indexed, not unique) — each is one attempt, numbered by
    `attempt_number`. Attempt 1 is created by `invite_candidate`
    (resending before it's ever started/submitted reuses that same row,
    matching docs/campus-hiring.md § 3); attempt 2+ is created by
    `create_retest` as a brand-new row so the prior attempt's row (and its
    linked AssessmentResult/CandidateAnswers) is never mutated or deleted —
    retest history must stay intact (CLAUDE.md § 3).
    """

    __tablename__ = "assessment_invitations"
    __table_args__ = ()

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[InvitationStatus] = mapped_column(
        Enum(InvitationStatus, name="assessment_invitation_status", native_enum=True),
        nullable=False,
        default=InvitationStatus.SENT,
        server_default=InvitationStatus.SENT.value,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When a recruiter last emailed this invitation to the candidate (NULL =
    # prepared but never emailed). `status = SENT` predates manual email and
    # does not imply an email went out; this is the honest record.
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    retest_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    assessment: Mapped["Assessment"] = relationship("Assessment", lazy="raise", viewonly=True)
    result: Mapped["AssessmentResult | None"] = relationship(
        "AssessmentResult", lazy="raise", uselist=False, viewonly=True
    )


class CandidateAnswer(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "candidate_answers"

    invitation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_invitations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    selected_option_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Free-text answer for SHORT_ANSWER / DESCRIPTIVE / CODING questions.
    text_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Grading: set at submission for automatically graded questions, by a
    # grader (assessment_grading_service) for manual ones. NULL
    # `awarded_points` = not graded yet.
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    awarded_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grading_method: Mapped[GradingMethod | None] = mapped_column(
        Enum(GradingMethod, name="grading_method", native_enum=True), nullable=True
    )
    graded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # HR-only; never shown to the candidate.
    grader_comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class AssessmentResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "assessment_results"

    invitation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_invitations.id", ondelete="CASCADE"),
        nullable=False,
        # A unique *constraint* (uq_…), as migration 89d2c5e04700 created it.
        unique=True,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    max_score: Mapped[int] = mapped_column(Integer, nullable=False)
    percentage: Mapped[int] = mapped_column(Integer, nullable=False)
    # NULL only while `grading_status` is PENDING_MANUAL_REVIEW.
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    grading_status: Mapped[ResultGradingStatus] = mapped_column(
        Enum(ResultGradingStatus, name="result_grading_status", native_enum=True),
        nullable=False,
        default=ResultGradingStatus.FINAL,
        server_default=ResultGradingStatus.FINAL.value,
    )
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MonitoringEventType(StrEnum):
    """Transparent, browser-observable assessment-monitoring events (SIGVITAS
    platform overhaul § 6) — an event is an *observed* browser condition,
    never an automatic accusation. Only events genuinely reliable to detect
    in a standard browser are listed; do not add one that can't actually be
    implemented (§ 5/6: no overclaiming to the candidate)."""

    MONITORING_CONSENT_GIVEN = "MONITORING_CONSENT_GIVEN"
    TAB_SWITCH = "TAB_SWITCH"
    WINDOW_BLUR = "WINDOW_BLUR"
    WINDOW_FOCUS = "WINDOW_FOCUS"
    FULLSCREEN_EXIT = "FULLSCREEN_EXIT"
    CAMERA_PERMISSION_CHANGED = "CAMERA_PERMISSION_CHANGED"
    MICROPHONE_PERMISSION_CHANGED = "MICROPHONE_PERMISSION_CHANGED"
    CAMERA_DEVICE_CHANGED = "CAMERA_DEVICE_CHANGED"
    MICROPHONE_DEVICE_CHANGED = "MICROPHONE_DEVICE_CHANGED"
    CAMERA_UNAVAILABLE = "CAMERA_UNAVAILABLE"
    MICROPHONE_UNAVAILABLE = "MICROPHONE_UNAVAILABLE"
    CONNECTION_INTERRUPTED = "CONNECTION_INTERRUPTED"
    CONNECTION_RESTORED = "CONNECTION_RESTORED"


class AssessmentMonitoringEvent(UUIDPrimaryKeyMixin, Base):
    """One observed browser-monitoring event during a candidate's attempt.
    No `organization_id` of its own — tenancy is derived through
    `invitation_id -> assessment_invitations.organization_id`, same indirect
    pattern as `CandidateAnswer`/`AssessmentResult` above.

    Deliberately minimal: no raw audio/video, no free-text beyond a small
    JSON `event_metadata` blob (e.g. a device label), matching CLAUDE.md's
    "do not store raw audio/video unless there is a clearly defined, secure
    requirement and architecture for it" — there isn't one here, so none is
    stored.
    """

    __tablename__ = "assessment_monitoring_events"

    invitation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_invitations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[MonitoringEventType] = mapped_column(
        Enum(MonitoringEventType, name="monitoring_event_type", native_enum=True), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
