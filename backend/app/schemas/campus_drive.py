import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models.campus_drive import CampusDriveStatus
from app.models.institution import AffiliationStatus, AffiliationVerificationMethod
from app.schemas.institution import InstitutionCreateRequest


class CampusDriveCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Exactly one of these identifies the drive's institution: a registered
    # one (`institution_id`), one registered inline (`new_institution`), or
    # — the original request shape, still accepted — a `college_name`,
    # which is resolved against the registry (case/spacing-insensitive) and
    # registered if new (docs/campus-hiring.md § 1a).
    institution_id: uuid.UUID | None = None
    new_institution: InstitutionCreateRequest | None = None
    college_name: str | None = Field(default=None, min_length=1, max_length=255)
    affiliation_verification_method: AffiliationVerificationMethod = (
        AffiliationVerificationMethod.NONE
    )
    description: str | None = Field(default=None, max_length=5000)
    batch_year: int | None = Field(default=None, ge=1980, le=2100)
    start_date: date | None = None
    end_date: date | None = None
    registration_deadline: date | None = None
    default_assessment_id: uuid.UUID | None = None

    # Exactly one of these two identifies the job this drive hires for —
    # an existing job, or a brand-new one entered inline (see
    # docs/campus-hiring.md's redesigned "Can't find the job? + Add New
    # Job" flow).
    job_id: uuid.UUID | None = None
    new_job_title: str | None = Field(default=None, min_length=1, max_length=255)
    new_job_description: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _exactly_one_institution_source(self) -> "CampusDriveCreateRequest":
        provided = [
            self.institution_id is not None,
            self.new_institution is not None,
            bool(self.college_name and self.college_name.strip()),
        ]
        if sum(provided) != 1:
            raise ValueError(
                "Provide exactly one of institution_id, new_institution or college_name."
            )
        return self

    @model_validator(mode="after")
    def _exactly_one_job_source(self) -> "CampusDriveCreateRequest":
        if bool(self.job_id) == bool(self.new_job_title):
            raise ValueError(
                "Provide either an existing job_id or a new_job_title, not both or neither."
            )
        if self.new_job_title and not self.new_job_description:
            raise ValueError("new_job_description is required when creating a new job.")
        return self


class CampusDriveDeleteRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class CampusDriveUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    # Re-pointing a drive at a different registered institution — only
    # while it has no applications (campus_drive_service). `college_name`
    # is the display snapshot and follows the institution; it can no
    # longer be edited on its own.
    institution_id: uuid.UUID | None = None
    affiliation_verification_method: AffiliationVerificationMethod | None = None
    description: str | None = Field(default=None, max_length=5000)
    batch_year: int | None = Field(default=None, ge=1980, le=2100)
    start_date: date | None = None
    end_date: date | None = None
    registration_deadline: date | None = None
    default_assessment_id: uuid.UUID | None = None
    status: CampusDriveStatus | None = None


class CampusDriveResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    job_id: uuid.UUID
    job_title: str
    institution_id: uuid.UUID
    #: The institution's name (same value as `college_name`, kept for
    #: existing clients).
    institution_name: str
    college_name: str
    affiliation_verification_method: AffiliationVerificationMethod
    description: str | None
    batch_year: int | None
    start_date: date | None
    end_date: date | None
    registration_deadline: date | None
    default_assessment_id: uuid.UUID | None
    default_assessment_title: str | None
    status: CampusDriveStatus
    application_count: int
    deleted_at: datetime | None = None
    created_at: datetime
    # Only populated by the create/regenerate-link endpoints — the raw
    # token is shown once, like an assessment invitation link.
    application_link: str | None = None


class CampusDriveFunnelCounts(BaseModel):
    registered: int
    screening: int
    assessment_invited: int
    assessment_completed: int
    assessment_passed: int
    assessment_failed: int
    shortlisted: int
    interview: int
    selected: int
    rejected: int
    hired: int
    # Affiliation verification (0 when the drive doesn't require it).
    verification_pending: int = 0
    verified: int = 0
    verification_rejected: int = 0


class CampusDriveReportRow(BaseModel):
    """One drive's line in the cross-drive campus report — every number a
    live count over that drive's (non-deleted) applications."""

    drive_id: uuid.UUID
    drive_name: str
    drive_status: CampusDriveStatus
    institution_id: uuid.UUID
    institution_name: str
    job_title: str
    affiliation_verification_method: AffiliationVerificationMethod
    applications: int
    verification_pending: int
    verified: int
    verification_rejected: int
    assessment_invited: int
    assessment_completed: int
    assessment_passed: int
    assessment_failed: int
    assessment_pending_review: int
    interview: int
    selected: int
    hired: int
    rejected: int


class AffiliationVerificationResponse(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    candidate_full_name: str
    candidate_email: str
    status: AffiliationStatus
    method: AffiliationVerificationMethod | None
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime


class AffiliationReviewRequest(BaseModel):
    status: AffiliationStatus
    note: str | None = Field(default=None, max_length=2000)


class RosterEntryCreate(BaseModel):
    email: EmailStr
    student_identifier: str | None = Field(default=None, max_length=100)
    full_name: str | None = Field(default=None, max_length=255)


class RosterUploadRequest(BaseModel):
    entries: list[RosterEntryCreate] = Field(min_length=1, max_length=5000)


class RosterUploadResponse(BaseModel):
    added: int
    total: int


class RosterEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    student_identifier: str | None
    full_name: str | None
    created_at: datetime
