import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.security import hash_password
from app.models.assessment import Assessment
from app.models.calendar_event import CalendarEvent
from app.models.campus_drive import CampusDrive
from app.models.job import Job
from app.models.note import Note
from app.models.rbac import Role, UserRole
from app.models.team_hierarchy import Employee
from app.models.user import User
from app.schemas.user import UserUpdateRequest
from app.services import activity_service

_ORG_ADMIN_ROLE = "ORG_ADMIN"

#: Held by ORG_ADMIN only. `user.create`/`user.update` (which HR — the
#: RECRUITER role — also holds) manage the team; this one additionally
#: covers anything that creates, removes, or acts on an Org Admin, and
#: changing one's own role — so team management can never escalate to admin.
MANAGE_ADMINS_PERMISSION = "user.manage_admins"


def _require_admin_management(can_manage_admins: bool, message: str) -> None:
    if not can_manage_admins:
        raise ForbiddenError(message)


async def create_tenant_user(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    email: str,
    password: str,
    full_name: str,
    role_name: str,
    can_manage_admins: bool,
    actor: User | None = None,
) -> User:
    """`organization_id` must come from the authenticated caller's own
    record — never from client input (see app/schemas/user.py). Runs under
    the caller's normal tenant-scoped RLS context; no bypass needed since
    the caller is only ever creating a user in their own tenant.
    `can_manage_admins`: whether the caller holds `user.manage_admins`.
    """
    if role_name == _ORG_ADMIN_ROLE:
        _require_admin_management(
            can_manage_admins, "Only an Org Admin can add another Org Admin."
        )
    role = await db.scalar(select(Role).where(Role.organization_id.is_(None), Role.name == role_name))
    if role is None:
        raise NotFoundError(f"Role '{role_name}' does not exist.")

    user = User(
        organization_id=organization_id,
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ConflictError("A user with this email already exists in this organization.") from exc

    db.add(UserRole(user_id=user.id, role_id=role.id))
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=organization_id,
        actor=actor,
        action="USER_CREATED",
        entity_type="user",
        entity_id=user.id,
        entity_label=f"{full_name} ({email})",
        description=f"Team member {full_name} was added with role {role_name}.",
    )
    return user


async def list_organization_users(db: AsyncSession, organization_id: uuid.UUID) -> list[User]:
    result = await db.execute(
        select(User).where(User.organization_id == organization_id).order_by(User.created_at)
    )
    return list(result.scalars().all())


async def get_organization_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """RLS scopes this to the caller's own tenant automatically — a
    cross-tenant id simply isn't visible, so this returns None exactly as
    if the row didn't exist (see docs/security.md § 2: 404, not 403, for
    cross-tenant access attempts)."""
    return await db.get(User, user_id)


