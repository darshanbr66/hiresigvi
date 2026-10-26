"""Anonymous career-site + apply flow (Phase 3.5): browsing OPEN jobs by
organization slug and submitting an application with a resume, with no
authentication. Covers the RLS bootstrap path (app/services/
organization_service.py::get_organization_by_slug) and the actual resume
upload/storage/download round trip — not a mocked file.
"""

from httpx import AsyncClient

from app.models.user import User
from tests.conftest import SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD, login
from tests.public_apply import apply_publicly, request_code, verify_email

_JOB_PAYLOAD = {
    "title": "Senior Backend Engineer",
    "department": "Engineering",
    "location": "Remote",
    "employment_type": "FULL_TIME",
    "description": "Own the recruitment platform's backend.",
    "openings_count": 2,
}


async def _bootstrap_org_with_open_job(client: AsyncClient, slug: str) -> dict:
    super_admin_tokens = await login(client, email=SUPER_ADMIN_EMAIL, password=SUPER_ADMIN_PASSWORD)
    org_payload = {
        "name": "Acme Corp",
        "slug": slug,
        "admin_email": f"admin@{slug}.dev",
        "admin_password": "AcmeAdminPass1",
        "admin_full_name": "Acme Admin",
    }
    org_response = await client.post(
        "/api/v1/admin/organizations",
        json=org_payload,
        headers={"Authorization": f"Bearer {super_admin_tokens['access_token']}"},
    )
    assert org_response.status_code == 201, org_response.text

    admin_tokens = await login(
        client, email=org_payload["admin_email"], password=org_payload["admin_password"]
    )
    admin_headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

    job_response = await client.post(
        "/api/v1/recruiter/jobs", json=_JOB_PAYLOAD, headers=admin_headers
    )
    assert job_response.status_code == 201, job_response.text
    job = job_response.json()

    publish_response = await client.patch(
        f"/api/v1/recruiter/jobs/{job['id']}", json={"status": "OPEN"}, headers=admin_headers
    )
    assert publish_response.status_code == 200

    return {"slug": slug, "job_id": job["id"], "admin_headers": admin_headers}


_PDF_CONTENT = b"%PDF-1.4 fake resume content"


def _resume_file(name: str = "resume.pdf", content: bytes = _PDF_CONTENT) -> dict:
    return {"resume": (name, content, "application/pdf")}


async def test_public_listing_only_shows_open_jobs(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "public-jobs-open")

    draft_response = await client.post(
        "/api/v1/recruiter/jobs",
        json={**_JOB_PAYLOAD, "title": "Draft Role"},
        headers=ctx["admin_headers"],
    )
    assert draft_response.status_code == 201

    listing = await client.get(f"/api/v1/public/organizations/{ctx['slug']}/jobs")
    assert listing.status_code == 200
    titles = [job["title"] for job in listing.json()]
    assert _JOB_PAYLOAD["title"] in titles
    assert "Draft Role" not in titles


async def test_public_job_detail_omits_description_when_hidden(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "public-jobs-hidden-jd")

    visible = await client.get(
        f"/api/v1/public/organizations/{ctx['slug']}/jobs/{ctx['job_id']}"
    )
    assert visible.status_code == 200
    assert visible.json()["description"] == _JOB_PAYLOAD["description"]

    hide_response = await client.patch(
        f"/api/v1/recruiter/jobs/{ctx['job_id']}",
        json={"description_visible": False},
        headers=ctx["admin_headers"],
    )
    assert hide_response.status_code == 200

    hidden = await client.get(
        f"/api/v1/public/organizations/{ctx['slug']}/jobs/{ctx['job_id']}"
    )
    assert hidden.status_code == 200
    assert hidden.json()["description"] is None
    # Every other field stays intact — only the JD text is withheld.
    assert hidden.json()["title"] == _JOB_PAYLOAD["title"]


async def test_unknown_organization_slug_is_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/public/organizations/does-not-exist/jobs")
    assert response.status_code == 404


