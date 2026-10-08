"""Team management: deactivate/reactivate/role-change on
PATCH /api/v1/recruiter/users/{id} (app/services/user_service.py), and the
Activities audit log's tenant isolation (QA § 4-5)."""

import pytest
from httpx import AsyncClient

from app.models.user import User
from tests.conftest import SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD, login


async def _bootstrap_org(client: AsyncClient, slug: str) -> dict:
    super_admin_tokens = await login(client, email=SUPER_ADMIN_EMAIL, password=SUPER_ADMIN_PASSWORD)
    payload = {
        "name": "Acme Corp",
        "slug": slug,
        "admin_email": f"admin@{slug}.dev",
        "admin_password": "AcmeAdminPass1",
        "admin_full_name": "Acme Admin",
    }
    response = await client.post(
        "/api/v1/admin/organizations",
        json=payload,
        headers={"Authorization": f"Bearer {super_admin_tokens['access_token']}"},
    )
    assert response.status_code == 201, response.text
    return payload


async def _org_admin_headers(client: AsyncClient, org: dict) -> dict:
    tokens = await login(client, email=org["admin_email"], password=org["admin_password"])
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _create_recruiter(client: AsyncClient, headers: dict, org_slug: str) -> dict:
    response = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": f"recruiter@{org_slug}.dev",
            "full_name": "Riya Recruiter",
            "password": "RecruiterPass1",
            "role": "RECRUITER",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _admin_user_id(client: AsyncClient, headers: dict) -> str:
    me = await client.get("/api/v1/recruiter/auth/me", headers=headers)
    return me.json()["id"]


async def test_org_admin_can_deactivate_and_reactivate_a_recruiter(
    client: AsyncClient, super_admin: User
) -> None:
    org = await _bootstrap_org(client, "team-deactivate-happy")
    headers = await _org_admin_headers(client, org)
    recruiter = await _create_recruiter(client, headers, "team-deactivate-happy")

    deactivate = await client.patch(
        f"/api/v1/recruiter/users/{recruiter['id']}",
        json={"is_active": False, "reason": "Left the team."},
        headers=headers,
    )
    assert deactivate.status_code == 200, deactivate.text
    assert deactivate.json()["is_active"] is False

    # A deactivated user can no longer log in.
    login_attempt = await client.post(
        "/api/v1/recruiter/auth/login",
        json={"email": recruiter["email"], "password": "RecruiterPass1"},
    )
    assert login_attempt.status_code == 401

    reactivate = await client.patch(
        f"/api/v1/recruiter/users/{recruiter['id']}",
        json={"is_active": True},
        headers=headers,
    )
    assert reactivate.status_code == 200
    assert reactivate.json()["is_active"] is True

    activities = await client.get("/api/v1/recruiter/activities", headers=headers)
    actions = [a["action"] for a in activities.json()]
    assert "USER_DEACTIVATED" in actions
    assert "USER_REACTIVATED" in actions


async def test_org_admin_cannot_deactivate_their_own_account(
    client: AsyncClient, super_admin: User
) -> None:
    org = await _bootstrap_org(client, "team-self-deactivate")
    headers = await _org_admin_headers(client, org)
    admin_id = await _admin_user_id(client, headers)

    response = await client.patch(
        f"/api/v1/recruiter/users/{admin_id}",
        json={"is_active": False},
        headers=headers,
    )
    assert response.status_code == 409


