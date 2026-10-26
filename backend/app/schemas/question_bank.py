import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.text import collapse_whitespace
from app.models.assessment import QuestionType
from app.models.question_bank import QuestionBankStatus, QuestionDifficulty
from app.schemas.question_content import (
    MAX_ACCEPTED_ANSWERS,
    MAX_OPTIONS,
    clean_accepted_answers,
    validate_question_content,
)

MAX_TAGS_PER_QUESTION = 15


# --- Categories & tags ---


class CategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    parent_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str) -> str:
        cleaned = collapse_whitespace(value)
        if not cleaned:
            raise ValueError("Category name is required.")
        return cleaned


class CategoryUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = collapse_whitespace(value)
        if not cleaned:
            raise ValueError("Category name is required.")
        return cleaned


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    description: str | None
    is_active: bool
    #: "Programming › Python › Decorators" — the full path from the root.
    path: str = ""
    question_count: int = 0


class TagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    question_count: int = 0


class TagCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


# --- Question content ---


class BankOptionInput(BaseModel):
    label: str = Field(min_length=1, max_length=2000)
    is_correct: bool = False


class BankQuestionContent(BaseModel):
    """Everything that makes up one *version* of a bank question. Sending a
    changed value for any of these on update creates a new version."""

    type: QuestionType
    prompt: str = Field(min_length=1, max_length=20000)
    points: int = Field(default=1, ge=1, le=1000)
    negative_points: int = Field(default=0, ge=0, le=1000)
    options: list[BankOptionInput] = Field(default_factory=list, max_length=MAX_OPTIONS)
    accepted_answers: list[str] = Field(default_factory=list, max_length=MAX_ACCEPTED_ANSWERS)
    case_sensitive: bool = False
    grading_notes: str | None = Field(default=None, max_length=10000)
    explanation: str | None = Field(default=None, max_length=10000)

    @field_validator("accepted_answers")
    @classmethod
    def _clean_answers(cls, value: list[str]) -> list[str]:
        return clean_accepted_answers(value)

    @model_validator(mode="after")
    def _validate(self) -> "BankQuestionContent":
        validate_question_content(
            question_type=self.type,
            prompt=self.prompt,
            option_labels=[o.label for o in self.options],
            correct_flags=[o.is_correct for o in self.options],
            accepted_answers=self.accepted_answers,
            points=self.points,
            negative_points=self.negative_points,
        )
        return self


class BankItemCreateRequest(BankQuestionContent):
    category_id: uuid.UUID | None = None
    difficulty: QuestionDifficulty = QuestionDifficulty.MEDIUM
    #: Tag names — unknown ones are created for the organization.
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS_PER_QUESTION)
    status: QuestionBankStatus = QuestionBankStatus.DRAFT

    @field_validator("status")
    @classmethod
    def _no_archived_create(cls, value: QuestionBankStatus) -> QuestionBankStatus:
        if value == QuestionBankStatus.ARCHIVED:
            raise ValueError("A new question can't be created archived.")
        return value


class BankItemUpdateRequest(BaseModel):
    """`content`, when sent, is the complete new content: a new version is
    created if it differs from the current one. Metadata changes never
    create a version."""

    content: BankQuestionContent | None = None
    category_id: uuid.UUID | None = None
    clear_category: bool = False
    difficulty: QuestionDifficulty | None = None
    tags: list[str] | None = Field(default=None, max_length=MAX_TAGS_PER_QUESTION)


class BankItemStatusRequest(BaseModel):
    status: QuestionBankStatus


MAX_BULK_STATUS_ITEMS = 200


class BankBulkStatusRequest(BaseModel):
    item_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_BULK_STATUS_ITEMS)
    status: QuestionBankStatus


class BankBulkStatusResult(BaseModel):
    status: QuestionBankStatus
    item_ids: list[uuid.UUID]


# --- Responses (recruiter-only; they include the answer key) ---


class BankOptionResponse(BaseModel):
    key: str
    label: str
    is_correct: bool


