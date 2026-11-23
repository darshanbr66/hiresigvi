"""Request/response shapes of the progressive self-service apply flow
(app/api/v1/public/apply_flow.py). Every request that touches a draft
carries the email + the verification token from
`POST .../email-verification/verify`; the token is what authorizes it."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.phone import InvalidPhoneNumberError, normalize_phone
from app.models.application_draft import EducationCheckStatus
from app.schemas.candidate import ApplicantBackground, BlankToNone

#: Earliest completion year accepted; the latest is a few years ahead, for
#: candidates still completing their degree.
MIN_COMPLETION_YEAR = 1960
MAX_YEARS_AHEAD = 6


class DraftAuth(BaseModel):
    email: EmailStr
    email_verification_token: str = Field(min_length=1, max_length=200)


class DraftStart(DraftAuth):
    """Step 1's details, sent once the email is verified."""

    full_name: str = Field(min_length=1)
    phone: str = Field(min_length=1, max_length=32)

    @field_validator("full_name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        letters = sum(ch.isalpha() for ch in cleaned)
        if letters < 2:
            raise ValueError("Enter your full name.")
        if any(ch.isdigit() for ch in cleaned) or any(ch in "<>{}[]@#$%^*=_|\\/" for ch in cleaned):
            raise ValueError("A name can contain letters, spaces, dots, hyphens and apostrophes.")
        return cleaned

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, value: str) -> str:
        try:
            return normalize_phone(value)
        except InvalidPhoneNumberError as exc:
            raise ValueError(str(exc)) from exc


class BackgroundSave(DraftAuth):
    background: ApplicantBackground


class EducationDetails(BaseModel):
    qualification_key: str = Field(min_length=1, max_length=50)
    #: Required only for a catch-all ("Other bachelor's degree") option.
    degree_name: Annotated[str | None, BlankToNone] = Field(default=None, max_length=150)
    branch: Annotated[str | None, BlankToNone] = Field(default=None, max_length=150)
    institution: Annotated[str, BlankToNone] = Field(min_length=1, max_length=255)
    completion_year: int
    tenth_marks_percentage: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2
    )

    @field_validator("completion_year")
    @classmethod
    def _check_year(cls, value: int) -> int:
        latest = datetime.now(UTC).year + MAX_YEARS_AHEAD
        if not MIN_COMPLETION_YEAR <= value <= latest:
            raise ValueError(f"Enter a year between {MIN_COMPLETION_YEAR} and {latest}.")
        return value


class EducationSave(DraftAuth):
    education: EducationDetails


class QualificationOptionView(BaseModel):
    key: str
    label: str
    level: str
    branch_label: str
    branch_required: bool
    requires_degree_name: bool
    document_hint: str


class QualificationOptionsView(BaseModel):
    """What the Education step offers for this job — derived from the JD,
    never a global list. `restricted=false` means the JD doesn't clearly
    require a particular qualification, so every option is offered."""

    restricted: bool
    summary: str
    options: list[QualificationOptionView]
    fields_of_study: list[str]
    field_mandatory: bool
    accepted_document_types: list[str]
    max_document_size_mb: int


class AcademicDocumentView(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime


class EducationCheckView(BaseModel):
    """The academic-document check as the candidate sees it — a status and
    a plain-language message, never the model's raw output, provider or
    the document's extracted contents."""

    status: EducationCheckStatus
    message: str | None
    #: VERIFIED or MANUAL_REVIEW: the Education step is complete.
    can_continue: bool
    #: The check can be run again on the same document.
    can_retry: bool
    #: The candidate may continue and leave the document to HR.
    can_request_manual_review: bool
    attempts_remaining: int


class DraftView(BaseModel):
    email: str
    full_name: str
    phone: str
    background: ApplicantBackground | None
    education: EducationDetails | None
    document: AcademicDocumentView | None
    education_check: EducationCheckView
    expires_at: datetime
