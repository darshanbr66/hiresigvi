"""Recruiter-facing Campus Drive management (docs/campus-hiring.md)."""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.campus_drive import CampusDrive
from app.models.institution import AffiliationStatus
from app.models.user import User
from app.schemas.campus_drive import (
    AffiliationReviewRequest,
    AffiliationVerificationResponse,
    CampusDriveCreateRequest,
    CampusDriveDeleteRequest,
    CampusDriveFunnelCounts,
    CampusDriveReportRow,
    CampusDriveResponse,
    CampusDriveUpdateRequest,
    RosterEntryResponse,
    RosterUploadRequest,
    RosterUploadResponse,
)
from app.services import campus_affiliation_service, campus_drive_service

router = APIRouter(prefix="/campus-drives", tags=["recruiter-campus-drives"])


def _application_link(raw_token: str) -> str:
    # The candidate-facing app's own origin — the link a recruiter
    # copies/shares points at the frontend route that resolves the token,
    # not the API itself (settings.frontend_base_url, env-configurable).
    return f"{get_settings().frontend_base_url}/campus-drive/{raw_token}"


def _to_response(
    drive: CampusDrive, application_count: int, *, link: str | None = None
) -> CampusDriveResponse:
    return CampusDriveResponse(
        id=drive.id,
        name=drive.name,
        job_id=drive.job_id,
        job_title=drive.job.title,
        institution_id=drive.institution_id,
        institution_name=drive.institution.name,
        college_name=drive.college_name,
        affiliation_verification_method=drive.affiliation_verification_method,
        description=drive.description,
        batch_year=drive.batch_year,
        start_date=drive.start_date,
        end_date=drive.end_date,
        registration_deadline=drive.registration_deadline,
        default_assessment_id=drive.default_assessment_id,
        default_assessment_title=drive.default_assessment.title if drive.default_assessment else None,
        status=drive.status,
        application_count=application_count,
        deleted_at=drive.deleted_at,
        created_at=drive.created_at,
        application_link=link,
    )


@router.post("", response_model=CampusDriveResponse, status_code=status.HTTP_201_CREATED)
async def create_campus_drive(
    payload: CampusDriveCreateRequest,
    current_user: User = Depends(require_permission("campus_drive.manage")),
    db: AsyncSession = Depends(get_db),
) -> CampusDriveResponse:
    assert current_user.organization_id is not None
    drive, raw_token = await campus_drive_service.create_campus_drive(
        db, actor=current_user, payload=payload
    )
    return _to_response(drive, 0, link=_application_link(raw_token))


@router.get("", response_model=list[CampusDriveResponse])
async def list_campus_drives(
    institution_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(require_permission("campus_drive.read")),
    db: AsyncSession = Depends(get_db),
) -> list[CampusDriveResponse]:
    assert current_user.organization_id is not None
    drives = await campus_drive_service.list_campus_drives(
        db, current_user.organization_id, institution_id=institution_id
    )
    counts = await campus_drive_service.application_counts(db, current_user.organization_id)
    return [_to_response(drive, counts.get(drive.id, 0)) for drive in drives]


@router.get("/report", response_model=list[CampusDriveReportRow])
async def campus_report(
    institution_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(require_permission("campus_drive.read")),
    db: AsyncSession = Depends(get_db),
) -> list[CampusDriveReportRow]:
    """Per-drive campus report (institution, applications, verification,
    assessment, interview and outcomes), optionally for one institution."""
    assert current_user.organization_id is not None
    return await campus_drive_service.campus_report(
        db, current_user.organization_id, institution_id=institution_id
    )


async def _live_drive(db: AsyncSession, drive_id: uuid.UUID) -> CampusDrive:
    drive = await campus_drive_service.get_campus_drive(db, drive_id)
    if drive is None or drive.deleted_at is not None:
        raise NotFoundError("Campus drive not found.")
    return drive


@router.get("/{drive_id}/verifications", response_model=list[AffiliationVerificationResponse])
async def list_affiliation_verifications(
    drive_id: uuid.UUID,
    verification_status: AffiliationStatus | None = Query(default=None, alias="status"),
    _: User = Depends(require_permission("campus_drive.read")),
    db: AsyncSession = Depends(get_db),
) -> list[AffiliationVerificationResponse]:
    drive = await campus_drive_service.get_campus_drive(db, drive_id)
    if drive is None:
        raise NotFoundError("Campus drive not found.")
    rows = await campus_affiliation_service.list_for_drive(db, drive, status=verification_status)
    return [
        AffiliationVerificationResponse(
            id=row.verification.id,
            application_id=row.verification.application_id,
            candidate_full_name=row.candidate_name,
            candidate_email=row.candidate_email,
            status=row.verification.status,
            method=row.verification.method,
            reviewed_by_name=row.reviewer_name,
            reviewed_at=row.verification.reviewed_at,
            review_note=row.verification.review_note,
            created_at=row.verification.created_at,
        )
        for row in rows
    ]


@router.post(
    "/{drive_id}/verifications/{verification_id}/review",
    response_model=AffiliationVerificationResponse,
)
async def review_affiliation(
    drive_id: uuid.UUID,
    verification_id: uuid.UUID,
    payload: AffiliationReviewRequest,
    current_user: User = Depends(require_permission("campus_drive.manage")),
    db: AsyncSession = Depends(get_db),
) -> AffiliationVerificationResponse:
    drive = await _live_drive(db, drive_id)
    record = await campus_affiliation_service.review(
        db,
        drive=drive,
        verification_id=verification_id,
        status=payload.status,
        note=payload.note,
        actor=current_user,
    )
    rows = await campus_affiliation_service.list_for_drive(db, drive)
    row = next(r for r in rows if r.verification.id == record.id)
    return AffiliationVerificationResponse(
        id=record.id,
        application_id=record.application_id,
        candidate_full_name=row.candidate_name,
        candidate_email=row.candidate_email,
        status=record.status,
        method=record.method,
        reviewed_by_name=row.reviewer_name,
        reviewed_at=record.reviewed_at,
        review_note=record.review_note,
        created_at=record.created_at,
    )


