"""Recruitment reports are computed from real, tenant-scoped data — never
hardcoded (CLAUDE.md § 2)."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import rls_bypass
from app.integrations.ai import AIProviderError
from app.models.screening import ScreeningRun
from app.models.user import User
from app.services import screening_service
from tests.conftest import SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD, login
from tests.public_apply import apply_publicly
from tests.test_candidate_intake import _StubScreeningProvider
from tests.test_public_applications import _bootstrap_org_with_open_job


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
    tokens = await login(client, email=payload["admin_email"], password=payload["admin_password"])
    return {"headers": {"Authorization": f"Bearer {tokens['access_token']}"}}


async def test_overview_reflects_real_data(client: AsyncClient, super_admin: User) -> None:
    org = await _bootstrap_org(client, "reports-overview")
    headers = org["headers"]

    empty = await client.get("/api/v1/recruiter/reports/overview", headers=headers)
    assert empty.status_code == 200
    assert empty.json() == {
        "total_jobs": 0,
        "open_jobs": 0,
        "total_candidates": 0,
        "total_applications": 0,
        "selected_candidates": 0,
        "rejected_candidates": 0,
        "hired_candidates": 0,
        "applications_by_status": [],
        "applications_by_job": [],
        "by_employment_type": [
            {"employment_type": "INTERNSHIP", "jobs": 0, "applications": 0},
            {"employment_type": "PART_TIME", "jobs": 0, "applications": 0},
            {"employment_type": "FULL_TIME", "jobs": 0, "applications": 0},
        ],
        "screening": {"total_runs": 0, "completed": 0, "failed": 0, "average_score": 0.0},
        "assessments": {"total_invitations": 0, "submitted": 0, "passed": 0},
        "campus_drives": [],
    }

    job = (
        await client.post(
            "/api/v1/recruiter/jobs",
            json={"title": "Backend Engineer", "description": "Build things."},
            headers=headers,
        )
    ).json()
    await client.patch(
        f"/api/v1/recruiter/jobs/{job['id']}", json={"status": "OPEN"}, headers=headers
    )

    candidate = (
        await client.post(
            "/api/v1/recruiter/candidates",
            json={"email": "c1@example.com", "full_name": "Cara Candidate"},
            headers=headers,
        )
    ).json()

    await client.post(
        "/api/v1/recruiter/applications",
        json={"candidate_id": candidate["id"], "job_id": job["id"]},
        headers=headers,
    )

    report = (
        await client.get("/api/v1/recruiter/reports/overview", headers=headers)
    ).json()
    assert report["total_jobs"] == 1
    assert report["open_jobs"] == 1
    assert report["total_candidates"] == 1
    assert report["total_applications"] == 1
    assert report["applications_by_status"] == [{"status": "APPLIED", "count": 1}]
    assert report["applications_by_job"] == [
        {"job_id": job["id"], "job_title": "Backend Engineer", "employment_type": "FULL_TIME", "count": 1}
    ]
    assert report["by_employment_type"] == [
        {"employment_type": "INTERNSHIP", "jobs": 0, "applications": 0},
        {"employment_type": "PART_TIME", "jobs": 0, "applications": 0},
        {"employment_type": "FULL_TIME", "jobs": 1, "applications": 1},
    ]


async def test_overview_includes_campus_drive_breakdown(client: AsyncClient, super_admin: User) -> None:
    org = await _bootstrap_org(client, "reports-campus")
    headers = org["headers"]

    job = (
        await client.post(
            "/api/v1/recruiter/jobs",
            json={"title": "Graduate Engineer", "description": "..."},
            headers=headers,
        )
    ).json()
    drive = (
        await client.post(
            "/api/v1/recruiter/campus-drives",
            json={"name": "Fall Drive", "job_id": job["id"], "college_name": "MIT"},
            headers=headers,
        )
    ).json()

    report = (await client.get("/api/v1/recruiter/reports/overview", headers=headers)).json()
    assert report["campus_drives"] == [
        {
            "drive_id": drive["id"],
            "drive_name": "Fall Drive",
            "institution_id": drive["institution_id"],
            "institution_name": "MIT",
            "application_count": 0,
        }
    ]


async def test_overview_outcome_metrics_exclude_soft_deleted_applications(
    client: AsyncClient, super_admin: User
) -> None:
    org = await _bootstrap_org(client, "reports-outcomes")
    headers = org["headers"]

    job = (
        await client.post(
            "/api/v1/recruiter/jobs",
            json={"title": "Backend Engineer", "description": "Build things."},
            headers=headers,
        )
    ).json()
    await client.patch(f"/api/v1/recruiter/jobs/{job['id']}", json={"status": "OPEN"}, headers=headers)

    async def _application(email: str) -> str:
        candidate = (
            await client.post(
                "/api/v1/recruiter/candidates",
                json={"email": email, "full_name": "Test Candidate"},
                headers=headers,
            )
        ).json()
        application = (
            await client.post(
                "/api/v1/recruiter/applications",
                json={"candidate_id": candidate["id"], "job_id": job["id"]},
                headers=headers,
            )
        ).json()
        return application["id"]

    async def _advance(application_id: str, *statuses: str) -> None:
        for status in statuses:
            response = await client.post(
                f"/api/v1/recruiter/applications/{application_id}/status",
                json={"to_status": status},
                headers=headers,
            )
            assert response.status_code == 200, response.text

    selected_id = await _application("selected@reports-outcomes.dev")
    await _advance(selected_id, "UNDER_REVIEW", "SCREENING", "SHORTLISTED", "INTERVIEW", "SELECTED")

    rejected_id = await _application("rejected@reports-outcomes.dev")
    await _advance(rejected_id, "UNDER_REVIEW", "REJECTED")

    hired_id = await _application("hired@reports-outcomes.dev")
    await _advance(hired_id, "UNDER_REVIEW", "SCREENING", "SHORTLISTED", "INTERVIEW", "SELECTED", "HIRED")

    # A rejected application that's then soft-deleted must not be counted.
    deleted_rejected_id = await _application("deleted-rejected@reports-outcomes.dev")
    await _advance(deleted_rejected_id, "UNDER_REVIEW", "REJECTED")
    delete_response = await client.post(
        f"/api/v1/recruiter/applications/{deleted_rejected_id}/delete",
        json={"reason": "Duplicate entry"},
        headers=headers,
    )
    assert delete_response.status_code == 200, delete_response.text

    report = (await client.get("/api/v1/recruiter/reports/overview", headers=headers)).json()
    assert report["selected_candidates"] == 1
    assert report["rejected_candidates"] == 1
    assert report["hired_candidates"] == 1


async def test_reports_are_tenant_scoped(client: AsyncClient, super_admin: User) -> None:
    org_a = await _bootstrap_org(client, "reports-tenant-a")
    org_b = await _bootstrap_org(client, "reports-tenant-b")

    await client.post(
        "/api/v1/recruiter/jobs",
        json={"title": "Org A Job", "description": "..."},
        headers=org_a["headers"],
    )

    report_b = (
        await client.get("/api/v1/recruiter/reports/overview", headers=org_b["headers"])
    ).json()
    assert report_b["total_jobs"] == 0


async def test_reports_require_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/v1/recruiter/reports/overview")
    assert response.status_code == 401


# --- AI screening metrics count only the current pipeline ----------------------------
#
# Deleting an application, candidate or job is a soft delete that keeps every
# linked ScreeningRun for the audit trail; the report must count runs only
# through the organization's current applications, never every run row the
# organization ever produced.

_EMPTY_SCREENING = {"total_runs": 0, "completed": 0, "failed": 0, "average_score": 0.0}


def _ai_verdict(monkeypatch: pytest.MonkeyPatch, decision: str) -> None:
    provider = _StubScreeningProvider(decision)
    monkeypatch.setattr(screening_service, "get_llm_provider", lambda: provider)


async def _report(client: AsyncClient, headers: dict) -> dict:
    response = await client.get("/api/v1/recruiter/reports/overview", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _delete(client: AsyncClient, headers: dict, path: str) -> None:
    response = await client.post(
        f"/api/v1/recruiter/{path}/delete", json={"reason": "Demo data clean-up"}, headers=headers
    )
    assert response.status_code == 200, response.text


async def test_screening_metrics_are_zero_once_all_recruitment_data_is_deleted(
    client: AsyncClient, super_admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production report: 0 jobs/candidates/applications, yet still
    "2 runs, average 15" from the deleted demo applications."""
    _ai_verdict(monkeypatch, "NOT_MATCH")
    ctx = await _bootstrap_org_with_open_job(client, "reports-stale-screening")
    headers = ctx["admin_headers"]
    for email in ("demo1@example.com", "demo2@example.com"):
        applied = await apply_publicly(client, ctx["slug"], ctx["job_id"], email=email)
        assert applied.status_code == 201, applied.text

    before = await _report(client, headers)
    assert before["screening"] == {
        "total_runs": 2, "completed": 2, "failed": 0, "average_score": 12.0,
    }

    for application in (
        await client.get("/api/v1/recruiter/applications", headers=headers)
    ).json():
        await _delete(client, headers, f"applications/{application['id']}")
    for candidate in (await client.get("/api/v1/recruiter/candidates", headers=headers)).json():
        await _delete(client, headers, f"candidates/{candidate['id']}")
    await _delete(client, headers, f"jobs/{ctx['job_id']}")

    after = await _report(client, headers)
    assert after["total_jobs"] == 0
    assert after["total_candidates"] == 0
    assert after["total_applications"] == 0
    assert after["screening"] == _EMPTY_SCREENING
    assert after["assessments"] == {"total_invitations": 0, "submitted": 0, "passed": 0}


