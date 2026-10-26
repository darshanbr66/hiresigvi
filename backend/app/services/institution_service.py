"""The organization's institution registry (docs/campus-hiring.md § 1a).
Every lookup is tenant-scoped twice: explicitly by `organization_id` and by
RLS — a cross-tenant id behaves exactly like an unknown one."""

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.text import normalize_name
from app.models.campus_drive import CampusDrive
from app.models.institution import Institution
from app.models.user import User
from app.schemas.institution import InstitutionCreateRequest, InstitutionUpdateRequest
from app.services import activity_service


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_institutions(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    search: str | None = None,
    active_only: bool = False,
) -> list[Institution]:
    query = select(Institution).where(Institution.organization_id == organization_id)
    if active_only:
        query = query.where(Institution.is_active.is_(True))
    if search and search.strip():
        pattern = f"%{_escape_like(search.strip())}%"
        query = query.where(
            or_(
                Institution.name.ilike(pattern, escape="\\"),
                Institution.city.ilike(pattern, escape="\\"),
                Institution.state.ilike(pattern, escape="\\"),
            )
        )
    result = await db.execute(query.order_by(Institution.name))
    return list(result.scalars().all())


async def drive_counts(db: AsyncSession, organization_id: uuid.UUID) -> dict[uuid.UUID, int]:
    result = await db.execute(
        select(CampusDrive.institution_id, func.count(CampusDrive.id))
        .where(CampusDrive.organization_id == organization_id, CampusDrive.deleted_at.is_(None))
        .group_by(CampusDrive.institution_id)
    )
    return {institution_id: count for institution_id, count in result.all()}


async def get_institution(
    db: AsyncSession, organization_id: uuid.UUID, institution_id: uuid.UUID
) -> Institution | None:
    institution = await db.get(Institution, institution_id)
    if institution is None or institution.organization_id != organization_id:
        return None
    return institution


async def require_institution(
    db: AsyncSession, organization_id: uuid.UUID, institution_id: uuid.UUID, *, active: bool = True
) -> Institution:
    """The institution, or a 404 — also for another organization's id, and
    (by default) for an inactive one, which can't be attached to anything
    new."""
    institution = await get_institution(db, organization_id, institution_id)
    if institution is None or (active and not institution.is_active):
        raise NotFoundError("Institution not found.")
    return institution


async def _name_taken(
    db: AsyncSession, organization_id: uuid.UUID, normalized: str, *, exclude_id: uuid.UUID | None
) -> Institution | None:
    query = select(Institution).where(
        Institution.organization_id == organization_id, Institution.normalized_name == normalized
    )
    if exclude_id is not None:
        query = query.where(Institution.id != exclude_id)
    return await db.scalar(query)


def _duplicate_error(existing: Institution) -> ConflictError:
    return ConflictError(
        f'An institution named "{existing.name}" is already registered.',
        code="institution_exists",
        data={"institution_id": str(existing.id)},
    )


async def create_institution(
    db: AsyncSession, *, actor: User, payload: InstitutionCreateRequest
) -> Institution:
    assert actor.organization_id is not None
    normalized = normalize_name(payload.name)
    existing = await _name_taken(db, actor.organization_id, normalized, exclude_id=None)
    if existing is not None:
        raise _duplicate_error(existing)

    institution = Institution(
        organization_id=actor.organization_id,
        created_by=actor.id,
        normalized_name=normalized,
        **{**payload.model_dump(exclude={"email_domains"}), "email_domains": payload.email_domains or []},
    )
    institution.placement_contact_email = (
        str(payload.placement_contact_email) if payload.placement_contact_email else None
    )
    db.add(institution)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:  # lost a race with a concurrent create
        raise ConflictError("An institution with this name is already registered.") from exc

    await activity_service.record_activity(
        db,
        organization_id=institution.organization_id,
        actor=actor,
        action="INSTITUTION_CREATED",
        entity_type="institution",
        entity_id=institution.id,
        entity_label=institution.name,
        description=f'Institution "{institution.name}" was added to the registry.',
    )
    return institution


async def find_or_create_by_name(db: AsyncSession, *, actor: User, name: str) -> Institution:
    """For the legacy `college_name`-only campus-drive request: resolves the
    name against the registry (case/spacing-insensitive) and registers it
    if it's new, so even the old request shape never produces a second
    spelling of the same college."""
    assert actor.organization_id is not None
    existing = await _name_taken(db, actor.organization_id, normalize_name(name), exclude_id=None)
    if existing is not None:
        if not existing.is_active:
            raise ConflictError(
                f'The institution "{existing.name}" is inactive. Reactivate it before creating a drive.',
                code="institution_inactive",
            )
        return existing
    return await create_institution(db, actor=actor, payload=InstitutionCreateRequest(name=name))


async def update_institution(
    db: AsyncSession, institution: Institution, payload: InstitutionUpdateRequest, *, actor: User
) -> Institution:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return institution

    if "name" in updates and updates["name"] is not None:
        normalized = normalize_name(updates["name"])
        existing = await _name_taken(
            db, institution.organization_id, normalized, exclude_id=institution.id
        )
        if existing is not None:
            raise _duplicate_error(existing)
        institution.normalized_name = normalized
    if "email_domains" in updates:
        updates["email_domains"] = updates["email_domains"] or []
    if "placement_contact_email" in updates and updates["placement_contact_email"] is not None:
        updates["placement_contact_email"] = str(updates["placement_contact_email"])

    for field, value in updates.items():
        if field == "name" and value is None:
            continue
        setattr(institution, field, value)

    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        raise ConflictError("An institution with this name is already registered.") from exc

    # Keep every drive's display snapshot of the name in step.
    if "name" in updates and updates["name"] is not None:
        drives = await db.execute(
            select(CampusDrive).where(CampusDrive.institution_id == institution.id)
        )
        for drive in drives.scalars().all():
            drive.college_name = institution.name
        await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=institution.organization_id,
        actor=actor,
        action="INSTITUTION_UPDATED",
        entity_type="institution",
        entity_id=institution.id,
        entity_label=institution.name,
        description=f'Institution "{institution.name}" was updated ({", ".join(sorted(updates))}).',
    )
    return institution
