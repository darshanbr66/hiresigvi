"""Campus drives: recruiter management (including inline "new job"
creation), the DRAFT/ACTIVE/PAUSED/CLOSED lifecycle, and the dedicated
public application link (docs/campus-hiring.md)."""

from httpx import AsyncClient

from app.models.user import User
from tests.conftest import SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD, login
from tests.public_apply import apply_to_campus_drive


async def _bootstrap_org_with_job(client: AsyncClient, slug: str) -> dict:
    super_admin_tokens = await login(client, email=SUPER_ADMIN_EMAIL, password=SUPER_ADMIN_PASSWORD)
    org_payload = {
        "name": "Acme Corp",
        "slug": slug,
        "admin_email": f"admin@{slug}.dev",
        "admin_password": "AcmeAdminPass1",
        "admin_full_name": "Acme Admin",
    }
    await client.post(
        "/api/v1/admin/organizations",
        json=org_payload,
        headers={"Authorization": f"Bearer {super_admin_tokens['access_token']}"},
    )
    admin_tokens = await login(
        client, email=org_payload["admin_email"], password=org_payload["admin_password"]
    )
    headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

    job = (
        await client.post(
            "/api/v1/recruiter/jobs",
            json={"title": "Graduate Engineer", "description": "Entry-level role."},
            headers=headers,
        )
    ).json()
    await client.patch(
        f"/api/v1/recruiter/jobs/{job['id']}", json={"status": "OPEN"}, headers=headers
    )

    return {"headers": headers, "job": job, "slug": slug}


async def _create_drive(client: AsyncClient, ctx: dict, **overrides) -> dict:
    payload = {
        "name": "Fall 2026 Drive",
        "job_id": ctx["job"]["id"],
        "college_name": "MIT",
        **overrides,
    }
    response = await client.post(
        "/api/v1/recruiter/campus-drives", json=payload, headers=ctx["headers"]
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_drive_with_existing_job(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-existing-job")
    drive = await _create_drive(client, ctx)

    assert drive["status"] == "DRAFT"
    assert drive["job_title"] == "Graduate Engineer"
    assert drive["application_link"] is not None
    assert "/campus-drive/" in drive["application_link"]

    listing = await client.get("/api/v1/recruiter/campus-drives", headers=ctx["headers"])
    assert len(listing.json()) == 1
    # the list endpoint never re-exposes the one-time link
    assert listing.json()[0]["application_link"] is None


async def test_create_drive_with_inline_new_job(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-new-job")
    response = await client.post(
        "/api/v1/recruiter/campus-drives",
        json={
            "name": "Spring Drive",
            "college_name": "Stanford",
            "new_job_title": "Campus Hire — Backend",
            "new_job_description": "For students graduating this year.",
        },
        headers=ctx["headers"],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job_title"] == "Campus Hire — Backend"

    jobs = (await client.get("/api/v1/recruiter/jobs", headers=ctx["headers"])).json()
    assert any(j["title"] == "Campus Hire — Backend" for j in jobs)


async def test_create_drive_rejects_both_or_neither_job_source(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-bad-job-source")

    neither = await client.post(
        "/api/v1/recruiter/campus-drives",
        json={"name": "Drive", "college_name": "MIT"},
        headers=ctx["headers"],
    )
    assert neither.status_code == 422

    both = await client.post(
        "/api/v1/recruiter/campus-drives",
        json={
            "name": "Drive",
            "college_name": "MIT",
            "job_id": ctx["job"]["id"],
            "new_job_title": "Other Role",
            "new_job_description": "...",
        },
        headers=ctx["headers"],
    )
    assert both.status_code == 422


async def test_drive_status_lifecycle(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-lifecycle")
    drive = await _create_drive(client, ctx)

    activate = await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["headers"],
    )
    assert activate.status_code == 200
    assert activate.json()["status"] == "ACTIVE"

    pause = await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "PAUSED"},
        headers=ctx["headers"],
    )
    assert pause.status_code == 200

    resume = await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["headers"],
    )
    assert resume.status_code == 200

    close = await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "CLOSED"},
        headers=ctx["headers"],
    )
    assert close.status_code == 200

    reopen = await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["headers"],
    )
    assert reopen.status_code == 200
    assert reopen.json()["status"] == "ACTIVE"


async def test_illegal_status_transition_is_rejected(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-illegal-transition")
    drive = await _create_drive(client, ctx)  # DRAFT

    response = await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "PAUSED"},
        headers=ctx["headers"],
    )
    assert response.status_code == 409


def _extract_token(link: str) -> str:
    return link.rsplit("/", 1)[-1]


