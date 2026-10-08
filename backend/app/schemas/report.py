import uuid

from pydantic import BaseModel


class StatusCount(BaseModel):
    status: str
    count: int


class JobApplicationCount(BaseModel):
    job_id: uuid.UUID
    job_title: str
    count: int


class ScreeningSummary(BaseModel):
    total_runs: int
    completed: int
    failed: int
    #: Mean score of the COMPLETED runs that carry one; 0 when there are none.
    average_score: float


class AssessmentSummaryStats(BaseModel):
    total_invitations: int
    submitted: int
    passed: int


class CampusDriveCount(BaseModel):
    drive_id: uuid.UUID
    drive_name: str
    application_count: int


class ReportOverview(BaseModel):
    """Every field here is computed from persisted data at request time
    (CLAUDE.md § 2: "Reports != hardcoded numbers"), tenant-scoped like
    every other recruiter-facing query."""

    total_jobs: int
    open_jobs: int
    total_candidates: int
    total_applications: int
    # Candidate-outcome metrics for the Overview dashboard (SIGVITAS
    # platform overhaul § 15) — each a direct count of
    # `ApplicationStatus.{SELECTED,REJECTED,HIRED}`, tenant-scoped and
    # excluding soft-deleted applications like every other field here.
    selected_candidates: int
    rejected_candidates: int
    hired_candidates: int
    applications_by_status: list[StatusCount]
    applications_by_job: list[JobApplicationCount]
    screening: ScreeningSummary
    assessments: AssessmentSummaryStats
    campus_drives: list[CampusDriveCount]
