"""A returning candidate on the self-service apply flow, end to end over HTTP
(app/services/public_application_service.py):

- a new email verifies and registers normally, with nothing to prefill
- an existing email gets its stored profile back only once the one-time
  code has been verified — never before, never from another organization
- one profile, many applications: an existing candidate may apply for a
  different job (outside the reapply window), as the same Candidate row
- already applied for *this* job -> stopped at verification, before any
  form/resume step — and the submission still refuses it on its own
- HR's reapply permission lets the same job be applied for again; without
  it the candidate stays blocked
- a mobile number belonging to another candidate is refused by the
  pre-resume precheck with a clear, mobile-specific error that leaks nothing
  about the other profile, and burns the verification token
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
from tests.public_apply import apply_publicly, phone_for, request_code
from tests.test_candidate_intake import _second_job, _StubScreeningProvider
from tests.test_public_applications import _bootstrap_org_with_open_job

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _ai_matches(monkeypatch: pytest.MonkeyPatch, otp_settings) -> None:
    provider = _StubScreeningProvider("MATCH")
    monkeypatch.setattr(screening_service, "get_llm_provider", lambda: provider)
    # These tests verify the same address several times in a row.
    otp_settings(email_otp_resend_cooldown_seconds=0, email_otp_max_sends_per_hour=50)


def _base(slug: str) -> str:
    return f"/api/v1/public/organizations/{slug}"


async def _verify(
    client: AsyncClient, slug: str, email: str, *, job_id: str | None = None
) -> Response:
    response, code = await request_code(client, slug, email)
    assert response.status_code == 202, response.text
    payload: dict[str, Any] = {"email": email, "code": code}
    if job_id is not None:
        payload["job_id"] = job_id
    return await client.post(f"{_base(slug)}/email-verification/verify", json=payload)


async def _precheck(
    client: AsyncClient, slug: str, job_id: str, *, email: str, token: str, phone: str
) -> Response:
    return await client.post(
        f"{_base(slug)}/jobs/{job_id}/apply/precheck",
        json={"email": email, "email_verification_token": token, "phone": phone},
    )


async def _age_applications(db: AsyncSession, email: str, *, days: int) -> None:
    """Moves a candidate's applications into the past (no API can), so the
    3-month reapply window has passed. Committed for the same reason as
    tests/test_reapply_window.py::_seed_applicant."""
    moment = datetime.now(UTC) - timedelta(days=days)
    async with rls_bypass(db):
        candidate_id = await db.scalar(select(Candidate.id).where(Candidate.email == email))
        await db.execute(
            update(Application)
            .where(Application.candidate_id == candidate_id)
            .values(applied_at=moment, created_at=moment)
        )
    await db.commit()


async def _candidates(client: AsyncClient, ctx: dict) -> list[dict[str, Any]]:
    response = await client.get("/api/v1/recruiter/candidates", headers=ctx["admin_headers"])
    assert response.status_code == 200, response.text
    return list(response.json())


# --- 1. new email -------------------------------------------------------------


async def test_new_email_verifies_with_nothing_to_prefill_and_registers(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "returning-new")
    verified = await _verify(client, ctx["slug"], "new@example.com", job_id=ctx["job_id"])
    assert verified.status_code == 200, verified.text
    assert verified.json()["existing_profile"] is None
    token = verified.json()["verification_token"]

    precheck = await _precheck(
        client, ctx["slug"], ctx["job_id"],
        email="new@example.com", token=token, phone=phone_for("new@example.com"),
    )
    assert precheck.status_code == 204, precheck.text

    applied = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="new@example.com", token=token
    )
    assert applied.status_code == 201, applied.text
    assert [c["email"] for c in await _candidates(client, ctx)] == ["new@example.com"]


# --- 2./3. profile only after verification, fully prefilled -----------------------


async def test_profile_is_only_returned_after_the_code_is_verified(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "returning-otp-first")
    first = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="ola@example.com", full_name="Ola Owner"
    )
    assert first.status_code == 201, first.text
    await _age_applications(db_session, "ola@example.com", days=130)
    other_job = await _second_job(client, ctx)
    private = [phone_for("ola@example.com"), "1998-04-12", "Ola Owner", "Chennai"]

    # Requesting a code says nothing about the address.
    response, code = await request_code(client, ctx["slug"], "ola@example.com")
    assert response.status_code == 202
    assert set(response.json()) == {"expires_in_seconds", "resend_available_in_seconds"}

    # A wrong code: refused, and nothing about the profile comes back.
    wrong = "000000" if code != "000000" else "111111"
    refused = await client.post(
        f"{_base(ctx['slug'])}/email-verification/verify",
        json={"email": "ola@example.com", "code": wrong, "job_id": other_job},
    )
    assert refused.status_code == 422
    for value in private:
        assert value not in refused.text

    # The precheck can't be used as a lookup without a verified token either.
    unverified = await _precheck(
        client, ctx["slug"], other_job,
        email="ola@example.com", token="not-a-real-token", phone=phone_for("ola@example.com"),
    )
    assert unverified.status_code == 422
    assert unverified.json()["error"]["code"] == "email_not_verified"

    # The right code: the profile comes back.
    verified = await client.post(
        f"{_base(ctx['slug'])}/email-verification/verify",
        json={"email": "ola@example.com", "code": code, "job_id": other_job},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["existing_profile"]["full_name"] == "Ola Owner"


async def test_verified_returning_candidate_gets_every_stored_field_to_prefill(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "returning-prefill")
    first = await apply_publicly(
        client,
        ctx["slug"],
        ctx["job_id"],
        email="pia@example.com",
        full_name="Pia Prefill",
        candidate_type="EXPERIENCED",
        years_experience="6",
        notice_period_days="45",
        current_title="Senior Engineer",
        current_company="Globex",
        languages=["English", "Hindi"],
    )
    assert first.status_code == 201, first.text
    await _age_applications(db_session, "pia@example.com", days=130)
    other_job = await _second_job(client, ctx)

    verified = await _verify(client, ctx["slug"], "pia@example.com", job_id=other_job)
    assert verified.status_code == 200, verified.text
    assert verified.json()["existing_profile"] == {
        "full_name": "Pia Prefill",
        "phone": phone_for("pia@example.com"),
        "date_of_birth": "1998-04-12",
        "place_of_birth": "Chennai",
        "languages": ["English", "Hindi"],
        "candidate_type": "EXPERIENCED",
        "years_experience": 6,
        "notice_period_days": 45,
        "immediate_joiner": False,
        "current_title": "Senior Engineer",
        "current_company": "Globex",
        "location": "Chennai",
        "preferred_location": "Bengaluru",
        "qualification": "B.Tech Computer Science",
        "linkedin_url": "https://www.linkedin.com/in/jane-candidate",
        "github_url": "https://github.com/jane-candidate",
        "tenth_marks_percentage": None,
    }


async def test_hr_created_profile_prefills_only_what_is_stored(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "returning-hr-partial")
    created = await client.post(
        "/api/v1/recruiter/candidates",
        json={"email": "hana@example.com", "full_name": "Hana Partial"},
        headers=ctx["admin_headers"],
    )
    assert created.status_code == 201, created.text

    verified = await _verify(client, ctx["slug"], "hana@example.com", job_id=ctx["job_id"])
    assert verified.status_code == 200, verified.text
    profile = verified.json()["existing_profile"]
    assert profile["full_name"] == "Hana Partial"
    assert profile["phone"] is None
    assert profile["date_of_birth"] is None
    assert profile["languages"] == []


# --- 4./9. one profile, many applications ---------------------------------------------


async def test_existing_candidate_may_apply_for_a_different_job_as_the_same_profile(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "returning-other-job")
    first = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="dev@example.com")
    assert first.status_code == 201, first.text
    await _age_applications(db_session, "dev@example.com", days=130)
    (before,) = await _candidates(client, ctx)
    other_job = await _second_job(client, ctx, "Software Engineer")

    verified = await _verify(client, ctx["slug"], "dev@example.com", job_id=other_job)
    assert verified.status_code == 200, verified.text
    token = verified.json()["verification_token"]
    # Their own mobile number on their own profile is no conflict.
    precheck = await _precheck(
        client, ctx["slug"], other_job,
        email="dev@example.com", token=token, phone=phone_for("dev@example.com"),
    )
    assert precheck.status_code == 204, precheck.text

    applied = await apply_publicly(
        client, ctx["slug"], other_job, email="dev@example.com", token=token
    )
    assert applied.status_code == 201, applied.text

    # Same email + same mobile = the same person: still one Candidate row,
    # now with two applications.
    (after,) = await _candidates(client, ctx)
    assert after["id"] == before["id"]
    applications = (
        await client.get(
            "/api/v1/recruiter/applications",
            params={"candidate_id": before["id"]},
            headers=ctx["admin_headers"],
        )
    ).json()
    assert sorted(a["job_id"] for a in applications) == sorted([ctx["job_id"], other_job])


# --- 5./7. already applied for this job -------------------------------------------------


async def test_already_applied_to_this_job_is_stopped_at_verification(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    """Even once the reapply window has passed: one application per job."""
    ctx = await _bootstrap_org_with_open_job(client, "returning-same-job")
    first = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="sam@example.com")
    assert first.status_code == 201, first.text
    await _age_applications(db_session, "sam@example.com", days=130)

    blocked = await _verify(client, ctx["slug"], "sam@example.com", job_id=ctx["job_id"])
    assert blocked.status_code == 409
    error = blocked.json()["error"]
    assert error["code"] == "already_applied_to_job"
    assert "Senior Backend Engineer" in error["message"]
    assert "permission to reapply" in error["message"]
    assert "existing_profile" not in blocked.text


async def test_submission_refuses_the_same_job_even_if_the_early_check_is_skipped(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    """The backend stays authoritative: a client that verifies without
    naming the job still can't submit a second application for it."""
    ctx = await _bootstrap_org_with_open_job(client, "returning-same-job-submit")
    first = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="sid@example.com")
    assert first.status_code == 201, first.text
    await _age_applications(db_session, "sid@example.com", days=130)

    verified = await _verify(client, ctx["slug"], "sid@example.com")
    assert verified.status_code == 200, verified.text
    token = verified.json()["verification_token"]

    precheck = await _precheck(
        client, ctx["slug"], ctx["job_id"],
        email="sid@example.com", token=token, phone=phone_for("sid@example.com"),
    )
    assert precheck.status_code == 409
    assert precheck.json()["error"]["code"] == "already_applied_to_job"

    applied = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="sid@example.com", token=token
    )
    assert applied.status_code == 409
    assert applied.json()["error"]["code"] == "already_applied_to_job"
    assert len((await client.get(
        "/api/v1/recruiter/applications", headers=ctx["admin_headers"]
    )).json()) == 1


