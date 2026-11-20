"""Operational CLI for one-time bootstrap tasks that cannot go through the
HTTP API, because there is no authenticated principal yet capable of
calling it.

`/api/v1/admin/organizations` requires a SUPER_ADMIN token, and a
SUPER_ADMIN account (organization_id IS NULL) cannot be created through any
tenant-scoped or self-service flow — every other account-creation path in
this system exists specifically to prevent an unauthenticated caller from
creating a platform-level account, which is exactly what the very first
SUPER_ADMIN would otherwise require. This script is the one deliberate,
out-of-band exception: it runs with direct DB access (RLS bypass) as a
local operator action, not as an HTTP endpoint.

Usage:
    python -m app.cli create-superadmin \\
        --email admin@example.com --password "..." --full-name "Jane Doe"

    # A realistic, clearly-labelled demo job (app/demo_data.py) for
    # exercising AI resume screening — DRAFT unless --open is given.
    python -m app.cli seed-demo-job --org-slug zodiac-patent \\
        --created-by-email hr.admin@example.com [--open]
"""

import argparse
import asyncio
import sys

from app.core.asyncio_compat import configure_event_loop_policy

configure_event_loop_policy()

from sqlalchemy import select  # noqa: E402

from app import demo_data  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.rls import rls_bypass, set_tenant_context  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.models.job import Job, JobStatus  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.rbac import Role, UserRole  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.job import JobCreateRequest, JobUpdateRequest  # noqa: E402
from app.services import job_service  # noqa: E402

_MIN_PASSWORD_LENGTH = 10


async def create_superadmin(*, email: str, password: str, full_name: str) -> None:
    async with AsyncSessionLocal() as db:
        async with rls_bypass(db):
            existing = await db.scalar(
                select(User).where(User.organization_id.is_(None), User.email == email)
            )
            if existing is not None:
                print(f"A platform account with email {email!r} already exists.", file=sys.stderr)
                raise SystemExit(1)

            role = await db.scalar(
                select(Role).where(Role.organization_id.is_(None), Role.name == "SUPER_ADMIN")
            )
            if role is None:
                print(
                    "System role SUPER_ADMIN is not seeded — run "
                    "`alembic upgrade head` first.",
                    file=sys.stderr,
                )
                raise SystemExit(1)

            user = User(
                organization_id=None,
                email=email,
                hashed_password=hash_password(password),
                full_name=full_name,
            )
            db.add(user)
            await db.flush()
            db.add(UserRole(user_id=user.id, role_id=role.id))
            await db.flush()

        await db.commit()

    print(f"Created SUPER_ADMIN account: {email}")


async def seed_demo_job(*, org_slug: str, created_by_email: str, publish: bool) -> None:
    """Idempotent: an existing demo job (same title, not deleted) in the
    organization is left as it is. Created through job_service, so it is
    audited like any recruiter-created job, attributed to a real staff user
    of that organization."""
    async with AsyncSessionLocal() as db:
        async with rls_bypass(db):
            organization = await db.scalar(select(Organization).where(Organization.slug == org_slug))
            actor = (
                await db.scalar(
                    select(User).where(
                        User.organization_id == organization.id, User.email == created_by_email
                    )
                )
                if organization is not None
                else None
            )
        if organization is None or actor is None:
            print(
                f"Organization {org_slug!r} or its user {created_by_email!r} was not found.",
                file=sys.stderr,
            )
            raise SystemExit(1)

        await set_tenant_context(db, organization.id)
        existing = await db.scalar(
            select(Job).where(Job.title == demo_data.DEMO_JOB_TITLE, Job.deleted_at.is_(None))
        )
        if existing is not None:
            print(f"Demo job already exists ({existing.id}, {existing.status.value}); unchanged.")
            return

        job = await job_service.create_job(
            db,
            actor=actor,
            payload=JobCreateRequest(
                title=demo_data.DEMO_JOB_TITLE,
                department=demo_data.DEMO_JOB_DEPARTMENT,
                location=demo_data.DEMO_JOB_LOCATION,
                employment_type=demo_data.DEMO_JOB_EMPLOYMENT_TYPE,
                description=demo_data.DEMO_JOB_DESCRIPTION,
            ),
        )
        if publish:
            job = await job_service.update_job(
                db, job, JobUpdateRequest(status=JobStatus.OPEN), actor=actor
            )
        await db.commit()
    print(f"Created demo job {job.id} ({job.status.value}) in {org_slug}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Recruitment Platform operational CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser(
        "create-superadmin", help="Create the first platform administrator account"
    )
    create_parser.add_argument("--email", required=True)
    create_parser.add_argument("--password", required=True)
    create_parser.add_argument("--full-name", required=True)

    demo_parser = subparsers.add_parser(
        "seed-demo-job", help="Create the realistic demo job used to test AI screening"
    )
    demo_parser.add_argument("--org-slug", required=True)
    demo_parser.add_argument("--created-by-email", required=True)
    demo_parser.add_argument("--open", action="store_true", help="Publish it (status OPEN)")

    args = parser.parse_args()

    if args.command == "seed-demo-job":
        asyncio.run(
            seed_demo_job(
                org_slug=args.org_slug, created_by_email=args.created_by_email, publish=args.open
            )
        )

    if args.command == "create-superadmin":
        if len(args.password) < _MIN_PASSWORD_LENGTH:
            parser.error(f"--password must be at least {_MIN_PASSWORD_LENGTH} characters")
        asyncio.run(
            create_superadmin(email=args.email, password=args.password, full_name=args.full_name)
        )


if __name__ == "__main__":
    main()