@router.get("/{drive_id}/roster", response_model=list[RosterEntryResponse])
async def list_roster(
    drive_id: uuid.UUID,
    _: User = Depends(require_permission("campus_drive.read")),
    db: AsyncSession = Depends(get_db),
) -> list[RosterEntryResponse]:
    drive = await campus_drive_service.get_campus_drive(db, drive_id)
    if drive is None:
        raise NotFoundError("Campus drive not found.")
    return [
        RosterEntryResponse.model_validate(e)
        for e in await campus_affiliation_service.list_roster(db, drive)
    ]


@router.post("/{drive_id}/roster", response_model=RosterUploadResponse)
async def add_roster_entries(
    drive_id: uuid.UUID,
    payload: RosterUploadRequest,
    current_user: User = Depends(require_permission("campus_drive.manage")),
    db: AsyncSession = Depends(get_db),
) -> RosterUploadResponse:
    """Adds the college-provided student list used by STUDENT_LIST
    verification (email, optional roll number and name — nothing more)."""
    drive = await _live_drive(db, drive_id)
    added = await campus_affiliation_service.add_roster_entries(
        db,
        drive=drive,
        entries=[
            campus_affiliation_service.RosterEntryInput(
                email=str(e.email), student_identifier=e.student_identifier, full_name=e.full_name
            )
            for e in payload.entries
        ],
        actor=current_user,
    )
    total = len(await campus_affiliation_service.list_roster(db, drive))
    return RosterUploadResponse(added=added, total=total)


@router.delete("/{drive_id}/roster/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_roster_entry(
    drive_id: uuid.UUID,
    entry_id: uuid.UUID,
    current_user: User = Depends(require_permission("campus_drive.manage")),
    db: AsyncSession = Depends(get_db),
) -> None:
    drive = await _live_drive(db, drive_id)
    await campus_affiliation_service.remove_roster_entry(
        db, drive=drive, entry_id=entry_id, actor=current_user
    )


@router.get("/{drive_id}", response_model=CampusDriveResponse)
async def get_campus_drive(
    drive_id: uuid.UUID,
    current_user: User = Depends(require_permission("campus_drive.read")),
    db: AsyncSession = Depends(get_db),
) -> CampusDriveResponse:
    assert current_user.organization_id is not None
    drive = await campus_drive_service.get_campus_drive(db, drive_id)
    if drive is None:
        raise NotFoundError("Campus drive not found.")
    counts = await campus_drive_service.application_counts(db, current_user.organization_id)
    return _to_response(drive, counts.get(drive.id, 0))


@router.get("/{drive_id}/funnel", response_model=CampusDriveFunnelCounts)
async def get_campus_drive_funnel(
    drive_id: uuid.UUID,
    _: User = Depends(require_permission("campus_drive.read")),
    db: AsyncSession = Depends(get_db),
) -> CampusDriveFunnelCounts:
    drive = await campus_drive_service.get_campus_drive(db, drive_id)
    if drive is None:
        raise NotFoundError("Campus drive not found.")
    return await campus_drive_service.get_funnel_counts(db, drive_id)


@router.patch("/{drive_id}", response_model=CampusDriveResponse)
async def update_campus_drive(
    drive_id: uuid.UUID,
    payload: CampusDriveUpdateRequest,
    current_user: User = Depends(require_permission("campus_drive.manage")),
    db: AsyncSession = Depends(get_db),
) -> CampusDriveResponse:
    assert current_user.organization_id is not None
    drive = await campus_drive_service.get_campus_drive(db, drive_id)
    if drive is None or drive.deleted_at is not None:
        raise NotFoundError("Campus drive not found.")
    updated = await campus_drive_service.update_campus_drive(db, drive, payload, actor=current_user)
    counts = await campus_drive_service.application_counts(db, current_user.organization_id)
    return _to_response(updated, counts.get(updated.id, 0))


@router.post("/{drive_id}/regenerate-link", response_model=CampusDriveResponse)
async def regenerate_campus_drive_link(
    drive_id: uuid.UUID,
    current_user: User = Depends(require_permission("campus_drive.manage")),
    db: AsyncSession = Depends(get_db),
) -> CampusDriveResponse:
    assert current_user.organization_id is not None
    drive = await campus_drive_service.get_campus_drive(db, drive_id)
    if drive is None or drive.deleted_at is not None:
        raise NotFoundError("Campus drive not found.")
    raw_token = await campus_drive_service.regenerate_link(db, drive, actor=current_user)
    counts = await campus_drive_service.application_counts(db, current_user.organization_id)
    return _to_response(drive, counts.get(drive.id, 0), link=_application_link(raw_token))


@router.post("/{drive_id}/delete", response_model=CampusDriveResponse)
async def delete_campus_drive(
    drive_id: uuid.UUID,
    payload: CampusDriveDeleteRequest,
    current_user: User = Depends(require_permission("campus_drive.delete")),
    db: AsyncSession = Depends(get_db),
) -> CampusDriveResponse:
    assert current_user.organization_id is not None
    drive = await campus_drive_service.get_campus_drive(db, drive_id)
    if drive is None:
        raise NotFoundError("Campus drive not found.")
    deleted = await campus_drive_service.delete_campus_drive(
        db, drive, actor=current_user, reason=payload.reason
    )
    counts = await campus_drive_service.application_counts(db, current_user.organization_id)
    return _to_response(deleted, counts.get(deleted.id, 0))
