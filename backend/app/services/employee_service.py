import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.models.team_hierarchy import Department, Employee, EmploymentStatus
from app.models.user import User
from app.schemas.team_hierarchy import EmployeeCreateRequest, EmployeeUpdateRequest
from app.services import activity_service, user_service

_ORG_ADMIN_ROLE = "ORG_ADMIN"

# --- display order ---------------------------------------------------------
#
# Every employee holds a hidden `display_order` among the others in the same
# (organization, department) "scope"; `department_id IS NULL` is the
# Unassigned scope. The org chart is sorted by it. It is kept dense (1..n)
# and duplicate-free by the helpers below, and every writer takes a
# per-scope advisory lock first, so two admins adding or reordering at the
# same moment can't both claim the same position.


def _scope_filter(
    organization_id: uuid.UUID, department_id: uuid.UUID | None
) -> tuple[ColumnElement[bool], ...]:
    in_department = (
        Employee.department_id.is_(None)
        if department_id is None
        else Employee.department_id == department_id
    )
    return (Employee.organization_id == organization_id, in_department)


async def _lock_scope(
    db: AsyncSession, organization_id: uuid.UUID, department_id: uuid.UUID | None
) -> None:
    """Transaction-scoped lock: released automatically on commit/rollback."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"employee-order:{organization_id}:{department_id}"},
    )


async def _next_display_order(
    db: AsyncSession, organization_id: uuid.UUID, department_id: uuid.UUID | None
) -> int:
    # Counts every row in the scope — inactive and soft-deleted ones keep
    # their slot — so a new employee never lands on an occupied position.
    highest = await db.scalar(
        select(func.coalesce(func.max(Employee.display_order), 0)).where(
            *_scope_filter(organization_id, department_id)
        )
    )
    return (highest or 0) + 1


async def _scope_rows(
    db: AsyncSession, organization_id: uuid.UUID, department_id: uuid.UUID | None
) -> list[Employee]:
    """Every employee in the scope, in display order. `populate_existing`
    because callers hold the scope lock and must see the rows as they are
    now, not as an earlier query in the same session cached them."""
    result = await db.execute(
        select(Employee)
        .where(*_scope_filter(organization_id, department_id))
        .order_by(Employee.display_order, Employee.created_at, Employee.id)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


def _renumber(employees: Sequence[Employee]) -> None:
    for position, employee in enumerate(employees, start=1):
        if employee.display_order != position:
            employee.display_order = position


def _is_active(employee: Employee) -> bool:
    return employee.deleted_at is None and employee.employment_status == EmploymentStatus.ACTIVE


async def create_employee(
    db: AsyncSession, *, organization_id: uuid.UUID, actor: User, payload: EmployeeCreateRequest
) -> Employee:
    await _lock_scope(db, organization_id, payload.department_id)
    employee = Employee(
        display_order=await _next_display_order(db, organization_id, payload.department_id),
        organization_id=organization_id,
        full_name=payload.full_name,
        email=payload.email,
        phone=payload.phone,
        employee_code=payload.employee_code,
        designation=payload.designation,
        department_id=payload.department_id,
        manager_id=payload.manager_id,
        joining_date=payload.joining_date,
        location=payload.location,
        user_id=payload.user_id,
        created_by=actor.id,
    )
    db.add(employee)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "An employee with this email already exists in this organization."
        ) from exc

    await activity_service.record_activity(
        db,
        organization_id=organization_id,
        actor=actor,
        action="EMPLOYEE_CREATED",
        entity_type="employee",
        entity_id=employee.id,
        entity_label=f"{employee.full_name} ({employee.email})",
        description=f"Employee {employee.full_name} was added.",
    )
    return employee


async def list_employees(
    db: AsyncSession, organization_id: uuid.UUID, *, department_id: uuid.UUID | None = None
) -> list[Employee]:
    query = (
        select(Employee)
        .where(Employee.organization_id == organization_id, Employee.deleted_at.is_(None))
        .order_by(Employee.display_order, Employee.created_at, Employee.id)
    )
    if department_id is not None:
        query = query.where(Employee.department_id == department_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_employee(db: AsyncSession, employee_id: uuid.UUID) -> Employee | None:
    return await db.get(Employee, employee_id)


async def get_department_names_for(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Small helper for denormalizing `department_name`/`manager_name` on
    `EmployeeResponse` — a single lookup for the department table, one for
    the (self-referential) employee table, both callable with whatever id
    set the response layer already has in hand."""
    if not ids:
        return {}
    result = await db.execute(select(Department.id, Department.name).where(Department.id.in_(ids)))
    return dict(result.tuples().all())