async def get_user_role_names(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    result = await db.execute(
        select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
    )
    return list(result.scalars().all())


async def _count_other_active_org_admins(
    db: AsyncSession, *, organization_id: uuid.UUID, exclude_user_id: uuid.UUID
) -> int:
    """Used to guard against leaving an organization with no ORG_ADMIN who
    can manage it (QA § 4)."""
    count = await db.scalar(
        select(func.count(func.distinct(User.id)))
        .select_from(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            User.organization_id == organization_id,
            User.is_active.is_(True),
            User.id != exclude_user_id,
            Role.name == _ORG_ADMIN_ROLE,
        )
    )
    return count or 0


async def deactivate_user(
    db: AsyncSession,
    *,
    actor: User,
    target: User,
    reason: str | None,
    can_manage_admins: bool,
) -> User:
    """Deactivation, not a delete — a team member may have created
    jobs/assessments/campus drives (`created_by` is ON DELETE RESTRICT on
    every one of those). Deactivating preserves that history and simply
    revokes login (`get_current_user` rejects `is_active=False`). Removing
    an account for good is a separate, stricter step: `delete_team_member`."""
    if target.id == actor.id:
        raise ConflictError("You cannot deactivate your own account.")
    if not target.is_active:
        raise ConflictError("This team member is already deactivated.")

    target_roles = await get_user_role_names(db, target.id)
    if _ORG_ADMIN_ROLE in target_roles:
        _require_admin_management(
            can_manage_admins, "Only an Org Admin can deactivate another Org Admin."
        )
        assert target.organization_id is not None
        remaining = await _count_other_active_org_admins(
            db, organization_id=target.organization_id, exclude_user_id=target.id
        )
        if remaining < 1:
            raise ConflictError(
                "Cannot deactivate the organization's last admin — promote another "
                "team member to Org Admin first."
            )

    target.is_active = False
    await db.flush()

    assert target.organization_id is not None
    await activity_service.record_activity(
        db,
        organization_id=target.organization_id,
        actor=actor,
        action="USER_DEACTIVATED",
        entity_type="user",
        entity_id=target.id,
        entity_label=f"{target.full_name} ({target.email})",
        description=f"Team member {target.full_name} was deactivated.",
        reason=reason,
    )
    return target


async def reactivate_user(
    db: AsyncSession, *, actor: User, target: User, can_manage_admins: bool
) -> User:
    if target.is_active:
        raise ConflictError("This team member is already active.")
    if _ORG_ADMIN_ROLE in await get_user_role_names(db, target.id):
        _require_admin_management(
            can_manage_admins, "Only an Org Admin can reactivate an Org Admin."
        )

    target.is_active = True
    await db.flush()

    assert target.organization_id is not None
    await activity_service.record_activity(
        db,
        organization_id=target.organization_id,
        actor=actor,
        action="USER_REACTIVATED",
        entity_type="user",
        entity_id=target.id,
        entity_label=f"{target.full_name} ({target.email})",
        description=f"Team member {target.full_name} was reactivated.",
    )
    return target


async def change_user_role(
    db: AsyncSession, *, actor: User, target: User, new_role_name: str, can_manage_admins: bool
) -> User:
    current_roles = await get_user_role_names(db, target.id)
    if not can_manage_admins:
        if target.id == actor.id:
            raise ForbiddenError("You cannot change your own role.")
        if _ORG_ADMIN_ROLE in current_roles or new_role_name == _ORG_ADMIN_ROLE:
            raise ForbiddenError("Only an Org Admin can grant or remove the Org Admin role.")
    if new_role_name in current_roles:
        return target

    if _ORG_ADMIN_ROLE in current_roles and new_role_name != _ORG_ADMIN_ROLE:
        assert target.organization_id is not None
        remaining = await _count_other_active_org_admins(
            db, organization_id=target.organization_id, exclude_user_id=target.id
        )
        if remaining < 1:
            raise ConflictError(
                "Cannot change the organization's last admin's role — promote another "
                "team member to Org Admin first."
            )

    role = await db.scalar(select(Role).where(Role.organization_id.is_(None), Role.name == new_role_name))
    if role is None:
        raise NotFoundError(f"Role '{new_role_name}' does not exist.")

    await db.execute(delete(UserRole).where(UserRole.user_id == target.id))
    db.add(UserRole(user_id=target.id, role_id=role.id))
    await db.flush()

    assert target.organization_id is not None
    old_role_label = ", ".join(current_roles) or "no role"
    await activity_service.record_activity(
        db,
        organization_id=target.organization_id,
        actor=actor,
        action="USER_ROLE_CHANGED",
        entity_type="user",
        entity_id=target.id,
        entity_label=f"{target.full_name} ({target.email})",
        description=(
            f"Team member {target.full_name}'s role changed from "
            f"{old_role_label} to {new_role_name}."
        ),
    )
    return target


async def update_team_member(
    db: AsyncSession,
    *,
    actor: User,
    target: User,
    payload: UserUpdateRequest,
    can_manage_admins: bool,
) -> User:
    """Single entry point the API uses for team management (QA § 4) —
    applies a role change and/or an active-state change from one PATCH,
    each fully guarded and audited by the functions above.
    `can_manage_admins`: whether the caller holds `user.manage_admins`."""
    if payload.role is not None:
        target = await change_user_role(
            db,
            actor=actor,
            target=target,
            new_role_name=payload.role.value,
            can_manage_admins=can_manage_admins,
        )

    if payload.is_active is not None and payload.is_active != target.is_active:
        if payload.is_active:
            target = await reactivate_user(
                db, actor=actor, target=target, can_manage_admins=can_manage_admins
            )
        else:
            target = await deactivate_user(
                db,
                actor=actor,
                target=target,
                reason=payload.reason,
                can_manage_admins=can_manage_admins,
            )

    return target


#: Recruitment/HR records whose author is kept as a non-null reference
#: (ON DELETE RESTRICT): an account that authored any of them is never hard
#: deleted, so no job, assessment, drive, employee record, note or calendar
#: event loses its author. (label, model, author column)
_AUTHORED_HISTORY = (
    ("job", Job, Job.created_by),
    ("assessment", Assessment, Assessment.created_by),
    ("campus drive", CampusDrive, CampusDrive.created_by),
    ("employee record", Employee, Employee.created_by),
    ("note", Note, Note.author_id),
    ("calendar event", CalendarEvent, CalendarEvent.organizer_user_id),
)


async def _authored_history(db: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label, model, column in _AUTHORED_HISTORY:
        count = await db.scalar(select(func.count()).select_from(model).where(column == user_id))
        if count:
            counts[label] = count
    return counts


def _describe_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{n} {label}{'' if n == 1 else 's'}" for label, n in counts.items())


async def delete_team_member(db: AsyncSession, *, actor: User, target: User) -> None:
    """Permanently removes a *deactivated* team member's account (login).

    Only ever reached with `user.manage_admins` (ORG_ADMIN) — the route
    enforces it. Guards: never one's own account; never an active account
    (deactivate first, which is reversible); never the organization's last
    Org Admin; same organization only (RLS already hides other tenants).

    What happens to the account's data is decided by the schema's foreign
    keys, deliberately — a plain SQL DELETE lets Postgres apply them:

    * removed with the login (CASCADE): role assignments, refresh tokens
      (every session ends), the account's own notification inbox, its
      calendar attendances, and its "Talk to Admin" thread;
    * kept, with the link to this account cleared (SET NULL): activities
      (which keep the actor's name as written), application status history,
      screening/matching runs, assessment invitations, every `deleted_by`,
      messages and notifications this account sent, and a linked Employee
      record — the HR record stays, it just no longer has a login;
    * authored recruitment/HR history (RESTRICT) — jobs, assessments,
      campus drives, employee records, notes, calendar events: an account
      that authored any of these is refused (409 `user_has_history`) rather
      than deleting or rewriting that history; it stays deactivated.

    The deletion itself is recorded in Activities first, so the audit trail
    survives the account."""
    if target.id == actor.id:
        raise ForbiddenError("You cannot delete your own account.", code="cannot_delete_self")
    if target.organization_id is None or target.organization_id != actor.organization_id:
        raise NotFoundError("User not found.")
    if target.is_active:
        raise ConflictError(
            "Only a deactivated account can be deleted. Deactivate it first.",
            code="user_active",
        )

    if _ORG_ADMIN_ROLE in await get_user_role_names(db, target.id):
        remaining = await _count_other_active_org_admins(
            db, organization_id=target.organization_id, exclude_user_id=target.id
        )
        if remaining < 1:
            raise ForbiddenError(
                "The organization's last Org Admin can't be deleted.", code="last_org_admin"
            )

    history = await _authored_history(db, target.id)
    if history:
        raise ConflictError(
            f"{target.full_name} can't be deleted because they created "
            f"{_describe_counts(history)}, which must keep their author. "
            "The account stays deactivated: it can't sign in.",
            code="user_has_history",
            data={"authored": history},
        )

    label = f"{target.full_name} ({target.email})"
    await activity_service.record_activity(
        db,
        organization_id=target.organization_id,
        actor=actor,
        action="USER_DELETED",
        entity_type="user",
        entity_id=target.id,
        entity_label=label,
        description=f"The deactivated account of {target.full_name} was permanently deleted.",
    )
    target_id = target.id
    db.expunge(target)
    try:
        async with db.begin_nested():
            await db.execute(delete(User).where(User.id == target_id))
    except IntegrityError as exc:
        # Something authored by the account appeared concurrently.
        raise ConflictError(
            "This account is still referenced by recruitment records and can't be deleted.",
            code="user_has_history",
        ) from exc
