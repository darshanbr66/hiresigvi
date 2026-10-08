"""User management within the caller's own organization.

`organization_id` is always taken from the authenticated caller, never from
the request — see app/schemas/user.py and docs/security.md § 2.
"""

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import granted_permission_codes, require_permission
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserCreateRequest, UserResponse, UserUpdateRequest
from app.services import user_service

router = APIRouter(prefix="/users", tags=["recruiter-users"])


async def _can_manage_admins(db: AsyncSession, user: User) -> bool:
    """`user.create`/`user.update` are held by ORG_ADMIN and HR (RECRUITER);
    only `user.manage_admins` (ORG_ADMIN) reaches Org Admin accounts — the
    service enforces it."""
    return user_service.MANAGE_ADMINS_PERMISSION in await granted_permission_codes(db, user)


async def _to_response(db: AsyncSession, user: User) -> UserResponse:
    roles = await user_service.get_user_role_names(db, user.id)
    return UserResponse(
        id=user.id,
        organization_id=user.organization_id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        created_at=user.created_at,
        roles=roles,
    )


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateRequest,
    current_user: User = Depends(require_permission("user.create")),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    # current_user.organization_id is guaranteed non-None here: only tenant
    # users can hold the "user.create" permission (see the Phase 2
    # migration's role_permissions seed — SUPER_ADMIN is not granted it).
    assert current_user.organization_id is not None
    created = await user_service.create_tenant_user(
        db,
        organization_id=current_user.organization_id,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        role_name=payload.role.value,
        can_manage_admins=await _can_manage_admins(db, current_user),
        actor=current_user,
    )
    return await _to_response(db, created)


@router.get("", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(require_permission("user.read")),
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    assert current_user.organization_id is not None
    users = await user_service.list_organization_users(db, current_user.organization_id)
    return [await _to_response(db, user) for user in users]


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: uuid.UUID,
    _: User = Depends(require_permission("user.read")),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    user = await user_service.get_organization_user(db, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    return await _to_response(db, user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateRequest,
    current_user: User = Depends(require_permission("user.update")),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Deactivate/reactivate a team member or change their role
    (app/services/user_service.py::update_team_member owns every guard —
    self-deactivation, last-admin protection, and Org Admin changes
    requiring `user.manage_admins` — and the audit trail)."""
    target = await user_service.get_organization_user(db, user_id)
    if target is None:
        raise NotFoundError("User not found.")
    updated = await user_service.update_team_member(
        db,
        actor=current_user,
        target=target,
        payload=payload,
        can_manage_admins=await _can_manage_admins(db, current_user),
    )
    return await _to_response(db, updated)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    current_user: User = Depends(require_permission(user_service.MANAGE_ADMINS_PERMISSION)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Permanently delete a deactivated team account — Org Admins only
    (`user.manage_admins`). The guards, and what happens to the account's
    data, live in app/services/user_service.py::delete_team_member."""
    target = await user_service.get_organization_user(db, user_id)
    if target is None:
        raise NotFoundError("User not found.")
    await user_service.delete_team_member(db, actor=current_user, target=target)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
