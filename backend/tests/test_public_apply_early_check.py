"""The apply form's early "already applied?" check, before any one-time code
(app/services/public_application_service.py::has_already_applied):

    existing application -> reapply permitted (window passed / HR grant)?
        yes -> allowed        no -> "You have already applied." — whatever job
    no existing application -> allowed

The check answers only yes/no; the code request enforces the same rule on
its own (no code is sent to a blocked applicant), and verification,
precheck and submission keep enforcing theirs.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import rls_bypass
from app.models.application import Application
from app.models.candidate import Candidate
from app.models.user import User
from app.services import screening_service
from tests.public_apply import (
    apply_publicly,
    phone_for,
    request_campus_code,
    request_code,
    verify_email,
)
from tests.test_candidate_intake import _active_drive, _second_job, _StubScreeningProvider
from tests.test_public_applications import _bootstrap_org_with_open_job

pytestmark = pytest.mark.asyncio

#: Everything about a candidate that must never come back before the email
#: has been verified.
PRIVATE_VALUES = ["Dee Private", "1998-04-12", "Chennai", "Bengaluru", "linkedin", "github"]


@pytest.fixture(autouse=True)
def _ai_matches(monkeypatch: pytest.MonkeyPatch, otp_settings) -> None:
    provider = _StubScreeningProvider("MATCH")
    monkeypatch.setattr(screening_service, "get_llm_provider", lambda: provider)
    otp_settings(email_otp_resend_cooldown_seconds=0, email_otp_max_sends_per_hour=50)


def _base(slug: str) -> str:
    return f"/api/v1/public/organizations/{slug}"


async def _status(client: AsyncClient, slug: str, **body: Any) -> Response:
    return await client.post(f"{_base(slug)}/applicant-status", json=body)


async def _already_applied(client: AsyncClient, slug: str, **body: Any) -> bool:
    response = await _status(client, slug, **body)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"already_applied"}  # nothing else, ever
    return bool(response.json()["already_applied"])


async def _age_applications(db: AsyncSession, email: str, *, days: int) -> None:
    """Moves a candidate's applications into the past so the 3-month reapply
    window has passed (committed: see tests/test_reapply_window.py)."""
    moment = datetime.now(UTC) - timedelta(days=days)
    async with rls_bypass(db):
        candidate_id = await db.scalar(select(Candidate.id).where(Candidate.email == email))
        await db.execute(
            update(Application)
            .where(Application.candidate_id == candidate_id)
            .values(applied_at=moment, created_at=moment)
        )
    await db.commit()


# --- 1. new candidate -------------------------------------------------------------------


async def test_a_new_candidate_is_allowed(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "early-new")
    assert not await _already_applied(
        client, ctx["slug"], email="new@example.com", job_id=ctx["job_id"]
    )
    assert not await _already_applied(client, ctx["slug"], phone=phone_for("new@example.com"))
    response, code = await request_code(client, ctx["slug"], "new@example.com")
    assert response.status_code == 202
    assert code is not None


# --- 2./3./5./6./12. an existing application blocks, for any job, before any code -----------


async def test_an_existing_application_blocks_early_for_any_job_and_reveals_nothing(
    client: AsyncClient, super_admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Screened out by the AI: the early check must look exactly the same.
    provider = _StubScreeningProvider("NOT_MATCH")
    monkeypatch.setattr(screening_service, "get_llm_provider", lambda: provider)
    ctx = await _bootstrap_org_with_open_job(client, "early-blocked")
    first = await apply_publicly(
        client,
        ctx["slug"],
        ctx["job_id"],
        email="dee@example.com",
        full_name="Dee Private",
        preferred_location="Bengaluru",
    )
    assert first.status_code == 201, first.text
    other_job = await _second_job(client, ctx)

    for body in (
        {"email": "dee@example.com", "job_id": ctx["job_id"]},
        {"email": "DEE@Example.com ", "job_id": other_job},  # a different job is no way round
        {"email": "dee@example.com"},
        {"phone": phone_for("dee@example.com")},
        {"phone": phone_for("dee@example.com").replace("+91", "0"), "job_id": other_job},
        # A brand-new email with the blocked person's mobile number.
        {"email": "someone.else@example.com", "phone": phone_for("dee@example.com")},
    ):
        response = await _status(client, ctx["slug"], **body)
        assert response.status_code == 200, (body, response.text)
        assert response.json() == {"already_applied": True}, body
        for value in PRIVATE_VALUES + ["NOT_MATCH", "score", "screen", "Senior Backend"]:
            assert value.lower() not in response.text.lower(), (body, value)

    # Blocked candidates never reach the code step, even calling the API
    # directly: no code is emailed, and the refusal is equally minimal.
    for job_id in (ctx["job_id"], other_job, None):
        refused = await client.post(
            f"{_base(ctx['slug'])}/email-verification/request",
            json={"email": "dee@example.com", "job_id": job_id},
        )
        assert refused.status_code == 409
        error = refused.json()["error"]
        assert (error["code"], error["message"]) == ("already_applied", "You have already applied.")
        for value in PRIVATE_VALUES:
            assert value.lower() not in refused.text.lower()
    _, code = await request_code(client, ctx["slug"], "dee@example.com")
    assert code is None


# --- 4./11. reapply permission is the only exception -------------------------------------


async def test_reapply_window_passed_allows_other_jobs_but_never_the_same_one(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "early-window")
    first = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="wes@example.com")
    assert first.status_code == 201, first.text
    other_job = await _second_job(client, ctx)
    assert await _already_applied(client, ctx["slug"], email="wes@example.com", job_id=other_job)

    await _age_applications(db_session, "wes@example.com", days=130)

    assert not await _already_applied(
        client, ctx["slug"], email="wes@example.com", job_id=other_job
    )
    # One application per job still holds (uq_applications_candidate_job).
    assert await _already_applied(
        client, ctx["slug"], email="wes@example.com", job_id=ctx["job_id"]
    )
    applied = await apply_publicly(client, ctx["slug"], other_job, email="wes@example.com")
    assert applied.status_code == 201, applied.text


async def test_hr_reapply_grant_allows_the_candidate_again(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "early-grant")
    first = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="gia@example.com")
    assert first.status_code == 201, first.text
    assert await _already_applied(
        client, ctx["slug"], email="gia@example.com", job_id=ctx["job_id"]
    )

    candidate_id = (
        await client.get("/api/v1/recruiter/candidates", headers=ctx["admin_headers"])
    ).json()[0]["id"]
    granted = await client.post(
        f"/api/v1/recruiter/candidates/{candidate_id}/grant-reapply",
        headers=ctx["admin_headers"],
    )
    assert granted.status_code == 200, granted.text

    assert not await _already_applied(
        client, ctx["slug"], email="gia@example.com", job_id=ctx["job_id"]
    )
    applied = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="gia@example.com")
    assert applied.status_code == 201, applied.text
    # ...and once that application is in, they are blocked again.
    assert await _already_applied(client, ctx["slug"], email="gia@example.com")


# --- 7. the profile only after the code -----------------------------------------------------


async def test_profile_prefill_comes_only_after_the_code_is_verified(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "early-prefill")
    first = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="pam@example.com", full_name="Dee Private"
    )
    assert first.status_code == 201, first.text
    await _age_applications(db_session, "pam@example.com", days=130)
    other_job = await _second_job(client, ctx)

    early = await _status(client, ctx["slug"], email="pam@example.com", job_id=other_job)
    assert early.json() == {"already_applied": False}
    for value in PRIVATE_VALUES:
        assert value.lower() not in early.text.lower()

    response, code = await request_code(client, ctx["slug"], "pam@example.com")
    assert response.status_code == 202
    verified = await client.post(
        f"{_base(ctx['slug'])}/email-verification/verify",
        json={"email": "pam@example.com", "code": code, "job_id": other_job},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["existing_profile"]["full_name"] == "Dee Private"
    assert verified.json()["existing_profile"]["date_of_birth"] == "1998-04-12"


# --- 8. identity input ------------------------------------------------------------------------


async def test_check_input_is_validated(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "early-input")
    assert (await _status(client, ctx["slug"])).status_code == 422
    invalid_phone = await _status(client, ctx["slug"], phone="12")
    assert invalid_phone.status_code == 422
    assert invalid_phone.json()["error"]["code"] == "invalid_phone"
    assert (await _status(client, ctx["slug"], email="not-an-email")).status_code == 422
    unknown_job = await _status(
        client, ctx["slug"], email="a@example.com", job_id="00000000-0000-0000-0000-000000000000"
    )
    assert unknown_job.status_code == 404


# --- 9. the backend refuses a bypassed duplicate on its own -----------------------------------


async def test_a_bypassed_duplicate_submission_is_still_refused(
    client: AsyncClient, super_admin: User
) -> None:
    """A different, never-used email gets a code — but the blocked person's
    mobile number on the submission is refused, and no second application
    or profile appears."""
    ctx = await _bootstrap_org_with_open_job(client, "early-bypass")
    first = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="bea@example.com")
    assert first.status_code == 201, first.text

    token = await verify_email(client, ctx["slug"], "fresh@example.com")
    second = await apply_publicly(
        client,
        ctx["slug"],
        ctx["job_id"],
        email="fresh@example.com",
        token=token,
        phone=phone_for("bea@example.com"),
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "phone_already_registered"
    applications = await client.get(
        "/api/v1/recruiter/applications", headers=ctx["admin_headers"]
    )
    assert len(applications.json()) == 1


# --- 10. tenant isolation ---------------------------------------------------------------------


async def test_another_organizations_applicant_is_not_blocked_here(
    client: AsyncClient, super_admin: User
) -> None:
    org_a = await _bootstrap_org_with_open_job(client, "early-tenant-a")
    org_b = await _bootstrap_org_with_open_job(client, "early-tenant-b")
    applied = await apply_publicly(client, org_a["slug"], org_a["job_id"], email="tom@example.com")
    assert applied.status_code == 201, applied.text

    assert await _already_applied(client, org_a["slug"], email="tom@example.com")
    assert not await _already_applied(
        client, org_b["slug"], email="tom@example.com", job_id=org_b["job_id"]
    )
    assert not await _already_applied(client, org_b["slug"], phone=phone_for("tom@example.com"))
    # Org A's job can't be named through org B's site.
    cross = await _status(client, org_b["slug"], email="tom@example.com", job_id=org_a["job_id"])
    assert cross.status_code == 404


# --- campus drive link -------------------------------------------------------------------------


async def test_campus_drive_link_applies_the_same_early_check(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "early-campus")
    _drive, drive_token = await _active_drive(client, ctx)
    applied = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="cal@example.com")
    assert applied.status_code == 201, applied.text

    url = f"/api/v1/public/campus-drive/{drive_token}/applicant-status"
    blocked = await client.post(url, json={"email": "cal@example.com"})
    assert blocked.json() == {"already_applied": True}
    by_phone = await client.post(url, json={"phone": phone_for("cal@example.com")})
    assert by_phone.json() == {"already_applied": True}
    newcomer = await client.post(url, json={"email": "newcomer@example.com"})
    assert newcomer.json() == {"already_applied": False}

    refused, code = await request_campus_code(client, drive_token, "cal@example.com")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "already_applied"
    assert code is None
