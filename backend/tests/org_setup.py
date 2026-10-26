"""Shared setup for the institution / question-bank / program tests: a real
organization created through the admin API, its ORG_ADMIN's headers, and
staff users with a given role — no shortcuts into the database."""

from httpx import AsyncClient

from tests.conftest import SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD, login


async def bootstrap_org(client: AsyncClient, slug: str, *, name: str = "Acme Corp") -> dict:
    super_admin = await login(client, email=SUPER_ADMIN_EMAIL, password=SUPER_ADMIN_PASSWORD)
    payload = {
        "name": name,
        "slug": slug,
        "admin_email": f"admin@{slug}.dev",
        "admin_password": "AcmeAdminPass1",
        "admin_full_name": "Acme Admin",
    }
    created = await client.post(
        "/api/v1/admin/organizations",
        json=payload,
        headers={"Authorization": f"Bearer {super_admin['access_token']}"},
    )
    assert created.status_code == 201, created.text
    tokens = await login(client, email=payload["admin_email"], password=payload["admin_password"])
    return {"slug": slug, "headers": {"Authorization": f"Bearer {tokens['access_token']}"}}


async def staff_headers(client: AsyncClient, ctx: dict, role: str) -> dict:
    email = f"{role.lower()}@{ctx['slug']}.dev"
    response = await client.post(
        "/api/v1/recruiter/users",
        json={"email": email, "full_name": f"{role.title()} User", "password": "StaffUserPass1", "role": role},
        headers=ctx["headers"],
    )
    assert response.status_code == 201, response.text
    tokens = await login(client, email=email, password="StaffUserPass1")
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def open_job(client: AsyncClient, ctx: dict, title: str = "Graduate Engineer") -> dict:
    job = (
        await client.post(
            "/api/v1/recruiter/jobs",
            json={"title": title, "description": "Entry-level engineering role."},
            headers=ctx["headers"],
        )
    ).json()
    opened = await client.patch(
        f"/api/v1/recruiter/jobs/{job['id']}", json={"status": "OPEN"}, headers=ctx["headers"]
    )
    assert opened.status_code == 200, opened.text
    return job


def link_token(link: str) -> str:
    return link.rstrip("/").rsplit("/", 1)[-1]