async def test_apply_creates_candidate_application_and_resume(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "public-apply-happy")

    response = await apply_publicly(
        client,
        ctx["slug"],
        ctx["job_id"],
        email="jane@example.com",
        resume=("resume.pdf", _PDF_CONTENT, "application/pdf"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job_title"] == _JOB_PAYLOAD["title"]
    assert body["candidate_email"] == "jane@example.com"
    # No AI provider in tests -> screening can't run -> the application is
    # never screened out; it waits for a recruiter.
    # Neither the screening outcome nor internal state reaches the candidate.
    assert "outcome" not in body
    assert "status" not in body

    applications = await client.get(
        "/api/v1/recruiter/applications", headers=ctx["admin_headers"]
    )
    application = next(
        a for a in applications.json() if a["candidate_full_name"] == "Jane Candidate"
    )
    assert application["source"] == "PORTAL"
    assert application["status"] == "APPLIED"
    assert application["resume_filename"] == "resume.pdf"

    download = await client.get(
        f"/api/v1/recruiter/applications/{application['id']}/resume",
        headers=ctx["admin_headers"],
    )
    assert download.status_code == 200
    assert download.content == _PDF_CONTENT
    assert download.headers["content-type"] == "application/pdf"


async def test_second_applicant_to_the_same_org_succeeds(
    client: AsyncClient, super_admin: User
) -> None:
    """Regression test: LocalResumeStorage.save() must reuse the org's
    upload directory across applications, not fail because it already
    exists from a prior applicant (see app/integrations/storage/local.py —
    `mkdir` needs `exist_ok=True`)."""
    ctx = await _bootstrap_org_with_open_job(client, "public-apply-second")

    first = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="jane@example.com")
    assert first.status_code == 201, first.text

    second = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="john@example.com", full_name="John Candidate"
    )
    assert second.status_code == 201, second.text


async def test_duplicate_application_is_rejected(
    client: AsyncClient, super_admin: User, otp_settings
) -> None:
    otp_settings(email_otp_resend_cooldown_seconds=0)
    ctx = await _bootstrap_org_with_open_job(client, "public-apply-dup")

    first = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="jane@example.com")
    assert first.status_code == 201

    # The same person doesn't even get a verification code a second time.
    # They applied moments ago and have no reapply permission
    # (app/services/reapply_service.py). They haven't proven they own the
    # address, so nothing but "already applied" comes back — no dates.
    code_response, code = await request_code(client, ctx["slug"], "jane@example.com")
    assert code_response.status_code == 409
    error = code_response.json()["error"]
    assert error["code"] == "already_applied"
    assert error["message"] == "You have already applied."
    assert "data" not in error or not error["data"]
    assert code is None


async def test_apply_with_disallowed_file_type_is_rejected(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "public-apply-bad-file")
    # A rejected file never spends the verification token, so one token
    # serves every attempt below.
    token = await verify_email(client, ctx["slug"], "jane@example.com")

    for name, content in (
        ("resume.exe", b"MZ fake binary"),
        ("resume.docx", b"PK fake docx"),
        # Right extension, wrong bytes: a renamed file is not a PDF.
        ("resume.pdf", b"MZ not really a pdf"),
    ):
        response = await apply_publicly(
            client,
            ctx["slug"],
            ctx["job_id"],
            email="jane@example.com",
            token=token,
            resume=(name, content, "application/pdf"),
        )
        assert response.status_code == 400, (name, response.text)
        assert response.json()["error"]["code"] == "invalid_file_type"


async def test_apply_to_a_non_open_job_is_404(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "public-apply-closed")
    token = await verify_email(client, ctx["slug"], "jane@example.com")
    await client.patch(
        f"/api/v1/recruiter/jobs/{ctx['job_id']}",
        json={"status": "CLOSED"},
        headers=ctx["admin_headers"],
    )

    response = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="jane@example.com", token=token
    )
    assert response.status_code == 404


async def test_recruiter_cannot_download_another_orgs_resume(
    client: AsyncClient, super_admin: User
) -> None:
    ctx_a = await _bootstrap_org_with_open_job(client, "public-apply-tenant-a")
    ctx_b = await _bootstrap_org_with_open_job(client, "public-apply-tenant-b")

    await apply_publicly(client, ctx_a["slug"], ctx_a["job_id"], email="jane@example.com")
    applications = await client.get(
        "/api/v1/recruiter/applications", headers=ctx_a["admin_headers"]
    )
    application_id = applications.json()[0]["id"]

    cross_tenant_download = await client.get(
        f"/api/v1/recruiter/applications/{application_id}/resume",
        headers=ctx_b["admin_headers"],
    )
    assert cross_tenant_download.status_code == 404
