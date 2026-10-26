import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.application import ApplicationSource, ApplicationStatus
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
    applied_at: datetime
    created_at: datetime
    updated_at: datetime
    resume_id: uuid.UUID | None = None
    resume_filename: str | None = None
    deleted_at: datetime | None = None


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
