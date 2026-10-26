"""Recruiter-facing institution registry (docs/campus-hiring.md § 1a).
Institutions are part of campus hiring, so they use the campus-drive
permissions: `campus_drive.read` to view, `campus_drive.manage` to change."""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.institution import Institution
from app.models.user import User
from app.schemas.institution import (
    InstitutionCreateRequest,
    InstitutionResponse,
    InstitutionUpdateRequest,
)
from app.services import institution_service

router = APIRouter(prefix="/institutions", tags=["recruiter-institutions"])


def _to_response(institution: Institution, drive_count: int) -> InstitutionResponse:
    response = InstitutionResponse.model_validate(institution)
    response.campus_drive_count = drive_count
    return response


@router.get("", response_model=list[InstitutionResponse])
async def list_institutions(
    q: str | None = Query(default=None, max_length=100),
    active_only: bool = Query(default=False),
    current_user: User = Depends(require_permission("campus_drive.read")),
    db: AsyncSession = Depends(get_db),
) -> list[InstitutionResponse]:
    assert current_user.organization_id is not None
    institutions = await institution_service.list_institutions(
        db, current_user.organization_id, search=q, active_only=active_only
    )
    counts = await institution_service.drive_counts(db, current_user.organization_id)
    return [_to_response(i, counts.get(i.id, 0)) for i in institutions]


@router.post("", response_model=InstitutionResponse, status_code=status.HTTP_201_CREATED)
async def create_institution(
    payload: InstitutionCreateRequest,
    current_user: User = Depends(require_permission("campus_drive.manage")),
    db: AsyncSession = Depends(get_db),
) -> InstitutionResponse:
    institution = await institution_service.create_institution(db, actor=current_user, payload=payload)
    return _to_response(institution, 0)


@router.get("/{institution_id}", response_model=InstitutionResponse)
async def get_institution(
    institution_id: uuid.UUID,
    current_user: User = Depends(require_permission("campus_drive.read")),
    db: AsyncSession = Depends(get_db),
) -> InstitutionResponse:
    assert current_user.organization_id is not None
    institution = await institution_service.get_institution(
        db, current_user.organization_id, institution_id
    )
    if institution is None:
        raise NotFoundError("Institution not found.")
    counts = await institution_service.drive_counts(db, current_user.organization_id)
    return _to_response(institution, counts.get(institution.id, 0))


@router.patch("/{institution_id}", response_model=InstitutionResponse)
async def update_institution(
    institution_id: uuid.UUID,
    payload: InstitutionUpdateRequest,
    current_user: User = Depends(require_permission("campus_drive.manage")),
    db: AsyncSession = Depends(get_db),
) -> InstitutionResponse:
    assert current_user.organization_id is not None
    institution = await institution_service.get_institution(
        db, current_user.organization_id, institution_id
    )
    if institution is None:
        raise NotFoundError("Institution not found.")
    updated = await institution_service.update_institution(
        db, institution, payload, actor=current_user
    )
    counts = await institution_service.drive_counts(db, current_user.organization_id)
    return _to_response(updated, counts.get(updated.id, 0))
