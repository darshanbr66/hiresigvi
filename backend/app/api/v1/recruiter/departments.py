"""Department management for the Team Hierarchy feature (ZPHIREX platform
overhaul § 9-10). `organization_id` always comes from the authenticated
caller, never the request."""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.team_hierarchy import Department
from app.models.user import User
from app.schemas.team_hierarchy import (
    DepartmentCreateRequest,
    DepartmentDeleteRequest,
    DepartmentResponse,
    DepartmentUpdateRequest,
)
from app.services import department_service

router = APIRouter(prefix="/departments", tags=["recruiter-departments"])


async def _to_response(db: AsyncSession, department: Department) -> DepartmentResponse:
    response = DepartmentResponse.model_validate(department)
    response.employee_count = await department_service.count_active_employees(db, department.id)
    return response


@router.post("", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
async def create_department(
    payload: DepartmentCreateRequest,
    current_user: User = Depends(require_permission("department.manage")),
    db: AsyncSession = Depends(get_db),
) -> DepartmentResponse:
    assert current_user.organization_id is not None
    department = await department_service.create_department(
        db, organization_id=current_user.organization_id, actor=current_user, payload=payload
    )
    return await _to_response(db, department)


@router.get("", response_model=list[DepartmentResponse])
async def list_departments(
    current_user: User = Depends(require_permission("department.read")),
    db: AsyncSession = Depends(get_db),
) -> list[DepartmentResponse]:
    assert current_user.organization_id is not None
    departments = await department_service.list_departments(db, current_user.organization_id)
    return [await _to_response(db, department) for department in departments]


@router.get("/{department_id}", response_model=DepartmentResponse)
async def get_department(
    department_id: uuid.UUID,
    _: User = Depends(require_permission("department.read")),
    db: AsyncSession = Depends(get_db),
) -> DepartmentResponse:
    department = await department_service.get_department(db, department_id)
    if department is None:
        raise NotFoundError("Department not found.")
    return await _to_response(db, department)


@router.patch("/{department_id}", response_model=DepartmentResponse)
async def update_department(
    department_id: uuid.UUID,
    payload: DepartmentUpdateRequest,
    current_user: User = Depends(require_permission("department.manage")),
    db: AsyncSession = Depends(get_db),
) -> DepartmentResponse:
    department = await department_service.get_department(db, department_id)
    if department is None or department.deleted_at is not None:
        raise NotFoundError("Department not found.")
    updated = await department_service.update_department(
        db, department, payload, actor=current_user
    )
    return await _to_response(db, updated)


@router.post("/{department_id}/delete", response_model=DepartmentResponse)
async def delete_department(
    department_id: uuid.UUID,
    payload: DepartmentDeleteRequest,
    current_user: User = Depends(require_permission("department.manage")),
    db: AsyncSession = Depends(get_db),
) -> DepartmentResponse:
    department = await department_service.get_department(db, department_id)
    if department is None:
        raise NotFoundError("Department not found.")
    deleted = await department_service.delete_department(
        db, department, actor=current_user, reason=payload.reason
    )
    return await _to_response(db, deleted)
