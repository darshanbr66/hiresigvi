import uuid
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models.candidate import CandidateType


class PublicOrganizationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    slug: str
    # The recruitment team's published contact address (None = not
    # published). Candidate-facing copy reads it from here, never from a
    # hardcoded constant.
    careers_contact_email: str | None = None


class PublicJobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    department: str | None
    location: str | None
    employment_type: str | None
    openings_count: int
    created_at: datetime


class PublicJobDetail(PublicJobSummary):
    # None when the recruiter has hidden the JD from the public listing —
    # the text itself is never deleted (app/models/job.py::description_visible),
    # this is a presentation omission enforced here, not just in the UI.
    description: str | None
    organization: PublicOrganizationSummary


class PublicApplicationOutcome(StrEnum):
    """The submission-time screening gate's result, used server-side only
    (welcome-email variant, campus-drive fast-track). Never serialized to
    the candidate: the apply response is identical whatever the AI decided,
    and any decision reaches the candidate by email, not the portal."""

    #: In the pipeline for recruiter review (AI matched, or AI unavailable).
    RECEIVED = "RECEIVED"
    #: The submission-time screening found the profile doesn't meet this
    #: role's requirements; the profile is retained for other roles.
    NOT_SHORTLISTED_FOR_ROLE = "NOT_SHORTLISTED_FOR_ROLE"


class PublicApplicationResult(BaseModel):
    """The candidate's submission confirmation. Deliberately carries no
    screening outcome, status, score or reasoning — a MATCH and a NOT_MATCH
    produce the same shape, so the portal cannot reveal the decision."""

    id: uuid.UUID
    job_title: str
    candidate_email: str
    #: True only when the email provider actually accepted the confirmation
    #: email — the UI must never claim an email that wasn't sent.
    confirmation_email_sent: bool
    careers_contact_email: str | None
    submitted_at: datetime


class EmailVerificationRequest(BaseModel):
    email: EmailStr
    #: The careers-site job being applied for, so a candidate who already
    #: applied to it gets no code. A campus drive link supplies its own job
    #: and ignores this.
    job_id: uuid.UUID | None = None


class ApplicantStatusQuery(BaseModel):
    """The early "have you already applied?" check of the apply form, run as
    soon as an email or mobile number is typed — before any one-time code."""

    email: EmailStr | None = None
    phone: str | None = Field(default=None, min_length=1, max_length=32)
    #: As for `EmailVerificationRequest.job_id`.
    job_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _email_or_phone(self) -> "ApplicantStatusQuery":
        if self.email is None and self.phone is None:
            raise ValueError("Provide an email address or a mobile number.")
        return self


class ApplicantStatus(BaseModel):
    """Deliberately nothing but the yes/no: the caller hasn't proven who they
    are yet, so not a name, date, job or reason comes back."""

    already_applied: bool


class EmailVerificationRequested(BaseModel):
    expires_in_seconds: int
    resend_available_in_seconds: int


class EmailVerificationConfirm(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)
    #: The careers-site job being applied for, so a returning candidate who
    #: already applied to it is stopped here rather than at submission. A
    #: campus drive link supplies its own job and ignores this.
    job_id: uuid.UUID | None = None


class ApplicantProfilePrefill(BaseModel):
    """A returning candidate's stored profile, for prefilling the apply
    form. Only ever returned in `EmailVerificationConfirmed` — i.e. to a
    caller who has just proven they own the candidate's email address with a
    one-time code — never from any lookup an anonymous caller can make.
    Every field may be null: an HR-created profile can be incomplete."""

    model_config = ConfigDict(from_attributes=True)

    full_name: str | None = None
    phone: str | None = None
    date_of_birth: date | None = None
    place_of_birth: str | None = None
    languages: list[str] = Field(default_factory=list)
    candidate_type: CandidateType | None = None
    years_experience: int | None = None
    notice_period_days: int | None = None
    immediate_joiner: bool | None = None
    current_title: str | None = None
    current_company: str | None = None
    #: The candidate's current location (the form's `current_location`).
    location: str | None = None
    preferred_location: str | None = None
    qualification: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    tenth_marks_percentage: float | None = None


class EmailVerificationConfirmed(BaseModel):
    verification_token: str
    expires_at: datetime
    #: Set only when the verified email belongs to an existing candidate of
    #: this organization who may apply; null for a new applicant.
    existing_profile: ApplicantProfilePrefill | None = None


class ApplicantPrecheck(BaseModel):
    """The identity checks of a submission, run before the resume step so
    a conflict is reported without the candidate uploading anything."""

    email: EmailStr
    email_verification_token: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=1, max_length=32)