async def test_public_link_rejects_draft_drive(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-draft-link")
    drive = await _create_drive(client, ctx)
    token = _extract_token(drive["application_link"])

    response = await client.get(f"/api/v1/public/campus-drive/{token}")
    assert response.status_code == 404


async def test_public_candidate_can_view_and_apply_to_active_drive(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-apply-flow")
    drive = await _create_drive(client, ctx)
    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["headers"],
    )
    token = _extract_token(drive["application_link"])

    view = await client.get(f"/api/v1/public/campus-drive/{token}")
    assert view.status_code == 200
    view_body = view.json()
    assert view_body["job_title"] == "Graduate Engineer"
    assert view_body["status"] == "ACTIVE"
    assert view_body["has_assessment"] is False

    apply_response = await apply_to_campus_drive(client, token, email="priya@example.com")
    assert apply_response.status_code == 201, apply_response.text
    body = apply_response.json()
    assert body["job_title"] == "Graduate Engineer"
    assert body["assessment_invitation_link"] is None
    # Neither the screening outcome nor internal state reaches the candidate.
    assert "outcome" not in body
    assert "status" not in body

    applications = (
        await client.get(
            f"/api/v1/recruiter/applications?campus_drive_id={drive['id']}", headers=ctx["headers"]
        )
    ).json()
    assert len(applications) == 1
    assert applications[0]["candidate_full_name"] == "Priya Candidate"
    assert applications[0]["source"] == "CAMPUS_IMPORT"


async def test_apply_to_paused_drive_is_rejected(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-paused")
    drive = await _create_drive(client, ctx)
    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["headers"],
    )
    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "PAUSED"},
        headers=ctx["headers"],
    )
    token = _extract_token(drive["application_link"])

    response = await apply_to_campus_drive(
        client, token, email="priya@example.com", token="never-issued"
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "drive_not_active"
    code_request = await client.post(
        f"/api/v1/public/campus-drive/{token}/email-verification/request",
        json={"email": "priya@example.com"},
    )
    assert code_request.status_code == 400
    assert code_request.json()["error"]["code"] == "drive_not_active"


async def test_public_view_of_paused_drive_still_shows_details(
    client: AsyncClient, super_admin: User
) -> None:
    """PAUSED is different from CLOSED/DELETED — the drive still exists and
    can be reactivated, so its details stay visible; only applying is
    blocked (SIGVITAS platform overhaul § 16)."""
    ctx = await _bootstrap_org_with_job(client, "campus-paused-view")
    drive = await _create_drive(client, ctx)
    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["headers"],
    )
    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "PAUSED"},
        headers=ctx["headers"],
    )
    token = _extract_token(drive["application_link"])

    view = await client.get(f"/api/v1/public/campus-drive/{token}")
    assert view.status_code == 200
    body = view.json()
    assert body["kind"] == "drive"
    assert body["status"] == "PAUSED"
    assert body["job_title"] == "Graduate Engineer"


async def test_public_view_of_closed_drive_hides_all_recruitment_content(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-closed-view")
    drive = await _create_drive(client, ctx)
    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["headers"],
    )
    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "CLOSED"},
        headers=ctx["headers"],
    )
    token = _extract_token(drive["application_link"])

    view = await client.get(f"/api/v1/public/campus-drive/{token}")
    assert view.status_code == 200
    body = view.json()
    assert body["kind"] == "unavailable"
    assert "job_title" not in body
    assert "job_description" not in body
    assert "college_name" not in body
    assert "has_assessment" not in body

    apply_response = await apply_to_campus_drive(
        client, token, email="priya-closed@example.com", token="never-issued"
    )
    assert apply_response.status_code == 400
    assert apply_response.json()["error"]["code"] == "drive_not_active"


async def test_public_link_of_deleted_drive_is_404(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-deleted-view")
    drive = await _create_drive(client, ctx)
    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["headers"],
    )
    token = _extract_token(drive["application_link"])

    delete_response = await client.post(
        f"/api/v1/recruiter/campus-drives/{drive['id']}/delete",
        json={"reason": "Duplicate drive"},
        headers=ctx["headers"],
    )
    assert delete_response.status_code == 200, delete_response.text

    view = await client.get(f"/api/v1/public/campus-drive/{token}")
    assert view.status_code == 404


