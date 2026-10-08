"""Employee directory management for the Team Hierarchy feature (SIGVITAS
platform overhaul § 9-11). `organization_id` always comes from the
authenticated caller, never the request. Deliberately separate from
`app/api/v1/recruiter/users.py` (portal login + RBAC) — most employees
here have no portal account at all."""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import granted_permission_codes, require_permission
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.team_hierarchy import Employee
from app.models.user import User
from app.schemas.team_hierarchy import (
    EmployeeCreateRequest,
    EmployeeDeactivateRequest,
    EmployeeDeleteRequest,
    EmployeeMoveRequest,
    EmployeeReorderRequest,
    EmployeeResponse,
    EmployeeUpdateRequest,
)
from app.services import employee_service, user_service

router = APIRouter(prefix="/employees", tags=["recruiter-employees"])


async def _to_responses(db: AsyncSession, employees: list[Employee]) -> list[EmployeeResponse]:
    department_ids = {e.department_id for e in employees if e.department_id is not None}
    manager_ids = {e.manager_id for e in employees if e.manager_id is not None}
    department_names = await employee_service.get_department_names_for(db, department_ids)
    manager_names = await employee_service.get_employee_names_for(db, manager_ids)

    responses = []
    for employee in employees:
        response = EmployeeResponse.model_validate(employee)
        response.department_name = (
            department_names.get(employee.department_id) if employee.department_id else None
        )
        response.manager_name = manager_names.get(employee.manager_id) if employee.manager_id else None
        responses.append(response)
    return responses


@router.post("", response_model=EmployeeResponse, status_code=status.HTTP_201_CREATED)
async def create_employee(
    payload: EmployeeCreateRequest,
    current_user: User = Depends(require_permission("employee.manage")),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    assert current_user.organization_id is not None
    employee = await employee_service.create_employee(
        db, organization_id=current_user.organization_id, actor=current_user, payload=payload
    )
    return (await _to_responses(db, [employee]))[0]


@router.get("", response_model=list[EmployeeResponse])
async def list_employees(
    department_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(require_permission("employee.read")),
    db: AsyncSession = Depends(get_db),
) -> list[EmployeeResponse]:
    assert current_user.organization_id is not None
    employees = await employee_service.list_employees(
        db, current_user.organization_id, department_id=department_id
    )
    return await _to_responses(db, employees)


# Declared before the `/{employee_id}` routes so "reorder" is never parsed as
# an employee id.
@router.patch("/reorder", response_model=list[EmployeeResponse])
async def reorder_employees(
    payload: EmployeeReorderRequest,
    current_user: User = Depends(require_permission("employee.manage")),
    db: AsyncSession = Depends(get_db),
) -> list[EmployeeResponse]:
    assert current_user.organization_id is not None
    reordered = await employee_service.reorder_employees(
        db,
        organization_id=current_user.organization_id,
        actor=current_user,
        employee_ids=payload.employee_ids,
    )
    return await _to_responses(db, reordered)


@router.get("/{employee_id}", response_model=EmployeeResponse)
async def get_employee(
    employee_id: uuid.UUID,
    _: User = Depends(require_permission("employee.read")),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.get_employee(db, employee_id)
    if employee is None:
        raise NotFoundError("Employee not found.")
    return (await _to_responses(db, [employee]))[0]


@router.patch("/{employee_id}", response_model=EmployeeResponse)
async def update_employee(
    employee_id: uuid.UUID,
    payload: EmployeeUpdateRequest,
    current_user: User = Depends(require_permission("employee.manage")),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.get_employee(db, employee_id)
    if employee is None or employee.deleted_at is not None:
        raise NotFoundError("Employee not found.")
    updated = await employee_service.update_employee(db, employee, payload, actor=current_user)
    return (await _to_responses(db, [updated]))[0]


@router.post("/{employee_id}/move", response_model=EmployeeResponse)
async def move_employee(
    employee_id: uuid.UUID,
    payload: EmployeeMoveRequest,
    current_user: User = Depends(require_permission("employee.manage")),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.get_employee(db, employee_id)
    if employee is None or employee.deleted_at is not None:
        raise NotFoundError("Employee not found.")
    moved = await employee_service.move_employee(
        db, employee, new_department_id=payload.department_id, actor=current_user
    )
    return (await _to_responses(db, [moved]))[0]


@router.post("/{employee_id}/deactivate", response_model=EmployeeResponse)
async def deactivate_employee(
    employee_id: uuid.UUID,
    payload: EmployeeDeactivateRequest,
    current_user: User = Depends(require_permission("employee.manage")),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.get_employee(db, employee_id)
    if employee is None or employee.deleted_at is not None:
        raise NotFoundError("Employee not found.")
    deactivated = await employee_service.deactivate_employee(
        db, employee, actor=current_user, reason=payload.reason
    )
    return (await _to_responses(db, [deactivated]))[0]


@router.post("/{employee_id}/reactivate", response_model=EmployeeResponse)
async def reactivate_employee(
    employee_id: uuid.UUID,
    current_user: User = Depends(require_permission("employee.manage")),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.get_employee(db, employee_id)
    if employee is None or employee.deleted_at is not None:
        raise NotFoundError("Employee not found.")
    reactivated = await employee_service.reactivate_employee(db, employee, actor=current_user)
    return (await _to_responses(db, [reactivated]))[0]


@router.post("/{employee_id}/delete", response_model=EmployeeResponse)
async def delete_employee(
    employee_id: uuid.UUID,
    payload: EmployeeDeleteRequest,
    current_user: User = Depends(require_permission("employee.manage")),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    """Remove an employee who left (soft delete — see
    employee_service.delete_employee). Another organization's employee is
    invisible under RLS, so it is a 404 like a missing one."""
    employee = await employee_service.get_employee(db, employee_id)
    if employee is None or employee.deleted_at is not None:
        raise NotFoundError("Employee not found.")
    can_manage_admins = user_service.MANAGE_ADMINS_PERMISSION in await granted_permission_codes(
        db, current_user
    )
    deleted = await employee_service.delete_employee(
        db,
        employee,
        actor=current_user,
        reason=payload.reason,
        can_manage_admins=can_manage_admins,
    )
    return (await _to_responses(db, [deleted]))[0]
