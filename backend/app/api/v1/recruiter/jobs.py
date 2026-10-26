"""Recruiter-facing Job CRUD. `organization_id` is always taken from the
authenticated caller, never from the request (docs/security.md § 2)."""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.job import EmploymentType, Job, JobStatus
from app.models.user import User
from app.schemas.job import JobCreateRequest, JobDeleteRequest, JobResponse, JobUpdateRequest
from app.services import job_service

router = APIRouter(prefix="/jobs", tags=["recruiter-jobs"])


async def _response(db: AsyncSession, job: Job) -> JobResponse:
    program_ids = await job_service.program_job_ids(db, job.organization_id)
    return JobResponse.model_validate(job).model_copy(update={"program_id": program_ids.get(job.id)})


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreateRequest,
    current_user: User = Depends(require_permission("job.create")),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    assert current_user.organization_id is not None
    job = await job_service.create_job(db, actor=current_user, payload=payload)
    return await _response(db, job)


@router.get("", response_model=list[JobResponse])
async def list_jobs(
    job_status: JobStatus | None = Query(default=None, alias="status"),
    employment_type: EmploymentType | None = Query(default=None),
    current_user: User = Depends(require_permission("job.read")),
    db: AsyncSession = Depends(get_db),
) -> list[JobResponse]:
    assert current_user.organization_id is not None
    jobs = await job_service.list_jobs(
        db, current_user.organization_id, status=job_status, employment_type=employment_type
    )
    program_ids = await job_service.program_job_ids(db, current_user.organization_id)
    return [
        JobResponse.model_validate(job).model_copy(update={"program_id": program_ids.get(job.id)})
        for job in jobs
    ]


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    _: User = Depends(require_permission("job.read")),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    job = await job_service.get_job(db, job_id)
    if job is None:
        raise NotFoundError("Job not found.")
    return await _response(db, job)


@router.patch("/{job_id}", response_model=JobResponse)
async def update_job(
    job_id: uuid.UUID,
    payload: JobUpdateRequest,
    current_user: User = Depends(require_permission("job.update")),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    job = await job_service.get_job(db, job_id)
    if job is None or job.deleted_at is not None:
        raise NotFoundError("Job not found.")
    updated = await job_service.update_job(db, job, payload, actor=current_user)
    return await _response(db, updated)


@router.post("/{job_id}/delete", response_model=JobResponse)
async def delete_job(
    job_id: uuid.UUID,
    payload: JobDeleteRequest,
    current_user: User = Depends(require_permission("job.delete")),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    job = await job_service.get_job(db, job_id)
    if job is None:
        raise NotFoundError("Job not found.")
    deleted = await job_service.delete_job(db, job, actor=current_user, reason=payload.reason)
    return await _response(db, deleted)
