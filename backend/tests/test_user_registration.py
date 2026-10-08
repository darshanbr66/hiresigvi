"""ORG_ADMIN creates users within their own tenant via
POST /api/v1/recruiter/users. organization_id is always derived from the
caller, never accepted from the client (docs/security.md § 2).
"""

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


async def test_org_admin_can_create_recruiter_user(client: AsyncClient, super_admin: User) -> None:
    org = await _bootstrap_org(client, "reg-happy-path")
    headers = await _org_admin_headers(client, org)

    response = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": "recruiter@reg-happy-path.dev",
            "password": "RecruiterPass1",
            "full_name": "Riley Recruiter",
            "role": "RECRUITER",
        },
        headers=headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "recruiter@reg-happy-path.dev"
    assert body["roles"] == ["RECRUITER"]
    assert body["organization_id"] is not None


async def test_duplicate_email_in_same_org_is_rejected(
    client: AsyncClient, super_admin: User
) -> None:
    org = await _bootstrap_org(client, "reg-duplicate")
    headers = await _org_admin_headers(client, org)
    payload = {
        "email": "dup@reg-duplicate.dev",
        "password": "SomePassword1",
        "full_name": "Dup User",
        "role": "RECRUITER",
    }

    first = await client.post("/api/v1/recruiter/users", json=payload, headers=headers)
    assert first.status_code == 201

    second = await client.post("/api/v1/recruiter/users", json=payload, headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


async def test_same_email_is_allowed_in_a_different_organization(
    client: AsyncClient, super_admin: User
) -> None:
    """Confirms the (organization_id, email) uniqueness scoping is per
    tenant, not global — a deliberate schema decision (docs/database.md
    § 3.1), not a bug.
    """
    org_a = await _bootstrap_org(client, "reg-cross-a")
    org_b = await _bootstrap_org(client, "reg-cross-b")

    shared_email_payload = {
        "email": "shared@example.dev",
        "password": "SomePassword1",
        "full_name": "Shared Email User",
        "role": "RECRUITER",
    }

    response_a = await client.post(
        "/api/v1/recruiter/users",
        json=shared_email_payload,
        headers=await _org_admin_headers(client, org_a),
    )
    response_b = await client.post(
        "/api/v1/recruiter/users",
        json=shared_email_payload,
        headers=await _org_admin_headers(client, org_b),
    )

    assert response_a.status_code == 201
    assert response_b.status_code == 201


async def test_invalid_email_is_rejected(client: AsyncClient, super_admin: User) -> None:
    org = await _bootstrap_org(client, "reg-bad-email")
    headers = await _org_admin_headers(client, org)

    response = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": "not-an-email",
            "password": "SomePassword1",
            "full_name": "Bad Email",
            "role": "RECRUITER",
        },
        headers=headers,
    )
    assert response.status_code == 422


async def test_weak_password_is_rejected(client: AsyncClient, super_admin: User) -> None:
    org = await _bootstrap_org(client, "reg-weak-password")
    headers = await _org_admin_headers(client, org)

    response = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": "weak@reg-weak-password.dev",
            "password": "short",
            "full_name": "Weak Password",
            "role": "RECRUITER",
        },
        headers=headers,
    )
    assert response.status_code == 422


async def test_super_admin_role_is_not_assignable_via_tenant_endpoint(
    client: AsyncClient, super_admin: User
) -> None:
    org = await _bootstrap_org(client, "reg-no-super-admin")
    headers = await _org_admin_headers(client, org)

    response = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": "wannabe@reg-no-super-admin.dev",
            "password": "SomePassword1",
            "full_name": "Wannabe Admin",
            "role": "SUPER_ADMIN",
        },
        headers=headers,
    )
    assert response.status_code == 422


async def test_hiring_manager_role_cannot_create_users(
    client: AsyncClient, super_admin: User
) -> None:
    """Only ORG_ADMIN and HR (RECRUITER, migration d7e8f9a0b1c2) hold
    `user.create`; HIRING_MANAGER does not.
    """
    org = await _bootstrap_org(client, "reg-no-permission")
    admin_headers = await _org_admin_headers(client, org)

    create_recruiter = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": "plain-recruiter@reg-no-permission.dev",
            "password": "RecruiterPass1",
            "full_name": "Plain Recruiter",
            "role": "HIRING_MANAGER",
        },
        headers=admin_headers,
    )
    assert create_recruiter.status_code == 201

    recruiter_tokens = await login(
        client, email="plain-recruiter@reg-no-permission.dev", password="RecruiterPass1"
    )

    response = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": "another@reg-no-permission.dev",
            "password": "SomePassword1",
            "full_name": "Another User",
            "role": "RECRUITER",
        },
        headers={"Authorization": f"Bearer {recruiter_tokens['access_token']}"},
    )
    assert response.status_code == 403


async def test_create_user_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": "nobody@nowhere.dev",
            "password": "SomePassword1",
            "full_name": "Nobody",
            "role": "RECRUITER",
        },
    )
    assert response.status_code == 401
