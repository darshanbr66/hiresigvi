import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.application import (
    ApplicationEducationStatus,
    ApplicationSource,
    ApplicationStatus,
)
from app.models.application_draft import EducationVerificationOutcome
from app.models.job import EmploymentType
from app.schemas.screening import ScreeningRunResponse


class ApplicationSortField(StrEnum):
    """What the Applications list can be ordered by. `created_at` is the
    long-standing default; `status` orders by workflow stage (the enum's
    declaration order), not alphabetically."""

    CREATED_AT = "created_at"
    APPLIED_AT = "applied_at"
    CANDIDATE_NAME = "candidate_name"
    JOB_TITLE = "job_title"
    STATUS = "status"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class ApplicationCreateRequest(BaseModel):
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    source: ApplicationSource = ApplicationSource.RECRUITER_ADDED


class CandidateJobMatchRequest(BaseModel):
    job_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=1000)


class AIScreeningOverrideRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class ApplicationStatusChangeRequest(BaseModel):
    to_status: ApplicationStatus
    reason: str | None = Field(default=None, max_length=1000)


class ApplicationDeleteRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class ApplicationResponse(BaseModel):
    """Includes denormalized `candidate_full_name`/`job_title` — populated
    by the service's join query — so a recruiter list view doesn't need a
    separate lookup per row for what is, in practice, always shown."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_full_name: str
    candidate_email: str
    candidate_phone: str | None = None
    job_id: uuid.UUID
    job_title: str
    #: The job's current employment type (None for a program's carrier job).
    job_employment_type: EmploymentType | None = None
    campus_drive_id: uuid.UUID | None = None
    status: ApplicationStatus
    source: ApplicationSource
    #: The candidate submitted it themselves (careers site / campus drive
    #: link) — as opposed to staff creating it.
    is_self_service: bool = False
    #: The academic-document check it was submitted with: VERIFIED,
    #: MANUAL_REVIEW (HR must review the document), or None (no check —
    #: HR-created, programs, or older applications).
    education_verification_status: ApplicationEducationStatus | None = None
    #: The job's qualification requirements were ambiguous at submission:
    #: HR must check eligibility against the JD.
    eligibility_review_required: bool = False
    applied_at: datetime
    created_at: datetime
    updated_at: datetime
    resume_id: uuid.UUID | None = None
    resume_filename: str | None = None
    deleted_at: datetime | None = None


class EducationDocumentEntry(BaseModel):
    """One education document on an application, for the recruiter's
    Education documents list. Metadata only — the file itself is streamed
    by `GET .../documents/{kind}`; no storage path or URL is ever exposed."""

    #: `academic-document` | `tenth-marks-card` (also the download path).
    kind: str
    label: str
    description: str
    required: bool
    #: The application has a record of this document.
    uploaded: bool
    #: Its file can be read from storage right now.
    available: bool
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    uploaded_at: datetime | None = None
    #: Academic document only: `AI_VERIFIED` | `MANUAL_VERIFICATION_REQUIRED`
    #: | `NOT_UPLOADED` (None for the 10th marks card, never checked).
    verification: str | None = None
    #: Why manual verification is required, in plain language (None
    #: otherwise). Never an internal error detail.
    verification_reason: str | None = None


class ApplicationDocumentsResponse(BaseModel):
    documents: list[EducationDocumentEntry]


class AcademicDocumentSummary(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime


class EducationCheckEntry(BaseModel):
    """One AI consistency check of the academic document — advisory only,
    never a statement that the document is genuine."""

    outcome: EducationVerificationOutcome
    reason_code: str
    confidence: float | None
    #: What the candidate was told.
    candidate_message: str
    #: What the document was checked against (labels; the specialization
    #: fields are None for checks run before they were recorded).
    selected_qualification: str | None = None
    selected_specialization_category: str | None = None
    selected_specialization: str | None = None
    #: The model's observations, for the reviewer (None when the AI call
    #: failed).
    document_type: str | None = None
    detected_level: str | None = None
    degree_text: str | None = None
    branch_text: str | None = None
    institution_text: str | None = None
    name_on_document: str | None = None
    notes: str | None = None
    provider: str | None
    model: str | None
    created_at: datetime


class ApplicationEducationResponse(BaseModel):
    education_verification_status: ApplicationEducationStatus | None
    eligibility_review_required: bool
    #: The candidate profile's qualification text ("M.Tech in Robotics and
    #: Automation") — shared across the candidate's applications.
    qualification: str | None
    #: This application's Education-step selections, as labels. None for
    #: applications submitted before they were recorded (fall back to
    #: `qualification`).
    highest_qualification: str | None = None
    specialization_category: str | None = None
    specialization: str | None = None
    #: The job's fields of study as recorded at submission, whether the JD
    #: made them mandatory, and the one the specialization matched (empty /
    #: None for applications submitted before this was recorded).
    job_fields_of_study: list[str] = []
    job_field_mandatory: bool = False
    matched_field_of_study: str | None = None
    #: Why HR is asked to check eligibility (plain language; empty when the
    #: application isn't flagged, or was flagged before reasons were kept).
    eligibility_review_reasons: list[str] = []
    institution: str | None
    qualification_year: int | None
    document: AcademicDocumentSummary | None
    #: Why the document needs manual verification (plain language, from the
    #: latest check's outcome; None when it was AI verified or isn't there).
    manual_review_reason: str | None = None
    checks: list[EducationCheckEntry]


class ApplicationStatusHistoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_status: ApplicationStatus | None
    to_status: ApplicationStatus
    changed_by_user_id: uuid.UUID | None
    reason: str | None
    created_at: datetime


class HrApplicationWithResumeResponse(BaseModel):
    """HR added a resume + applying role for a candidate. `screening` is the
    newest run (None when screening wasn't requested); the application's
    `status` already reflects the gate (APPLIED or AI_SCREENED_OUT)."""

    application: ApplicationResponse
    screening: ScreeningRunResponse | None = None
    attached_to_existing: bool = False
