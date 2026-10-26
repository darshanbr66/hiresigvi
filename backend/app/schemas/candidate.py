import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.core.phone import InvalidPhoneNumberError, canonical_phone_or_none, normalize_phone
from app.models.candidate import CandidateSource, CandidateType, EducationLevel


def _blank_to_none(value: object) -> object:
    """Form submissions and cleared inputs send "" for "not provided"."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


BlankToNone = BeforeValidator(_blank_to_none)


def _normalize_profile_url(value: str | None, *, allowed_hosts: tuple[str, ...]) -> str | None:
    """Accepts `linkedin.com/in/x` as well as a full URL; rejects anything
    that isn't http(s) on the expected site, so this field can never hold a
    `javascript:` URL that a recruiter UI might later render as a link."""
    if value is None:
        return None
    candidate = value if "://" in value else f"https://{value}"
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not any(
        host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts
    ):
        raise ValueError(f"Must be a valid {allowed_hosts[0]} URL.")
    return candidate


MAX_LANGUAGES = 15
# Letters (any script) in words joined by single spaces, hyphens,
# apostrophes or dots — no digits, markup or punctuation runs.
_LANGUAGE_NAME = re.compile(r"^[^\W\d_]+(?:[ '\-.][^\W\d_]+)*$")
MIN_APPLICANT_AGE_YEARS = 16
MAX_APPLICANT_AGE_YEARS = 100


def normalize_languages(values: list[str]) -> list[str]:
    """Trims, validates, de-duplicates case-insensitively (first spelling
    wins) and title-cases all-lower/all-upper input ("tamil" -> "Tamil").
    The one normalization every write path uses, so "English" and "english"
    are never stored as two languages."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        name = " ".join(str(raw).split())
        if not name:
            continue
        if len(name) > 50 or not _LANGUAGE_NAME.match(name):
            raise ValueError(f'"{name[:50]}" is not a valid language name.')
        if name.islower() or name.isupper():
            name = name.title()
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    if len(result) > MAX_LANGUAGES:
        raise ValueError(f"List at most {MAX_LANGUAGES} languages.")
    return result


