"""Recruiter-facing Job CRUD (Phase 3) — creation, listing, tenant
isolation, and permission enforcement."""

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


_JOB_PAYLOAD = {
    "title": "Senior Backend Engineer",
    "department": "Engineering",
    "location": "Remote",
    "employment_type": "FULL_TIME",
    "description": "Own the recruitment platform's backend.",
    "openings_count": 2,
}


async def test_org_admin_can_create_and_list_a_job(client: AsyncClient, super_admin: User) -> None:
    org = await _bootstrap_org(client, "jobs-happy-path")
    headers = await _org_admin_headers(client, org)

    create_response = await client.post(
        "/api/v1/recruiter/jobs", json=_JOB_PAYLOAD, headers=headers
    )
    assert create_response.status_code == 201, create_response.text
    body = create_response.json()
    assert body["title"] == _JOB_PAYLOAD["title"]
    assert body["status"] == "DRAFT"
    assert body["organization_id"] is not None

    list_response = await client.get("/api/v1/recruiter/jobs", headers=headers)
    assert list_response.status_code == 200
    titles = [job["title"] for job in list_response.json()]
    assert _JOB_PAYLOAD["title"] in titles


async def test_job_update_applies_only_provided_fields(
    client: AsyncClient, super_admin: User
) -> None:
    org = await _bootstrap_org(client, "jobs-update")
    headers = await _org_admin_headers(client, org)

    created = (
        await client.post("/api/v1/recruiter/jobs", json=_JOB_PAYLOAD, headers=headers)
    ).json()

    patch_response = await client.patch(
        f"/api/v1/recruiter/jobs/{created['id']}",
        json={"status": "OPEN"},
        headers=headers,
    )
    assert patch_response.status_code == 200
    body = patch_response.json()
    assert body["status"] == "OPEN"
    assert body["title"] == _JOB_PAYLOAD["title"]  # untouched


async def test_job_created_visible_by_default_and_can_be_hidden(
    client: AsyncClient, super_admin: User
) -> None:
    org = await _bootstrap_org(client, "jobs-description-visibility")
    headers = await _org_admin_headers(client, org)

    created = (
        await client.post("/api/v1/recruiter/jobs", json=_JOB_PAYLOAD, headers=headers)
    ).json()
    assert created["description_visible"] is True

    hidden = await client.patch(
        f"/api/v1/recruiter/jobs/{created['id']}",
        json={"description_visible": False},
        headers=headers,
    )
    assert hidden.status_code == 200
    body = hidden.json()
    assert body["description_visible"] is False
    # Hiding is UI-visibility only — the text itself must survive.
    assert body["description"] == _JOB_PAYLOAD["description"]


async def test_org_a_cannot_see_or_fetch_org_bs_job(client: AsyncClient, super_admin: User) -> None:
    org_a = await _bootstrap_org(client, "jobs-tenant-a")
    org_b = await _bootstrap_org(client, "jobs-tenant-b")

    headers_b = await _org_admin_headers(client, org_b)
    created_in_b = (
        await client.post("/api/v1/recruiter/jobs", json=_JOB_PAYLOAD, headers=headers_b)
    ).json()

    headers_a = await _org_admin_headers(client, org_a)
    list_response = await client.get("/api/v1/recruiter/jobs", headers=headers_a)
    assert created_in_b["id"] not in [job["id"] for job in list_response.json()]

    get_response = await client.get(
        f"/api/v1/recruiter/jobs/{created_in_b['id']}", headers=headers_a
    )
    assert get_response.status_code == 404


async def test_interviewer_cannot_create_a_job(client: AsyncClient, super_admin: User) -> None:
    org = await _bootstrap_org(client, "jobs-no-permission")
    admin_headers = await _org_admin_headers(client, org)

    await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": "interviewer@jobs-no-permission.dev",
            "password": "SomePassword1",
            "full_name": "Ivy Interviewer",
            "role": "INTERVIEWER",
        },
        headers=admin_headers,
    )
    interviewer_tokens = await login(
        client, email="interviewer@jobs-no-permission.dev", password="SomePassword1"
    )

    response = await client.post(
        "/api/v1/recruiter/jobs",
        json=_JOB_PAYLOAD,
        headers={"Authorization": f"Bearer {interviewer_tokens['access_token']}"},
    )
    assert response.status_code == 403


async def test_create_job_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/api/v1/recruiter/jobs", json=_JOB_PAYLOAD)
    assert response.status_code == 401


async def test_delete_job_soft_deletes_and_records_an_activity(
    client: AsyncClient, super_admin: User
) -> None:
    org = await _bootstrap_org(client, "jobs-delete-happy")
    headers = await _org_admin_headers(client, org)

    created = (
        await client.post("/api/v1/recruiter/jobs", json=_JOB_PAYLOAD, headers=headers)
    ).json()

    delete_response = await client.post(
        f"/api/v1/recruiter/jobs/{created['id']}/delete",
        json={"reason": "Requisition cancelled."},
        headers=headers,
    )
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["deleted_at"] is not None

    # Gone from the active list, but every application against it (if any)
    # would remain intact — this job has none, so we only check the list.
    listing = await client.get("/api/v1/recruiter/jobs", headers=headers)
    assert created["id"] not in [job["id"] for job in listing.json()]

    second_delete = await client.post(
        f"/api/v1/recruiter/jobs/{created['id']}/delete",
        json={"reason": "Again."},
        headers=headers,
    )
    assert second_delete.status_code == 409

    activities = await client.get("/api/v1/recruiter/activities", headers=headers)
    delete_entries = [a for a in activities.json() if a["action"] == "JOB_DELETED"]
    assert len(delete_entries) == 1
    assert delete_entries[0]["entity_id"] == created["id"]
    assert delete_entries[0]["reason"] == "Requisition cancelled."


async def test_delete_job_requires_a_reason(client: AsyncClient, super_admin: User) -> None:
    org = await _bootstrap_org(client, "jobs-delete-reason")
    headers = await _org_admin_headers(client, org)

    created = (
        await client.post("/api/v1/recruiter/jobs", json=_JOB_PAYLOAD, headers=headers)
    ).json()

    response = await client.post(
        f"/api/v1/recruiter/jobs/{created['id']}/delete", json={"reason": ""}, headers=headers
    )
    assert response.status_code == 422


async def test_job_status_change_is_recorded_as_an_activity(
    client: AsyncClient, super_admin: User
) -> None:
    org = await _bootstrap_org(client, "jobs-status-activity")
    headers = await _org_admin_headers(client, org)

    created = (
        await client.post("/api/v1/recruiter/jobs", json=_JOB_PAYLOAD, headers=headers)
    ).json()
    await client.patch(
        f"/api/v1/recruiter/jobs/{created['id']}", json={"status": "OPEN"}, headers=headers
    )
    await client.patch(
        f"/api/v1/recruiter/jobs/{created['id']}", json={"status": "CLOSED"}, headers=headers
    )

    activities = await client.get("/api/v1/recruiter/activities", headers=headers)
    actions = [a["action"] for a in activities.json()]
    assert "JOB_CREATED" in actions
    assert "JOB_CLOSED" in actions
