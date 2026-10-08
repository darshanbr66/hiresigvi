"""Permanently deleting a deactivated team account
(DELETE /api/v1/recruiter/users/{id} — app/services/user_service.py::
delete_team_member).

Org Admins only (`user.manage_admins`); never one's own account, never an
active account, never the last Org Admin, never another organization's user
(404). What happens to the account's data follows the schema's foreign keys:
its login-private data (roles, sessions, own inbox) goes; shared history
(activities, messages it sent, a linked employee record) stays with the link
cleared; and an account that authored recruitment records (jobs, notes, …)
is refused rather than losing that history.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError
from app.db.rls import rls_bypass
from app.models.activity import Activity
from app.models.notification import Notification
from app.models.rbac import UserRole
from app.models.team_hierarchy import Employee
from app.models.user import User, UserRefreshToken
from app.services import user_service
from tests.test_hr_team_management import _bootstrap_org, _create_user, _headers

PASSWORD = "TeamMemberPass1"


async def _org(client: AsyncClient, slug: str) -> dict:
    org = await _bootstrap_org(client, slug)
    admin_headers = await _headers(client, org["admin_email"], org["admin_password"])
    me = await client.get("/api/v1/recruiter/auth/me", headers=admin_headers)
    return {**org, "admin_headers": admin_headers, "admin_id": me.json()["id"]}


async def _deactivate(client: AsyncClient, headers: dict, user_id: str) -> None:
    response = await client.patch(
        f"/api/v1/recruiter/users/{user_id}", json={"is_active": False}, headers=headers
    )
    assert response.status_code == 200, response.text


async def _delete(client: AsyncClient, headers: dict, user_id: str):
    return await client.delete(f"/api/v1/recruiter/users/{user_id}", headers=headers)


async def _user_exists(db: AsyncSession, user_id: str) -> bool:
    async with rls_bypass(db):
        return await db.get(User, uuid.UUID(user_id), populate_existing=True) is not None


async def test_org_admin_deletes_a_deactivated_account(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    org = await _org(client, "del-basic")
    member = await _create_user(client, org["admin_headers"], "del-basic", "rita", "RECRUITER")
    await _deactivate(client, org["admin_headers"], member["id"])

    response = await _delete(client, org["admin_headers"], member["id"])
    assert response.status_code == 204, response.text

    listing = await client.get("/api/v1/recruiter/users", headers=org["admin_headers"])
    assert member["id"] not in {u["id"] for u in listing.json()}
    assert not await _user_exists(db_session, member["id"])
    # A second attempt: the account is gone.
    again = await _delete(client, org["admin_headers"], member["id"])
    assert again.status_code == 404


async def test_an_active_account_cannot_be_deleted(client: AsyncClient, super_admin: User) -> None:
    org = await _org(client, "del-active")
    member = await _create_user(client, org["admin_headers"], "del-active", "ava", "INTERVIEWER")

    response = await _delete(client, org["admin_headers"], member["id"])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "user_active"
    listing = await client.get("/api/v1/recruiter/users", headers=org["admin_headers"])
    assert member["id"] in {u["id"] for u in listing.json()}


async def test_an_org_admin_cannot_delete_their_own_account(
    client: AsyncClient, super_admin: User
) -> None:
    org = await _org(client, "del-self")
    response = await _delete(client, org["admin_headers"], org["admin_id"])
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "cannot_delete_self"


@pytest.mark.parametrize("role", ["RECRUITER", "HIRING_MANAGER", "INTERVIEWER"])
async def test_only_org_admins_can_delete_accounts(
    client: AsyncClient, super_admin: User, db_session: AsyncSession, role: str
) -> None:
    slug = f"del-role-{role.lower().replace('_', '-')}"
    org = await _org(client, slug)
    caller = await _create_user(client, org["admin_headers"], slug, "caller", role)
    target = await _create_user(client, org["admin_headers"], slug, "target", "INTERVIEWER")
    await _deactivate(client, org["admin_headers"], target["id"])
    caller_headers = await _headers(client, caller["email"], PASSWORD)

    response = await _delete(client, caller_headers, target["id"])
    assert response.status_code == 403, response.text
    assert await _user_exists(db_session, target["id"])


async def test_another_organizations_account_is_not_found(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    acme = await _org(client, "del-tenant-a")
    globex = await _org(client, "del-tenant-b")
    other = await _create_user(client, globex["admin_headers"], "del-tenant-b", "gus", "RECRUITER")
    await _deactivate(client, globex["admin_headers"], other["id"])

    response = await _delete(client, acme["admin_headers"], other["id"])
    assert response.status_code == 404, response.text
    assert await _user_exists(db_session, other["id"])
    # and an id that never existed
    missing = await _delete(client, acme["admin_headers"], str(uuid.uuid4()))
    assert missing.status_code == 404


async def test_the_last_org_admin_cannot_be_deleted(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    """Through the API the caller is always an active Org Admin (and can't
    delete themselves), so the guard is checked on the service directly: an
    inactive Org Admin with no other active admin left is never deleted."""
    org = await _org(client, "del-last-admin")
    async with rls_bypass(db_session):
        admin = await db_session.get(User, uuid.UUID(org["admin_id"]))
        assert admin is not None
        admin.is_active = False
        other_actor = User(
            organization_id=admin.organization_id,
            email="someone@del-last-admin.dev",
            hashed_password="x",
            full_name="Someone",
        )
        db_session.add(other_actor)
        await db_session.flush()
        with pytest.raises(ForbiddenError) as excinfo:
            await user_service.delete_team_member(db_session, actor=other_actor, target=admin)
    assert excinfo.value.code == "last_org_admin"


async def test_a_deactivated_org_admin_can_be_deleted_by_another_org_admin(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    org = await _org(client, "del-other-admin")
    second = await _create_user(
        client, org["admin_headers"], "del-other-admin", "second", "ORG_ADMIN"
    )
    await _deactivate(client, org["admin_headers"], second["id"])

    response = await _delete(client, org["admin_headers"], second["id"])
    assert response.status_code == 204, response.text
    assert not await _user_exists(db_session, second["id"])


async def test_sessions_roles_and_inbox_go_history_stays(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    org = await _org(client, "del-data")
    member = await _create_user(client, org["admin_headers"], "del-data", "maya", "RECRUITER")
    member_headers = await _headers(client, member["email"], PASSWORD)  # a session
    member_id = uuid.UUID(member["id"])

    # Shared history created by the account while it was active: a
    # department it created (an activity with it as the actor) and a
    # message it sent; plus a message it received (its own inbox).
    created = await client.post(
        "/api/v1/recruiter/departments", json={"name": "Research"}, headers=member_headers
    )
    assert created.status_code == 201, created.text
    sent = await client.post(
        "/api/v1/recruiter/notifications/send",
        json={
            "recipient_user_id": org["admin_id"],
            "title": "Handover",
            "message": "Notes attached.",
        },
        headers=member_headers,
    )
    assert sent.status_code == 201, sent.text
    received = await client.post(
        "/api/v1/recruiter/notifications/send",
        json={"recipient_user_id": member["id"], "title": "Welcome", "message": "Hi Maya"},
        headers=org["admin_headers"],
    )
    assert received.status_code == 201, received.text
    # Their HR employee record, linked to the login.
    employee = await client.post(
        "/api/v1/recruiter/employees",
        json={"full_name": "Maya", "email": member["email"], "user_id": member["id"]},
        headers=org["admin_headers"],
    )
    assert employee.status_code == 201, employee.text
    assert employee.json()["user_id"] == member["id"]

    await _deactivate(client, org["admin_headers"], member["id"])
    response = await _delete(client, org["admin_headers"], member["id"])
    assert response.status_code == 204, response.text

    async with rls_bypass(db_session):
        # login-private data is gone: roles, every session, own inbox
        assert (
            await db_session.scalar(
                select(func.count()).select_from(UserRole).where(UserRole.user_id == member_id)
            )
            == 0
        )
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(UserRefreshToken)
                .where(UserRefreshToken.user_id == member_id)
            )
            == 0
        )
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.recipient_user_id == member_id)
            )
            == 0
        )
        # the message it sent is still in the admin's inbox, sender detached
        kept = (
            await db_session.execute(
                select(Notification)
                .where(Notification.title == "Handover")
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert kept.sender_user_id is None
        # its activity history stays, with the actor's name as written
        acts = (
            (
                await db_session.execute(
                    select(Activity)
                    .where(Activity.actor_name == "Maya")
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        assert acts and all(a.actor_user_id is None for a in acts)
        # the HR employee record stays; it just has no login any more
        record = await db_session.get(
            Employee, uuid.UUID(employee.json()["id"]), populate_existing=True
        )
        assert record is not None and record.deleted_at is None
        assert record.user_id is None

    # the deletion itself is audited
    activities = await client.get("/api/v1/recruiter/activities", headers=org["admin_headers"])
    deleted = [a for a in activities.json() if a["action"] == "USER_DELETED"]
    assert len(deleted) == 1
    assert deleted[0]["entity_id"] == member["id"]
    assert member["email"] in deleted[0]["entity_label"]

    # the admin's inbox still renders the message from the deleted account
    inbox = await client.get("/api/v1/recruiter/notifications", headers=org["admin_headers"])
    assert inbox.status_code == 200, inbox.text
    assert "Handover" in inbox.text

    # and the old access token no longer works
    me = await client.get("/api/v1/recruiter/auth/me", headers=member_headers)
    assert me.status_code == 401


async def test_an_account_that_authored_recruitment_records_is_kept(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    org = await _org(client, "del-history")
    member = await _create_user(client, org["admin_headers"], "del-history", "jay", "RECRUITER")
    member_headers = await _headers(client, member["email"], PASSWORD)
    job = await client.post(
        "/api/v1/recruiter/jobs",
        json={"title": "Analyst", "description": "SQL and dashboards."},
        headers=member_headers,
    )
    assert job.status_code == 201, job.text
    await _deactivate(client, org["admin_headers"], member["id"])

    response = await _delete(client, org["admin_headers"], member["id"])
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "user_has_history"
    assert "1 job" in error["message"]
    assert error["data"] == {"authored": {"job": 1}}
    assert await _user_exists(db_session, member["id"])
    # the job and its author are untouched, and no deletion was audited
    fetched = await client.get(
        f"/api/v1/recruiter/jobs/{job.json()['id']}", headers=org["admin_headers"]
    )
    assert fetched.status_code == 200
    activities = await client.get("/api/v1/recruiter/activities", headers=org["admin_headers"])
    assert not [a for a in activities.json() if a["action"] == "USER_DELETED"]