async def get_employee_names_for(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    result = await db.execute(select(Employee.id, Employee.full_name).where(Employee.id.in_(ids)))
    return dict(result.tuples().all())


async def update_employee(
    db: AsyncSession, employee: Employee, payload: EmployeeUpdateRequest, *, actor: User
) -> Employee:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return employee

    for field, value in updates.items():
        setattr(employee, field, value)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "An employee with this email already exists in this organization."
        ) from exc

    await activity_service.record_activity(
        db,
        organization_id=employee.organization_id,
        actor=actor,
        action="EMPLOYEE_UPDATED",
        entity_type="employee",
        entity_id=employee.id,
        entity_label=employee.full_name,
        description=f"Employee {employee.full_name} was updated ({', '.join(sorted(updates))}).",
    )
    return employee


async def move_employee(
    db: AsyncSession, employee: Employee, *, new_department_id: uuid.UUID | None, actor: User
) -> Employee:
    if new_department_id == employee.department_id:
        return employee

    if new_department_id is not None:
        department = await db.get(Department, new_department_id)
        if department is None or department.deleted_at is not None:
            raise NotFoundError("Department not found.")
        new_department_name = department.name
    else:
        new_department_name = "no department"

    old_department_name = "no department"
    if employee.department_id is not None:
        old_department = await db.get(Department, employee.department_id)
        if old_department is not None:
            old_department_name = old_department.name

    # A moved employee joins the end of the new list, and the list they left
    # is closed up. Both scopes are locked in a fixed order so two concurrent
    # moves in opposite directions can't deadlock.
    old_department_id = employee.department_id
    for department_id in sorted({old_department_id, new_department_id}, key=str):
        await _lock_scope(db, employee.organization_id, department_id)
    new_display_order = await _next_display_order(db, employee.organization_id, new_department_id)

    employee.department_id = new_department_id
    employee.display_order = new_display_order
    await db.flush()
    _renumber(await _scope_rows(db, employee.organization_id, old_department_id))
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=employee.organization_id,
        actor=actor,
        action="EMPLOYEE_MOVED",
        entity_type="employee",
        entity_id=employee.id,
        entity_label=employee.full_name,
        description=(
            f"Employee {employee.full_name} was moved from {old_department_name} to "
            f"{new_department_name}."
        ),
    )
    return employee


async def deactivate_employee(
    db: AsyncSession, employee: Employee, *, actor: User, reason: str | None
) -> Employee:
    if employee.employment_status == EmploymentStatus.INACTIVE:
        raise ConflictError("This employee is already inactive.")

    employee.employment_status = EmploymentStatus.INACTIVE
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=employee.organization_id,
        actor=actor,
        action="EMPLOYEE_DEACTIVATED",
        entity_type="employee",
        entity_id=employee.id,
        entity_label=employee.full_name,
        description=f"Employee {employee.full_name} was deactivated.",
        reason=reason,
    )
    return employee


async def reactivate_employee(db: AsyncSession, employee: Employee, *, actor: User) -> Employee:
    if employee.employment_status == EmploymentStatus.ACTIVE:
        raise ConflictError("This employee is already active.")

    employee.employment_status = EmploymentStatus.ACTIVE
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=employee.organization_id,
        actor=actor,
        action="EMPLOYEE_REACTIVATED",
        entity_type="employee",
        entity_id=employee.id,
        entity_label=employee.full_name,
        description=f"Employee {employee.full_name} was reactivated.",
    )
    return employee