class BankVersionResponse(BaseModel):
    id: uuid.UUID
    version_number: int
    type: QuestionType
    prompt: str
    points: int
    negative_points: int
    options: list[BankOptionResponse]
    accepted_answers: list[str]
    case_sensitive: bool
    grading_notes: str | None
    explanation: str | None
    created_at: datetime
    created_by_name: str | None = None


class BankItemResponse(BaseModel):
    id: uuid.UUID
    status: QuestionBankStatus
    difficulty: QuestionDifficulty
    category_id: uuid.UUID | None
    category_path: str | None
    tags: list[str]
    current_version: BankVersionResponse
    version_count: int
    #: How many assessments snapshot any version of this question.
    assessment_usage_count: int = 0
    created_at: datetime
    updated_at: datetime


class BankItemListEntry(BaseModel):
    """List rows carry the prompt and organization only — never the answer
    key, so a long result page doesn't ship every key to the browser."""

    id: uuid.UUID
    status: QuestionBankStatus
    difficulty: QuestionDifficulty
    type: QuestionType
    prompt: str
    points: int
    category_id: uuid.UUID | None
    category_path: str | None
    tags: list[str]
    version_number: int
    #: What the test builder sends as `bank_version_id`.
    current_version_id: uuid.UUID
    updated_at: datetime


class BankPreviewOption(BaseModel):
    key: str
    label: str


class BankItemPreview(BaseModel):
    """What a candidate would see — no correctness, no notes."""

    type: QuestionType
    prompt: str
    points: int
    options: list[BankPreviewOption]


# --- File import (docs/assessment.md § 10a) ---

MAX_IMPORT_ITEMS = 500


class BankImportCandidate(BaseModel):
    """One question read from an uploaded file, shown for review before
    anything is saved. `issue` is set when the shared content rules reject
    it — such a question can't be imported until the file is fixed."""

    source: str
    content: BankQuestionContent | None
    #: The raw question text, kept even when `content` is rejected.
    prompt: str
    issue: str | None = None
    difficulty: QuestionDifficulty
    subject: str | None
    topic: str | None
    #: "Subject › Topic" as it will be filed; None = uncategorized.
    category_path: str | None
    #: True when that subject/topic doesn't exist yet and would be created.
    creates_category: bool
    tags: list[str]
    #: An existing bank question with the same text, if any.
    duplicate_of_item_id: uuid.UUID | None
    #: Same text as an earlier question in this file.
    duplicate_in_file: bool


class BankImportPreview(BaseModel):
    filename: str
    questions: list[BankImportCandidate]
    #: Rows/questions that couldn't be read at all, each with its reason.
    warnings: list[str]


class BankImportItem(BaseModel):
    content: BankQuestionContent
    difficulty: QuestionDifficulty = QuestionDifficulty.MEDIUM
    subject: str | None = Field(default=None, max_length=150)
    topic: str | None = Field(default=None, max_length=150)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS_PER_QUESTION)

    @model_validator(mode="after")
    def _topic_needs_subject(self) -> "BankImportItem":
        if self.topic and not (self.subject and self.subject.strip()):
            raise ValueError("A topic needs a subject.")
        return self


class BankImportRequest(BaseModel):
    """The reviewed questions to save — all of them, in one transaction, or
    none if any is invalid."""

    items: list[BankImportItem] = Field(min_length=1, max_length=MAX_IMPORT_ITEMS)
    status: QuestionBankStatus = QuestionBankStatus.DRAFT

    @field_validator("status")
    @classmethod
    def _no_archived(cls, value: QuestionBankStatus) -> QuestionBankStatus:
        if value == QuestionBankStatus.ARCHIVED:
            raise ValueError("Imported questions are saved as draft or published.")
        return value


class BankImportResult(BaseModel):
    imported: int
    item_ids: list[uuid.UUID]
    categories_created: int
    #: What the questions were saved as — drafts can't be added to a test
    #: until they are published.
    status: QuestionBankStatus