async def test_cannot_deactivate_the_last_org_admin(client: AsyncClient, super_admin: User) -> None:
    org = await _bootstrap_org(client, "team-last-admin-deactivate")
    admin_headers = await _org_admin_headers(client, org)

    # A second admin is created solely to attempt deactivating the first —
    # deactivating one's own account is already rejected on its own
    # (test_org_admin_cannot_deactivate_their_own_account), so this
    # isolates the "last admin" rule specifically.
    second_admin = (
        await client.post(
            "/api/v1/recruiter/users",
            json={
                "email": "second-admin@team-last-admin-deactivate.dev",
                "full_name": "Second Admin",
                "password": "SecondAdminPass1",
                "role": "ORG_ADMIN",
            },
            headers=admin_headers,
        )
    ).json()
    second_admin_tokens = await login(
        client, email=second_admin["email"], password="SecondAdminPass1"
    )
    second_admin_headers = {"Authorization": f"Bearer {second_admin_tokens['access_token']}"}

    first_admin_id = await _admin_user_id(client, admin_headers)

    # Deactivating the first admin is fine while a second one remains.
    deactivate_first = await client.patch(
        f"/api/v1/recruiter/users/{first_admin_id}",
        json={"is_active": False},
        headers=second_admin_headers,
    )
    assert deactivate_first.status_code == 200

    # Now only one active admin remains — deactivating them must be rejected.
    response = await client.patch(
        f"/api/v1/recruiter/users/{second_admin['id']}",
        json={"is_active": False},
        headers=second_admin_headers,
    )
    assert response.status_code == 409


async def test_role_change_is_recorded_as_an_activity(client: AsyncClient, super_admin: User) -> None:
    org = await _bootstrap_org(client, "team-role-change")
    headers = await _org_admin_headers(client, org)
    recruiter = await _create_recruiter(client, headers, "team-role-change")

    response = await client.patch(
        f"/api/v1/recruiter/users/{recruiter['id']}",
        json={"role": "HIRING_MANAGER"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["roles"] == ["HIRING_MANAGER"]

    activities = await client.get("/api/v1/recruiter/activities", headers=headers)
    role_changes = [a for a in activities.json() if a["action"] == "USER_ROLE_CHANGED"]
    assert len(role_changes) == 1
    assert role_changes[0]["entity_id"] == recruiter["id"]


async def test_cannot_change_role_of_the_last_org_admin(client: AsyncClient, super_admin: User) -> None:
    org = await _bootstrap_org(client, "team-last-admin-role")
    headers = await _org_admin_headers(client, org)
    admin_id = await _admin_user_id(client, headers)

    response = await client.patch(
        f"/api/v1/recruiter/users/{admin_id}",
        json={"role": "RECRUITER"},
        headers=headers,
    )
    assert response.status_code == 409


@pytest.mark.parametrize("role", ["HIRING_MANAGER", "INTERVIEWER"])
async def test_restricted_roles_cannot_manage_team_members(
    client: AsyncClient, super_admin: User, role: str
) -> None:
    """HR (RECRUITER) manages the team (tests/test_hr_team_management.py);
    the other non-admin roles still cannot."""
    slug = f"team-{role.lower().replace('_', '-')}-forbidden"
    org = await _bootstrap_org(client, slug)
    admin_headers = await _org_admin_headers(client, org)
    recruiter = await _create_recruiter(client, admin_headers, slug)
    restricted = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": f"restricted@{slug}.dev",
            "full_name": "Restricted User",
            "password": "RestrictedPass1",
            "role": role,
        },
        headers=admin_headers,
    )
    assert restricted.status_code == 201, restricted.text
    tokens = await login(client, email=f"restricted@{slug}.dev", password="RestrictedPass1")
    restricted_headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    listed = await client.get("/api/v1/recruiter/users", headers=restricted_headers)
    assert listed.status_code == 403
    response = await client.patch(
        f"/api/v1/recruiter/users/{recruiter['id']}",
        json={"is_active": False},
        headers=restricted_headers,
    )
    assert response.status_code == 403
    created = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": f"another@{slug}.dev",
            "full_name": "Another",
            "password": "AnotherPass12",
            "role": "RECRUITER",
        },
        headers=restricted_headers,
    )
    assert created.status_code == 403


async def test_activities_are_tenant_isolated(client: AsyncClient, super_admin: User) -> None:
    org_a = await _bootstrap_org(client, "activities-tenant-a")
    org_b = await _bootstrap_org(client, "activities-tenant-b")
    headers_a = await _org_admin_headers(client, org_a)
    headers_b = await _org_admin_headers(client, org_b)

    await _create_recruiter(client, headers_a, "activities-tenant-a")

    activities_b = await client.get("/api/v1/recruiter/activities", headers=headers_b)
    assert activities_b.status_code == 200
    org_a_entries = [a for a in activities_b.json() if a["action"] == "USER_CREATED"]
    assert org_a_entries == []