async def delete_employee(
    db: AsyncSession,
    employee: Employee,
    *,
    actor: User,
    reason: str | None,
    can_manage_admins: bool,
) -> Employee:
    """Removes someone who left the company: a soft delete (`deleted_at`),
    the same archive-in-place lifecycle departments use. Every employee list,
    department count and the org chart already exclude deleted rows, so the
    person disappears from the hierarchy at once — while the row, and
    everything that references it, stays intact for history and the audit
    trail (Activities keep the actor and the name).

    The record linked to the caller's own portal account can't be removed by
    the caller, and one linked to an Org Admin's account needs
    `user.manage_admins` (ORG_ADMIN) — the same boundary as account
    management (app/services/user_service.py). The linked portal account
    itself is untouched: it is deactivated separately, from Team → Accounts.

    Anyone who reported to the removed employee loses that manager link
    (what `ON DELETE SET NULL` would do on a hard delete), so a departed
    person never shows up as somebody's current manager.
    """
    if employee.deleted_at is not None:
        raise ConflictError("This employee has already been removed.")
    if employee.user_id is not None:
        if employee.user_id == actor.id:
            raise ForbiddenError("You cannot remove your own employee record.")
        if not can_manage_admins and _ORG_ADMIN_ROLE in await user_service.get_user_role_names(
            db, employee.user_id
        ):
            raise ForbiddenError("Only an Org Admin can remove an Org Admin's employee record.")

    employee.deleted_at = datetime.now(UTC)
    employee.deleted_by_user_id = actor.id
    employee.deletion_reason = reason
    await db.execute(
        update(Employee)
        .where(
            Employee.organization_id == employee.organization_id,
            Employee.manager_id == employee.id,
        )
        .values(manager_id=None)
    )
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=employee.organization_id,
        actor=actor,
        action="EMPLOYEE_DELETED",
        entity_type="employee",
        entity_id=employee.id,
        entity_label=f"{employee.full_name} ({employee.email})",
        description=f"Employee {employee.full_name} was removed.",
        reason=reason,
    )
    return employee


async def reorder_employees(
    db: AsyncSession, *, organization_id: uuid.UUID, actor: User, employee_ids: list[uuid.UUID]
) -> list[Employee]:
    """Persist a new order for one department's (or the Unassigned group's)
    active employees. `employee_ids` must be exactly that group, first to
    last — a partial list would leave the intended position of everyone else
    ambiguous, so it is rejected rather than guessed at.

    Employees who are inactive keep their place *between* the others (they
    are never touched or displaced), so reactivating one later doesn't shuffle
    the chart; the whole scope is then renumbered 1..n in a single flush,
    inside the request's transaction — all of it applies or none of it does.

    Missing ids and ids from another organization are indistinguishable
    (both "not found"): the lookup is scoped to `organization_id`, on top of
    RLS, so nothing about another tenant is ever revealed.
    """
    if len(set(employee_ids)) != len(employee_ids):
        raise UnprocessableError("employee_ids must not contain duplicates.")

    requested = list(
        (
            await db.execute(
                select(Employee).where(
                    Employee.organization_id == organization_id, Employee.id.in_(employee_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    if len(requested) != len(employee_ids):
        raise NotFoundError("One or more employees were not found.")
    if not all(_is_active(employee) for employee in requested):
        raise UnprocessableError("Only active employees can be reordered.")
    department_ids = {employee.department_id for employee in requested}
    if len(department_ids) != 1:
        raise UnprocessableError("Employees can only be reordered within a single department.")
    department_id = department_ids.pop()

    # Re-read the scope under the lock: someone may have added, moved or
    # deactivated an employee since the lookup above.
    await _lock_scope(db, organization_id, department_id)
    scope_rows = await _scope_rows(db, organization_id, department_id)
    active_by_id = {employee.id: employee for employee in scope_rows if _is_active(employee)}
    if set(active_by_id) != set(employee_ids):
        raise ConflictError(
            "This department's employees have changed. Refresh the page and try again."
        )

    new_order = iter(active_by_id[employee_id] for employee_id in employee_ids)
    final = [next(new_order) if _is_active(employee) else employee for employee in scope_rows]
    _renumber(final)
    await db.flush()

    department = await db.get(Department, department_id) if department_id is not None else None
    scope_label = department.name if department is not None else "Unassigned"
    await activity_service.record_activity(
        db,
        organization_id=organization_id,
        actor=actor,
        action="EMPLOYEE_REORDERED",
        entity_type="department" if department is not None else "employee",
        entity_id=department.id if department is not None else None,
        entity_label=scope_label,
        description=f"Employee order in {scope_label} was changed.",
    )
    return [employee for employee in final if _is_active(employee)]
