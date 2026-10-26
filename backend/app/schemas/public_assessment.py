import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.assessment import InvitationStatus, MonitoringEventType, QuestionType


class PublicQuestionOption(BaseModel):
    """Never includes `is_correct` (docs/assessment.md § 2)."""

    id: uuid.UUID
    label: str


class PublicQuestion(BaseModel):
    """Never includes correct options, accepted answers, grading notes,
    explanations or negative-marking internals beyond the marks shown."""

    id: uuid.UUID
    prompt: str
    type: QuestionType
    points: int
    options: list[PublicQuestionOption]


class PublicInvitationView(BaseModel):
    status: InvitationStatus
    assessment_title: str
    instructions: str
    duration_minutes: int
    job_title: str
    organization_name: str
    expires_at: datetime
    started_at: datetime | None
    questions: list[PublicQuestion]


class AnswerSubmission(BaseModel):
    question_id: uuid.UUID
    selected_option_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    #: For short-answer, descriptive and coding questions.
    text_answer: str | None = Field(default=None, max_length=20000)


class SubmitAnswersRequest(BaseModel):
    answers: list[AnswerSubmission] = Field(max_length=500)


class PublicSubmissionResult(BaseModel):
    """Deliberately carries no score/percentage/pass-fail (SIGVITAS platform
    overhaul § 4) — the candidate sees a polished submission confirmation,
    never a number. Recruiters/admins see the full score via
    `AssessmentResultResponse` (app/schemas/assessment.py), a completely
    separate schema."""

    submitted_at: datetime


class MonitoringEventCreate(BaseModel):
    event_type: MonitoringEventType
    occurred_at: datetime
    duration_ms: int | None = Field(default=None, ge=0)
    # Deliberately small and structured — never raw audio/video, never
    # free-text beyond what the client itself controls (e.g. a device
    # label), per CLAUDE.md's "avoid collecting unnecessary personal
    # information."
    metadata: dict[str, Any] | None = None


class MonitoringEventBatchCreate(BaseModel):
    events: list[MonitoringEventCreate] = Field(min_length=1, max_length=50)


class MonitoringEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: MonitoringEventType
    occurred_at: datetime
    duration_ms: int | None
    event_metadata: dict[str, Any] | None = Field(serialization_alias="metadata")