async def test_apply_with_default_assessment_auto_invites(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-auto-assessment")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments",
            json={
                "title": "Aptitude",
                "instructions": "Answer all.",
                "duration_minutes": 20,
                "pass_score": 50,
                "questions": [
                    {
                        "prompt": "2 + 2 = ?",
                        "type": "MCQ_SINGLE",
                        "points": 1,
                        "options": [
                            {"label": "3", "is_correct": False},
                            {"label": "4", "is_correct": True},
                        ],
                    }
                ],
            },
            headers=ctx["headers"],
        )
    ).json()

    drive = await _create_drive(client, ctx, default_assessment_id=assessment["id"])
    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["headers"],
    )
    token = _extract_token(drive["application_link"])

    apply_response = await apply_to_campus_drive(client, token, email="priya@example.com")
    assert apply_response.status_code == 201, apply_response.text
    body = apply_response.json()
    assert body["assessment_invitation_link"] is not None
    (application,) = (
        await client.get(
            f"/api/v1/recruiter/applications?campus_drive_id={drive['id']}", headers=ctx["headers"]
        )
    ).json()
    assert application["status"] == "ASSESSMENT_INVITED"


async def test_funnel_counts_reflect_real_applications(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-funnel")
    drive = await _create_drive(client, ctx)
    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["headers"],
    )
    token = _extract_token(drive["application_link"])

    empty_funnel = (
        await client.get(f"/api/v1/recruiter/campus-drives/{drive['id']}/funnel", headers=ctx["headers"])
    ).json()
    assert empty_funnel["registered"] == 0

    applied = await apply_to_campus_drive(client, token, email="priya@example.com")
    assert applied.status_code == 201, applied.text

    funnel = (
        await client.get(f"/api/v1/recruiter/campus-drives/{drive['id']}/funnel", headers=ctx["headers"])
    ).json()
    assert funnel["registered"] == 1


async def test_regenerate_link_produces_a_new_working_token(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-regen-link")
    drive = await _create_drive(client, ctx)
    old_token = _extract_token(drive["application_link"])

    regenerate = await client.post(
        f"/api/v1/recruiter/campus-drives/{drive['id']}/regenerate-link", headers=ctx["headers"]
    )
    assert regenerate.status_code == 200
    new_token = _extract_token(regenerate.json()["application_link"])
    assert new_token != old_token

    # activate so the view endpoint would otherwise succeed
    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["headers"],
    )
    old_link_response = await client.get(f"/api/v1/public/campus-drive/{old_token}")
    assert old_link_response.status_code == 404
    new_link_response = await client.get(f"/api/v1/public/campus-drive/{new_token}")
    assert new_link_response.status_code == 200


async def test_campus_drives_are_tenant_scoped(client: AsyncClient, super_admin: User) -> None:
    ctx_a = await _bootstrap_org_with_job(client, "campus-tenant-a")
    ctx_b = await _bootstrap_org_with_job(client, "campus-tenant-b")

    await _create_drive(client, ctx_a, name="Org A Drive")

    listing_b = await client.get("/api/v1/recruiter/campus-drives", headers=ctx_b["headers"])
    assert listing_b.json() == []


async def test_delete_campus_drive_soft_deletes_and_records_an_activity(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-delete-happy")
    drive = await _create_drive(client, ctx)

    delete_response = await client.post(
        f"/api/v1/recruiter/campus-drives/{drive['id']}/delete",
        json={"reason": "Drive cancelled."},
        headers=ctx["headers"],
    )
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["deleted_at"] is not None

    listing = await client.get("/api/v1/recruiter/campus-drives", headers=ctx["headers"])
    assert drive["id"] not in [d["id"] for d in listing.json()]

    second_delete = await client.post(
        f"/api/v1/recruiter/campus-drives/{drive['id']}/delete",
        json={"reason": "Again."},
        headers=ctx["headers"],
    )
    assert second_delete.status_code == 409

    activities = await client.get("/api/v1/recruiter/activities", headers=ctx["headers"])
    delete_entries = [a for a in activities.json() if a["action"] == "CAMPUS_DRIVE_DELETED"]
    assert len(delete_entries) == 1
    assert delete_entries[0]["entity_id"] == drive["id"]


async def test_campus_drive_lifecycle_changes_are_recorded_as_activities(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_job(client, "campus-lifecycle-activity")
    drive = await _create_drive(client, ctx)

    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["headers"],
    )
    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "PAUSED"},
        headers=ctx["headers"],
    )

    activities = await client.get("/api/v1/recruiter/activities", headers=ctx["headers"])
    actions = [a["action"] for a in activities.json()]
    assert "CAMPUS_DRIVE_CREATED" in actions
    assert "CAMPUS_DRIVE_ACTIVATED" in actions
    assert "CAMPUS_DRIVE_PAUSED" in actions