async def test_without_reapply_permission_the_candidate_stays_blocked(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "returning-no-permission")
    first = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="nia@example.com")
    assert first.status_code == 201, first.text
    other_job = await _second_job(client, ctx)

    # Inside the 3-month window, whatever job they look at: no code is sent,
    # and the only answer is "You have already applied."
    for job_id in (ctx["job_id"], other_job):
        response = await client.post(
            f"{_base(ctx['slug'])}/email-verification/request",
            json={"email": "nia@example.com", "job_id": job_id},
        )
        assert response.status_code == 409, job_id
        assert response.json()["error"]["code"] == "already_applied"


# --- 6. HR reapply permission ------------------------------------------------------------


async def test_reapply_permission_lets_the_candidate_apply_for_the_same_job_again(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "returning-permission")
    first = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="rae@example.com", full_name="Rae Return"
    )
    assert first.status_code == 201, first.text
    (candidate,) = await _candidates(client, ctx)

    blocked, _ = await request_code(client, ctx["slug"], "rae@example.com")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "already_applied"

    granted = await client.post(
        f"/api/v1/recruiter/candidates/{candidate['id']}/grant-reapply",
        headers=ctx["admin_headers"],
    )
    assert granted.status_code == 200, granted.text

    verified = await _verify(client, ctx["slug"], "rae@example.com", job_id=ctx["job_id"])
    assert verified.status_code == 200, verified.text
    assert verified.json()["existing_profile"]["full_name"] == "Rae Return"
    applied = await apply_publicly(
        client,
        ctx["slug"],
        ctx["job_id"],
        email="rae@example.com",
        full_name="Rae Return",
        token=verified.json()["verification_token"],
    )
    assert applied.status_code == 201, applied.text
    (after,) = await _candidates(client, ctx)
    assert after["id"] == candidate["id"]