def _age_on(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def validate_date_of_birth(value: date) -> date:
    today = datetime.now(UTC).date()
    if value >= today:
        raise ValueError("Date of birth must be in the past.")
    age = _age_on(value, today)
    if age < MIN_APPLICANT_AGE_YEARS:
        raise ValueError(f"Applicants must be at least {MIN_APPLICANT_AGE_YEARS} years old.")
    if age > MAX_APPLICANT_AGE_YEARS:
        raise ValueError("Enter a valid date of birth.")
    return value


class CandidateProfileFields(BaseModel):
    """Reusable profile data shared by the public application form and the
    recruiter create/update requests. `location` is the *current*
    location. Optional here: recruiter-added and imported candidates may not
    have them (the public form's stricter rules live on
    `PublicApplicantProfile`)."""

    location: Annotated[str | None, BlankToNone] = Field(default=None, max_length=255)
    current_title: Annotated[str | None, BlankToNone] = Field(default=None, max_length=255)
    current_company: Annotated[str | None, BlankToNone] = Field(default=None, max_length=255)
    preferred_location: Annotated[str | None, BlankToNone] = Field(default=None, max_length=255)
    years_experience: int | None = Field(default=None, ge=0, le=80)
    candidate_type: CandidateType | None = None
    notice_period_days: int | None = Field(default=None, ge=0, le=365)
    immediate_joiner: bool | None = None
    qualification: Annotated[str | None, BlankToNone] = Field(default=None, max_length=255)
    linkedin_url: Annotated[str | None, BlankToNone] = Field(default=None, max_length=500)
    github_url: Annotated[str | None, BlankToNone] = Field(default=None, max_length=500)
    date_of_birth: date | None = None
    place_of_birth: Annotated[str | None, BlankToNone] = Field(default=None, max_length=255)
    languages: list[str] | None = None

    # Field validators (not a model validator) so a rejected URL is reported
    # against its own field, and so they only run for values actually
    # supplied — which keeps `exclude_unset` PATCH semantics intact.
    @field_validator("linkedin_url")
    @classmethod
    def _check_linkedin_url(cls, value: str | None) -> str | None:
        return _normalize_profile_url(value, allowed_hosts=("linkedin.com",))

    @field_validator("github_url")
    @classmethod
    def _check_github_url(cls, value: str | None) -> str | None:
        return _normalize_profile_url(value, allowed_hosts=("github.com",))

    @field_validator("languages")
    @classmethod
    def _check_languages(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_languages(value)

    @field_validator("date_of_birth")
    @classmethod
    def _check_date_of_birth(cls, value: date | None) -> date | None:
        return None if value is None else validate_date_of_birth(value)

    @model_validator(mode="after")
    def _immediate_joiner_has_no_notice_period(self) -> "CandidateProfileFields":
        if self.immediate_joiner:
            self.notice_period_days = 0
        return self


class PublicApplicantProfile(BaseModel):
    """Everything the public (self-service) application form collects. All
    of it is mandatory except the LinkedIn/GitHub profile URLs and the 10th
    marks percentage, which are optional (validated when supplied): an
    incomplete submission is rejected here even if the React form is
    bypassed. The full name is required but has no length limit. The fields
    that only make sense for an
    experienced professional (experience, current role, availability) are
    required exactly when `candidate_type == EXPERIENCED`; a FRESHER carries
    0 years of experience, no current role and no notice period.

    Field names match `CandidateProfileFields` so the service can copy the
    profile onto a Candidate with the same `model_dump(include=...)`."""

    full_name: str = Field(min_length=1)
    phone: str = Field(min_length=1, max_length=32)
    date_of_birth: date
    place_of_birth: Annotated[str, BlankToNone] = Field(min_length=1, max_length=255)
    languages: list[str] = Field(min_length=1)
    candidate_type: CandidateType
    location: Annotated[str, BlankToNone] = Field(min_length=1, max_length=255)
    preferred_location: Annotated[str, BlankToNone] = Field(min_length=1, max_length=255)
    qualification: Annotated[str, BlankToNone] = Field(min_length=1, max_length=255)
    linkedin_url: Annotated[str | None, BlankToNone] = Field(default=None, max_length=500)
    github_url: Annotated[str | None, BlankToNone] = Field(default=None, max_length=500)
    #: The 10th-standard result as a percentage — optional for now.
    tenth_marks_percentage: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2
    )
    years_experience: int | None = Field(default=None, ge=0, le=80)
    current_title: Annotated[str | None, BlankToNone] = Field(default=None, max_length=255)
    current_company: Annotated[str | None, BlankToNone] = Field(default=None, max_length=255)
    notice_period_days: int | None = Field(default=None, ge=0, le=365)
    immediate_joiner: bool | None = None

    @field_validator("full_name")
    @classmethod
    def _clean_full_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Full name is required.")
        return cleaned

    @field_validator("phone")
    @classmethod
    def _normalize_phone(cls, value: str) -> str:
        try:
            return normalize_phone(value)
        except InvalidPhoneNumberError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("date_of_birth")
    @classmethod
    def _check_date_of_birth(cls, value: date) -> date:
        return validate_date_of_birth(value)

    @field_validator("languages")
    @classmethod
    def _check_languages(cls, value: list[str]) -> list[str]:
        normalized = normalize_languages(value)
        if not normalized:
            raise ValueError("List at least one language you know.")
        return normalized

    @field_validator("linkedin_url")
    @classmethod
    def _check_linkedin_url(cls, value: str | None) -> str | None:
        return _normalize_profile_url(value, allowed_hosts=("linkedin.com",))

    @field_validator("github_url")
    @classmethod
    def _check_github_url(cls, value: str | None) -> str | None:
        return _normalize_profile_url(value, allowed_hosts=("github.com",))

    @model_validator(mode="after")
    def _validate_by_candidate_type(self) -> "PublicApplicantProfile":
        if self.candidate_type == CandidateType.EXPERIENCED:
            missing = [
                label
                for label, value in (
                    ("years of experience", self.years_experience),
                    ("current job title", self.current_title),
                    ("current company", self.current_company),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    "Experienced candidates must provide: " + ", ".join(missing) + "."
                )
            if self.immediate_joiner:
                self.notice_period_days = 0
            elif self.notice_period_days is None:
                raise ValueError(
                    "Notice period is required for experienced candidates "
                    "unless they are an immediate joiner."
                )
            else:
                self.immediate_joiner = False
        else:
            self.years_experience = 0
            self.current_title = None
            self.current_company = None
            self.notice_period_days = None
            self.immediate_joiner = None
        return self


class CandidateCreateRequest(CandidateProfileFields):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    source: CandidateSource = CandidateSource.RECRUITER_ADDED

    @field_validator("phone")
    @classmethod
    def _canonical_phone(cls, value: str | None) -> str | None:
        return canonical_phone_or_none(value)


class CandidateUpdateRequest(CandidateProfileFields):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    is_active: bool | None = None

    @field_validator("phone")
    @classmethod
    def _canonical_phone(cls, value: str | None) -> str | None:
        return canonical_phone_or_none(value)


class CandidateDeleteRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class CandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    full_name: str
    phone: str | None
    location: str | None
    current_title: str | None
    current_company: str | None
    preferred_location: str | None
    years_experience: int | None
    candidate_type: CandidateType | None
    notice_period_days: int | None
    immediate_joiner: bool | None
    qualification: str | None
    linkedin_url: str | None
    github_url: str | None
    date_of_birth: date | None = None
    place_of_birth: str | None = None
    languages: list[str] = Field(default_factory=list)
    # Self-declared current education (recruitment-program applications).
    education_level: EducationLevel | None = None
    current_study_year: int | None = None
    specialization: str | None = None
    institution_id: uuid.UUID | None = None
    institution_name: str | None = None
    expected_completion_date: date | None = None
    email_verified_at: datetime | None = None
    source: CandidateSource
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReapplySearchResult(BaseModel):
    """One match in HR's reapply-permission lookup, with where the candidate
    stands under the reapply rule so the UI can tell "blocked" (offer the
    grant) from "can already apply" (nothing to do)."""

    id: uuid.UUID
    full_name: str
    email: str
    phone: str | None
    last_self_applied_at: datetime | None
    eligible_from: datetime | None
    can_self_apply_now: bool


class ReapplyPermissionGrantedResponse(BaseModel):
    candidate_id: uuid.UUID
    full_name: str
    email: str
    #: Previous self-service applications removed so the candidate can
    #: apply again from scratch.
    cleared_application_count: int


class ReapplyGrantResponse(BaseModel):
    """A single-use grant from the earlier "Allow Reapply" action — no longer
    issued, but any still open are honoured and shown in reapply status."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    granted_by_user_id: uuid.UUID | None
    granted_by_name: str | None = None
    reason: str
    created_at: datetime
    used_at: datetime | None
    used_by_application_id: uuid.UUID | None


class ReapplyStatusResponse(BaseModel):
    """Where the candidate stands under the self-service reapply rule, for
    HR reapply permission. `last_self_applied_at` None = they never
    self-applied (e.g. HR created them) and can apply any time."""

    candidate_id: uuid.UUID
    cooldown_months: int
    last_self_applied_at: datetime | None
    eligible_from: datetime | None
    can_self_apply_now: bool
    open_grant: ReapplyGrantResponse | None = None
