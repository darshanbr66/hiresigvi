import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import hash_password
from app.db.rls import rls_bypass
from app.models.organization import Organization
from app.models.rbac import Role, UserRole
from app.models.user import User
from app.schemas.organization import OrganizationSettingsUpdateRequest


async def bootstrap_organization(
    db: AsyncSession,
    *,
    name: str,
    slug: str,
    admin_email: str,
    admin_password: str,
    admin_full_name: str,
) -> tuple[Organization, User]:
    """Creates an Organization and its first ORG_ADMIN user in one
    transaction.

    Must be called with RLS bypass already active (see
    app/api/deps.py::get_current_super_admin) — organization creation is
    inherently cross-tenant, since the tenant being created doesn't exist
    yet for ordinary tenant-scoped RLS to apply to (docs/security.md § 3).
    """
    org_admin_role = await db.scalar(
        select(Role).where(Role.organization_id.is_(None), Role.name == "ORG_ADMIN")
    )
    if org_admin_role is None:
        raise RuntimeError("System role ORG_ADMIN is not seeded — check migrations.")

    organization = Organization(name=name, slug=slug)
    db.add(organization)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ConflictError("An organization with this slug already exists.") from exc

    admin_user = User(
        organization_id=organization.id,
        email=admin_email,
        hashed_password=hash_password(admin_password),
        full_name=admin_full_name,
    )
    db.add(admin_user)
    await db.flush()

    db.add(UserRole(user_id=admin_user.id, role_id=org_admin_role.id))
    await db.flush()

    return organization, admin_user


async def list_organizations(db: AsyncSession) -> list[Organization]:
    result = await db.execute(select(Organization).order_by(Organization.created_at))
    return list(result.scalars().all())


async def get_organization_by_slug(db: AsyncSession, slug: str) -> Organization | None:
    """`organizations` is itself RLS-protected, keyed on its own `id` rather
    than an `organization_id` column (see the identity/tenancy foundation
    migration) — so resolving a tenant from a slug is exactly the
    "identity not known yet" bootstrap case `rls_bypass` documents (see
    app/db/rls.py). Used by the anonymous public career site before any
    Candidate/Job/Application query, which get properly tenant-scoped via
    `set_tenant_context()` immediately afterward — this bypass only covers
    the one lookup needed to find the org id in the first place.

    A slug the organization used before it was renamed
    (`settings.organization_slug_aliases`) resolves to the organization's
    current record, so old public links keep working. The reverse also holds
    until the rename has been applied (deploy order: a frontend already using
    the new slug may reach a database not yet migrated). An exact match always
    wins: aliases are only consulted when no organization has the slug."""
    async with rls_bypass(db):
        organization = await db.scalar(select(Organization).where(Organization.slug == slug))
        if organization is not None:
            return organization
        aliases = get_settings().organization_slug_aliases
        fallbacks = [aliases[slug]] if slug in aliases else []
        fallbacks += [old for old, current in aliases.items() if current == slug]
        for candidate in fallbacks:
            if candidate == slug:
                continue
            organization = await db.scalar(
                select(Organization).where(Organization.slug == candidate)
            )
            if organization is not None:
                return organization
        return None


async def update_organization_settings(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    payload: OrganizationSettingsUpdateRequest,
) -> Organization:
    """SUPER_ADMIN path only — the caller (`get_current_super_admin`) has
    already enabled the RLS bypass a platform-level edit of a tenant root
    row needs."""
    organization = await db.get(Organization, organization_id)
    if organization is None:
        raise NotFoundError("Organization not found.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(organization, field, value)
    await db.flush()
    return organization
