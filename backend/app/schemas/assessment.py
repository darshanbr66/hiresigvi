import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.assessment import (
    AssessmentStatus,
    GradingMethod,
    InvitationStatus,
    QuestionType,
    ResultGradingStatus,
)
from app.schemas.question_content import (
    MAX_ACCEPTED_ANSWERS,
    MAX_OPTIONS,
    clean_accepted_answers,
    validate_question_content,
)


class QuestionOptionCreate(BaseModel):
    label: str = Field(min_length=1)
    is_correct: bool = False


class QuestionCreate(BaseModel):
    """One question of an assessment — either written directly
    (`prompt`/`type`/`options`/answer key), or taken from the question bank
    (`bank_version_id`), in which case the server copies that exact version
    (content *and* answer key) and ignores any content sent alongside it;
    only `points` may be overridden for this test (docs/assessment.md § 10).
    """

    prompt: str = ""
    type: QuestionType = QuestionType.MCQ_SINGLE
    #: None = the default (1 for a written question, the bank version's own
    #: marks for a bank question).
    points: int | None = Field(default=None, ge=1, le=1000)
    negative_points: int = Field(default=0, ge=0, le=1000)
    options: list[QuestionOptionCreate] = Field(default_factory=list, max_length=MAX_OPTIONS)
    accepted_answers: list[str] = Field(default_factory=list, max_length=MAX_ACCEPTED_ANSWERS)
    case_sensitive: bool = False
    grading_notes: str | None = Field(default=None, max_length=10000)
    explanation: str | None = Field(default=None, max_length=10000)
    bank_version_id: uuid.UUID | None = None

    @field_validator("accepted_answers")
    @classmethod
    def _clean_answers(cls, value: list[str]) -> list[str]:
        return clean_accepted_answers(value)

    @model_validator(mode="after")
    def _validate_content(self) -> "QuestionCreate":
        if self.bank_version_id is not None:
            return self
        validate_question_content(
            question_type=self.type,
            prompt=self.prompt,
            option_labels=[o.label for o in self.options],
            correct_flags=[o.is_correct for o in self.options],
            accepted_answers=self.accepted_answers,
            points=self.points or 1,
            negative_points=self.negative_points,
        )
        return self


class AssessmentCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    instructions: str = Field(min_length=1)
    duration_minutes: int = Field(default=30, ge=1, le=480)
    pass_score: int = Field(default=60, ge=0, le=100)
    questions: list[QuestionCreate] = Field(min_length=1)
    #: PUBLISHED (the default, matching how assessments always behaved) can
    #: be assigned right away; DRAFT is saved for later publishing.
    status: AssessmentStatus = AssessmentStatus.PUBLISHED


class ParsedQuestionsResponse(BaseModel):
    """Response for the "Import Questions" preview step — nothing here has
    been persisted yet. `questions` is shaped exactly like
    `AssessmentCreateRequest.questions` so the frontend can preview, edit,
    reorder, and select before sending the final create request unchanged.
    """

    questions: list[QuestionCreate]
    warnings: list[str]


class QuestionOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    is_correct: bool


class QuestionResponse(BaseModel):
    """Recruiter-only: carries the answer key."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    prompt: str
    type: QuestionType
    points: int
    negative_points: int = 0
    options: list[QuestionOptionResponse]
    accepted_answers: list[str] = []
    case_sensitive: bool = False
    grading_notes: str | None = None
    explanation: str | None = None
    source_bank_version_id: uuid.UUID | None = None


class AssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    instructions: str
    duration_minutes: int
    pass_score: int
    status: AssessmentStatus = AssessmentStatus.PUBLISHED
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    questions: list[QuestionResponse] = []
    # Computed, not an Assessment column — set by the route/service layer
    # (assessment_service.assessment_has_invitations), never by
    # `.model_validate(assessment)` alone. True once any candidate could
    # have been invited, which is exactly when question-structure edits
    # become locked (see AssessmentUpdateRequest).
    has_invitations: bool = False


class AssessmentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    duration_minutes: int
    pass_score: int
    question_count: int
    status: AssessmentStatus = AssessmentStatus.PUBLISHED
    created_at: datetime


class AssessmentUpdateRequest(BaseModel):
    """All fields optional — a PATCH applies only what the caller sends
    (`exclude_unset=True` at the service layer, mirroring
    `JobUpdateRequest`). `questions`, when provided, *replaces* the entire
    question set — only legal while no `AssessmentInvitation` exists yet for
    this assessment (see assessment_service.update_assessment); once a
    candidate could have been invited, question content is frozen so a
    completed/in-progress attempt is never invalidated retroactively."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    instructions: str | None = Field(default=None, min_length=1)
    duration_minutes: int | None = Field(default=None, ge=1, le=480)
    pass_score: int | None = Field(default=None, ge=0, le=100)
    questions: list[QuestionCreate] | None = Field(default=None, min_length=1)


class AssessmentDeleteRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class InviteCandidateRequest(BaseModel):
    assessment_id: uuid.UUID
    application_id: uuid.UUID


class RetestAssessmentChoice(StrEnum):
    """How a retest picks the assessment for the new attempt (QA § 6) — the
    previous attempt's own assessment/score/answers are never touched
    regardless of which of these is chosen (app/services/
    assessment_service.py::create_retest)."""

    SAME = "SAME"
    EXISTING = "EXISTING"
    NEW = "NEW"


class RetestRequest(BaseModel):
    application_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=1000)
    assessment_choice: RetestAssessmentChoice = RetestAssessmentChoice.SAME
    # Required when assessment_choice == EXISTING.
    assessment_id: uuid.UUID | None = None
    # Required when assessment_choice == NEW — reuses the same
    # create-assessment shape (and question-import workflow feeding it) as
    # a normal assessment creation, per QA § 6 ("do not duplicate code").
    new_assessment: AssessmentCreateRequest | None = None

    @model_validator(mode="after")
    def _validate_choice(self) -> "RetestRequest":
        if self.assessment_choice == RetestAssessmentChoice.EXISTING and self.assessment_id is None:
            raise ValueError("assessment_id is required when assessment_choice is EXISTING.")
        if self.assessment_choice == RetestAssessmentChoice.NEW and self.new_assessment is None:
            raise ValueError("new_assessment is required when assessment_choice is NEW.")
        return self


class AssessmentInvitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    assessment_id: uuid.UUID
    assessment_title: str
    application_id: uuid.UUID
    candidate_full_name: str
    status: InvitationStatus
    expires_at: datetime
    started_at: datetime | None
    submitted_at: datetime | None
    emailed_at: datetime | None = None
    attempt_number: int
    retest_reason: str | None
    result: "AssessmentResultResponse | None" = None
    invitation_link: str | None = None


class AssessmentResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    score: int
    max_score: int
    percentage: int
    #: None while `grading_status` is PENDING_MANUAL_REVIEW.
    passed: bool | None
    grading_status: ResultGradingStatus = ResultGradingStatus.FINAL
    evaluated_at: datetime


class AssessmentStatusChangeRequest(BaseModel):
    status: AssessmentStatus


class AttemptAnswerView(BaseModel):
    """One question of a submitted attempt, for a recruiter/grader: the
    candidate's answer next to the answer key, and its marks."""

    question_id: uuid.UUID
    order: int
    prompt: str
    type: QuestionType
    points: int
    negative_points: int
    options: list[QuestionOptionResponse]
    accepted_answers: list[str]
    grading_notes: str | None
    explanation: str | None
    answer_id: uuid.UUID | None
    selected_option_ids: list[uuid.UUID]
    text_answer: str | None
    is_correct: bool | None
    awarded_points: int | None
    grading_method: GradingMethod | None
    needs_manual_grading: bool
    #: A text answer a grader may (re-)grade; choice answers are automatic.
    manually_gradable: bool = False
    graded_by_name: str | None
    graded_at: datetime | None
    grader_comment: str | None


class AttemptReviewResponse(BaseModel):
    invitation_id: uuid.UUID
    application_id: uuid.UUID
    candidate_full_name: str
    assessment_title: str
    attempt_number: int
    status: InvitationStatus
    submitted_at: datetime | None
    result: "AssessmentResultResponse | None"
    answers: list[AttemptAnswerView]


class GradeEntry(BaseModel):
    question_id: uuid.UUID
    awarded_points: int = Field(ge=0)
    comment: str | None = Field(default=None, max_length=5000)


class GradeAttemptRequest(BaseModel):
    grades: list[GradeEntry] = Field(min_length=1, max_length=500)