async def test_deleted_self_service_application_leaves_no_stale_screening_metrics(
    client: AsyncClient, super_admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reports-deleted-application")
    headers = ctx["admin_headers"]

    _ai_verdict(monkeypatch, "MATCH")  # score 84 — stays
    kept = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="kept@example.com")
    assert kept.status_code == 201, kept.text
    _ai_verdict(monkeypatch, "NOT_MATCH")  # score 12 — deleted
    gone = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="gone@example.com")
    assert gone.status_code == 201, gone.text

    await _delete(client, headers, f"applications/{gone.json()['id']}")

    report = await _report(client, headers)
    assert report["total_applications"] == 1
    assert report["screening"] == {
        "total_runs": 1, "completed": 1, "failed": 0, "average_score": 84.0,
    }


async def test_screening_metrics_never_count_another_organizations_runs(
    client: AsyncClient, super_admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ai_verdict(monkeypatch, "MATCH")
    org_a = await _bootstrap_org_with_open_job(client, "reports-screening-a")
    org_b = await _bootstrap_org_with_open_job(client, "reports-screening-b")
    applied = await apply_publicly(client, org_a["slug"], org_a["job_id"], email="a@example.com")
    assert applied.status_code == 201, applied.text

    assert (await _report(client, org_a["admin_headers"]))["screening"]["total_runs"] == 1
    report_b = await _report(client, org_b["admin_headers"])
    assert report_b["total_applications"] == 0
    assert report_b["screening"] == _EMPTY_SCREENING


async def test_completed_and_failed_runs_and_average_and_history_is_kept(
    client: AsyncClient,
    super_admin: User,
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    """Completed and failed runs are both counted; only completed scores make
    the average. Deleting an application drops its runs from the report while
    the ScreeningRun rows themselves stay (soft delete, audit trail)."""
    ctx = await _bootstrap_org_with_open_job(client, "reports-mixed-runs")
    headers = ctx["admin_headers"]

    _ai_verdict(monkeypatch, "MATCH")  # COMPLETED, score 84
    matched = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="m@example.com")
    assert matched.status_code == 201, matched.text
    _ai_verdict(monkeypatch, "NOT_MATCH")  # COMPLETED, score 12
    unmatched = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="n@example.com")
    assert unmatched.status_code == 201, unmatched.text
    failing = _StubScreeningProvider(None, error=AIProviderError("provider down"))
    monkeypatch.setattr(screening_service, "get_llm_provider", lambda: failing)  # FAILED
    failed = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="f@example.com")
    assert failed.status_code == 201, failed.text

    report = await _report(client, headers)
    assert report["total_applications"] == 3
    assert report["screening"] == {
        "total_runs": 3, "completed": 2, "failed": 1, "average_score": 48.0,
    }

    await _delete(client, headers, f"applications/{matched.json()['id']}")
    await _delete(client, headers, f"applications/{failed.json()['id']}")
    report = await _report(client, headers)
    assert report["screening"] == {
        "total_runs": 1, "completed": 1, "failed": 0, "average_score": 12.0,
    }

    async with rls_bypass(db_session):
        stored = await db_session.scalar(
            select(func.count(ScreeningRun.id)).where(
                ScreeningRun.application_id.in_(
                    [
                        uuid.UUID(matched.json()["id"]),
                        uuid.UUID(failed.json()["id"]),
                    ]
                )
            )
        )
    assert stored == 2  # the history of the deleted applications is untouched