# --- 8. somebody else's mobile number --------------------------------------------------------


async def test_mobile_of_another_candidate_is_refused_before_the_resume_step(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "returning-phone-taken")
    owner = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="own@example.com")
    assert owner.status_code == 201, owner.text

    verified = await _verify(client, ctx["slug"], "other@example.com", job_id=ctx["job_id"])
    assert verified.status_code == 200, verified.text
    token = verified.json()["verification_token"]

    # Differently formatted, same number.
    taken = phone_for("own@example.com").replace("+91", "0")
    refused = await _precheck(
        client, ctx["slug"], ctx["job_id"], email="other@example.com", token=token, phone=taken
    )
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["code"] == "phone_already_registered"
    assert "mobile number" in error["message"]
    assert "own@example.com" not in refused.text
    assert "Jane Candidate" not in refused.text

    # The token is burned: it can't be replayed to probe other numbers.
    replay = await _precheck(
        client, ctx["slug"], ctx["job_id"],
        email="other@example.com", token=token, phone=phone_for("other@example.com"),
    )
    assert replay.status_code == 422
    assert replay.json()["error"]["code"] == "email_not_verified"
    assert len(await _candidates(client, ctx)) == 1


async def test_precheck_rejects_an_invalid_mobile_number(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "returning-phone-invalid")
    verified = await _verify(client, ctx["slug"], "ivy@example.com", job_id=ctx["job_id"])
    token = verified.json()["verification_token"]
    response = await _precheck(
        client, ctx["slug"], ctx["job_id"], email="ivy@example.com", token=token, phone="12"
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_phone"


# --- 10. tenant isolation -------------------------------------------------------------------


async def test_another_organizations_candidate_is_never_discovered(
    client: AsyncClient, super_admin: User
) -> None:
    org_a = await _bootstrap_org_with_open_job(client, "returning-tenant-a")
    org_b = await _bootstrap_org_with_open_job(client, "returning-tenant-b")
    applied = await apply_publicly(
        client, org_a["slug"], org_a["job_id"], email="tia@example.com", full_name="Tia Tenant"
    )
    assert applied.status_code == 201, applied.text

    # The same email at org B is a stranger there: no profile, no block.
    verified = await _verify(client, org_b["slug"], "tia@example.com", job_id=org_b["job_id"])
    assert verified.status_code == 200, verified.text
    assert verified.json()["existing_profile"] is None
    assert "Tia Tenant" not in verified.text

    # Org A's candidate's mobile number is no conflict at org B either.
    other = await _verify(client, org_b["slug"], "uma@example.com", job_id=org_b["job_id"])
    precheck = await _precheck(
        client, org_b["slug"], org_b["job_id"],
        email="uma@example.com",
        token=other.json()["verification_token"],
        phone=phone_for("tia@example.com"),
    )
    assert precheck.status_code == 204, precheck.text

    # And org A's job can't be named through org B's site.
    cross = await _verify(client, org_b["slug"], "tia@example.com", job_id=org_a["job_id"])
    assert cross.status_code == 404


# --- 14. the verify step itself is unchanged for older clients ---------------------------------


async def test_verify_without_a_job_still_issues_a_token(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "returning-no-job")
    verified = await _verify(client, ctx["slug"], "old.client@example.com")
    assert verified.status_code == 200, verified.text
    assert set(verified.json()) == {"verification_token", "expires_at", "existing_profile"}
