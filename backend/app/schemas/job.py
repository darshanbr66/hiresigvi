import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.job import CompensationPeriod, EmploymentType, JobStatus, WorkMode


def _currency(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    code = value.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError("Use a three-letter ISO currency code, e.g. INR.")
    return code


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


class _EmploymentDetails(BaseModel):
    """The optional, type-specific details (app/models/job.py::
    EMPLOYMENT_TYPE_FIELDS). Shape and range checks live here; *which*
    fields a type may use is checked by job_service against the saved job,
    because a PATCH may change the type without resending every field."""

    work_mode: WorkMode | None = None
    duration_months: int | None = Field(default=None, ge=1, le=60)
    is_paid: bool | None = None
    compensation_min: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    compensation_max: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    compensation_currency: str | None = None
    compensation_period: CompensationPeriod | None = None
    hours_per_week: int | None = Field(default=None, ge=1, le=168)
    work_schedule: str | None = Field(default=None, max_length=2000)
    min_experience_years: int | None = Field(default=None, ge=0, le=60)
    max_experience_years: int | None = Field(default=None, ge=0, le=60)
    eligibility_criteria: str | None = Field(default=None, max_length=5000)
    conversion_to_full_time: bool | None = None
    expected_joining_date: date | None = None
    employment_terms: str | None = Field(default=None, max_length=5000)

    @field_validator("compensation_currency")
    @classmethod
    def _check_currency(cls, value: str | None) -> str | None:
        return _currency(value)

    @field_validator("work_schedule", "eligibility_criteria", "employment_terms")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _check_ranges(self) -> Self:
        if (
            self.compensation_min is not None
            and self.compensation_max is not None
            and self.compensation_min > self.compensation_max
        ):
            raise ValueError("The minimum pay can't be more than the maximum.")
        if (
            self.min_experience_years is not None
            and self.max_experience_years is not None
            and self.min_experience_years > self.max_experience_years
        ):
            raise ValueError("The minimum experience can't be more than the maximum.")
        return self


class JobCreateRequest(_EmploymentDetails):
    title: str = Field(min_length=1, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    #: Omitted = FULL_TIME — the same default every pre-existing job received
    #: when this field became an enum, and what the job form pre-selects.
    employment_type: EmploymentType = EmploymentType.FULL_TIME
    description: str = Field(min_length=1)
    description_visible: bool = True
    openings_count: int = Field(default=1, ge=1)


class JobUpdateRequest(_EmploymentDetails):
    """All fields optional — a PATCH applies only what the caller sends
    (`exclude_unset=True` at the service layer)."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    employment_type: EmploymentType | None = None
    description: str | None = Field(default=None, min_length=1)
    description_visible: bool | None = None
    status: JobStatus | None = None
    openings_count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _type_cannot_be_cleared(self) -> Self:
        if "employment_type" in self.model_fields_set and self.employment_type is None:
            raise ValueError("A job's employment type can be changed but not removed.")
        return self


class JobDeleteRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    title: str
    department: str | None
    location: str | None
    #: None only for a recruitment program's carrier job (`program_id` set).
    employment_type: EmploymentType | None
    work_mode: WorkMode | None = None
    duration_months: int | None = None
    is_paid: bool | None = None
    compensation_min: Decimal | None = None
    compensation_max: Decimal | None = None
    compensation_currency: str | None = None
    compensation_period: CompensationPeriod | None = None
    hours_per_week: int | None = None
    work_schedule: str | None = None
    min_experience_years: int | None = None
    max_experience_years: int | None = None
    eligibility_criteria: str | None = None
    conversion_to_full_time: bool | None = None
    expected_joining_date: date | None = None
    employment_terms: str | None = None
    description: str
    description_visible: bool
    status: JobStatus
    openings_count: int
    created_by: uuid.UUID
    #: The recruitment program this job carries applications for (its
    #: details are managed on the program, not here); None for ordinary jobs.
    program_id: uuid.UUID | None = None
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
