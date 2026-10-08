"""First HR meeting requirements, end to end over HTTP:

A. valid candidate -> email verified -> AI MATCH -> application + welcome email
B. email one-time-code rules (wrong / expired / attempt limit / resend
   throttle / hourly cap / token binding and single use)
C. duplicate candidate by email or mobile -> blocked, nothing created
D. AI NOT_MATCH -> AI_SCREENED_OUT, retained; the apply response is the same
   neutral confirmation as a MATCH (the decision reaches the candidate only
   by email)
E. HR override of the AI decision (reason required, audited, role-gated)
F. HR matching a candidate to other jobs; the candidate can't self-apply again
G. organization careers contact configuration (SUPER_ADMIN only, public read)
H. campus drive links follow the same identity rules (verified email, one
   application per person across both entry points, AI screening, welcome)

The AI verdict comes from a stub `LLMProvider` (a real model can't run in
unit tests); the real Gemini path is exercised by
scripts/verify_ai_screening.py against a realistic JD and resumes.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import rls_bypass
from app.integrations.ai import AIProviderError, ScreeningVerdict
from app.integrations.email import EmailError
from app.models.email_verification import EmailVerification
from app.models.user import User
from app.services import notification_service, screening_service
from tests.conftest import (
    SUPER_ADMIN_EMAIL,
    SUPER_ADMIN_PASSWORD,
    RecordingEmailProvider,
    login,
)
from tests.public_apply import (
    apply_publicly,
    apply_to_campus_drive,
    phone_for,
    request_campus_code,
    request_code,
    verify_email,
)
from tests.test_email_workflow import _staff_headers
from tests.test_public_applications import _bootstrap_org_with_open_job


class _StubScreeningProvider:
    name = "stub"
    model = "stub-model"

    def __init__(self, decision: str | None, error: Exception | None = None) -> None:
        self.decision = decision
        self.error = error

    async def screen_candidate(self, *, resume_text: str, job_title: str, job_description: str):
        if self.error is not None:
            raise self.error
        matched = self.decision == "MATCH"
        return ScreeningVerdict(
            decision=self.decision,
            overall_score=84 if matched else 12,
            recommendation="STRONG_MATCH" if matched else "NOT_A_MATCH",
            summary="Internal summary for recruiters only.",
            matched_requirements=["Python"] if matched else [],
            missing_requirements=[] if matched else ["React.js", "Node.js"],
            matching_skills=["Python"] if matched else [],
            missing_skills=[] if matched else ["React.js"],
        )


@pytest.fixture
def ai_verdict(monkeypatch: pytest.MonkeyPatch):
    def apply(decision: str | None = "MATCH", error: Exception | None = None) -> None:
        provider = _StubScreeningProvider(decision, error)
        monkeypatch.setattr(screening_service, "get_llm_provider", lambda: provider)

    return apply


def _slug(name: str) -> str:
    return f"intake-{name}"


async def _applications(client: AsyncClient, ctx: dict) -> list[dict[str, Any]]:
    return (
        await client.get("/api/v1/recruiter/applications", headers=ctx["admin_headers"])
    ).json()


async def _candidates(client: AsyncClient, ctx: dict) -> list[dict[str, Any]]:
    return (await client.get("/api/v1/recruiter/candidates", headers=ctx["admin_headers"])).json()


async def _activities(client: AsyncClient, ctx: dict) -> list[str]:
    body = (
        await client.get("/api/v1/recruiter/activities?limit=200", headers=ctx["admin_headers"])
    ).json()
    items = body["items"] if isinstance(body, dict) else body
    return [a["action"] for a in items]


async def _set_contact(client: AsyncClient, slug: str, email: str | None) -> dict:
    tokens = await login(client, email=SUPER_ADMIN_EMAIL, password=SUPER_ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    orgs = (await client.get("/api/v1/admin/organizations", headers=headers)).json()
    org = next(o for o in orgs if o["slug"] == slug)
    response = await client.patch(
        f"/api/v1/admin/organizations/{org['id']}",
        json={"careers_contact_email": email},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _second_job(client: AsyncClient, ctx: dict, title: str = "Data Analyst") -> str:
    job = await client.post(
        "/api/v1/recruiter/jobs",
        json={"title": title, "description": f"{title}: SQL, Excel, dashboards."},
        headers=ctx["admin_headers"],
    )
    assert job.status_code == 201, job.text
    await client.patch(
        f"/api/v1/recruiter/jobs/{job.json()['id']}",
        json={"status": "OPEN"},
        headers=ctx["admin_headers"],
    )
    return str(job.json()["id"])


#: Everything a candidate's apply response may carry — no screening outcome,
#: status, score, decision or evidence, whatever the AI verdict was.
CANDIDATE_CONFIRMATION_FIELDS = {
    "id",
    "job_title",
    "candidate_email",
    "confirmation_email_sent",
    "careers_contact_email",
    "submitted_at",
}
CAMPUS_CONFIRMATION_FIELDS = CANDIDATE_CONFIRMATION_FIELDS | {"assessment_invitation_link"}


# --- A. valid candidate ------------------------------------------------------


async def test_valid_candidate_matching_resume_gets_application_and_welcome_email(
    client: AsyncClient,
    super_admin: User,
    recording_email: RecordingEmailProvider,
    ai_verdict,
) -> None:
    ai_verdict("MATCH")
    ctx = await _bootstrap_org_with_open_job(client, _slug("match"))
    await _set_contact(client, ctx["slug"], "talent@intake-match.dev")

    response = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="ana@example.com")
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == CANDIDATE_CONFIRMATION_FIELDS
    assert body["confirmation_email_sent"] is True
    assert body["careers_contact_email"] == "talent@intake-match.dev"
    assert "MATCH" not in response.text

    (welcome,) = recording_email.sent
    assert welcome.to == ["ana@example.com"]
    assert welcome.reply_to == "talent@intake-match.dev"
    assert "Welcome to Acme Corp" in welcome.subject
    assert "talent@intake-match.dev" in welcome.text
    assert "AI" not in welcome.text and "screen" not in welcome.text.lower()

    (application,) = await _applications(client, ctx)
    assert application["status"] == "APPLIED"
    runs = (
        await client.get(
            f"/api/v1/recruiter/applications/{application['id']}/screening",
            headers=ctx["admin_headers"],
        )
    ).json()
    assert runs[0]["decision"] == "MATCH"
    assert runs[0]["requested_by_user_id"] is None  # the system's own run
    assert runs[0]["matched_requirements"] == ["Python"]

    actions = await _activities(client, ctx)
    for expected in (
        "CANDIDATE_REGISTERED",
        "CANDIDATE_EMAIL_VERIFIED",
        "APPLICATION_CREATED",
        "AI_SCREENING_COMPLETED",
        "CANDIDATE_WELCOME_EMAIL_SENT",
    ):
        assert expected in actions


async def test_welcome_email_failure_never_loses_the_application(
    client: AsyncClient, super_admin: User, monkeypatch: pytest.MonkeyPatch, ai_verdict
) -> None:
    ai_verdict("MATCH")
    ctx = await _bootstrap_org_with_open_job(client, _slug("welcome-fail"))
    token = await verify_email(client, ctx["slug"], "ben@example.com")

    failing = RecordingEmailProvider(error=EmailError("Provider rejected the message."))
    monkeypatch.setattr(notification_service, "get_email_provider", lambda: failing)
    response = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="ben@example.com", token=token
    )
    assert response.status_code == 201, response.text
    assert response.json()["confirmation_email_sent"] is False
    assert len(await _applications(client, ctx)) == 1
    assert "CANDIDATE_WELCOME_EMAIL_FAILED" in await _activities(client, ctx)


async def test_ai_unavailable_keeps_application_for_manual_review(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider, ai_verdict
) -> None:
    ai_verdict(error=AIProviderError("model down"))
    ctx = await _bootstrap_org_with_open_job(client, _slug("ai-down"))

    response = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="cai@example.com")
    assert response.status_code == 201
    assert set(response.json()) == CANDIDATE_CONFIRMATION_FIELDS
    (application,) = await _applications(client, ctx)
    # Never screened out on a failure — a person decides.
    assert application["status"] == "APPLIED"
    assert "AI_SCREENING_FAILED" in await _activities(client, ctx)
    assert len(recording_email.sent) == 1  # still welcomed into the pipeline


# --- B. email verification ---------------------------------------------------


async def test_application_requires_a_verified_email(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, _slug("unverified"))

    for token in ("", "forged-token"):
        response = await apply_publicly(
            client, ctx["slug"], ctx["job_id"], email="dee@example.com", token=token
        )
        assert response.status_code == 422, response.text
    assert await _candidates(client, ctx) == []


async def test_verification_token_is_bound_to_its_email_and_single_use(
    client: AsyncClient, super_admin: User, otp_settings
) -> None:
    otp_settings(email_otp_resend_cooldown_seconds=0)
    ctx = await _bootstrap_org_with_open_job(client, _slug("token-bound"))
    token = await verify_email(client, ctx["slug"], "owner@example.com")

    stolen = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="someone-else@example.com", token=token
    )
    assert stolen.status_code == 422
    assert stolen.json()["error"]["code"] == "email_not_verified"

    first = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="owner@example.com", token=token
    )
    assert first.status_code == 201
    job_b = await _second_job(client, ctx)
    replay = await apply_publicly(
        client, ctx["slug"], job_b, email="owner@example.com", token=token
    )
    assert replay.status_code == 422


async def test_wrong_code_counts_attempts_then_locks(
    client: AsyncClient, super_admin: User, otp_settings
) -> None:
    otp_settings(email_otp_max_attempts=3)
    ctx = await _bootstrap_org_with_open_job(client, _slug("wrong-code"))
    response, code = await request_code(client, ctx["slug"], "eve@example.com")
    assert response.status_code == 202
    wrong = "000000" if code != "000000" else "111111"
    url = f"/api/v1/public/organizations/{ctx['slug']}/email-verification/verify"

    first = await client.post(url, json={"email": "eve@example.com", "code": wrong})
    assert first.status_code == 422
    assert first.json()["error"]["code"] == "otp_incorrect"
    assert "2 attempts remaining" in first.json()["error"]["message"]
    await client.post(url, json={"email": "eve@example.com", "code": wrong})
    third = await client.post(url, json={"email": "eve@example.com", "code": wrong})
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "otp_locked"

    # Even the right code is useless once locked.
    right = await client.post(url, json={"email": "eve@example.com", "code": code})
    assert right.status_code in (422, 429)


async def test_expired_code_is_rejected(
    client: AsyncClient, super_admin: User, otp_settings
) -> None:
    otp_settings(email_otp_ttl_minutes=0)
    ctx = await _bootstrap_org_with_open_job(client, _slug("expired"))
    _, code = await request_code(client, ctx["slug"], "fin@example.com")

    response = await client.post(
        f"/api/v1/public/organizations/{ctx['slug']}/email-verification/verify",
        json={"email": "fin@example.com", "code": code},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "otp_expired"


async def test_resend_is_throttled_and_capped_per_hour(
    client: AsyncClient, super_admin: User, otp_settings
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, _slug("throttle"))
    first, _ = await request_code(client, ctx["slug"], "gus@example.com")
    assert first.status_code == 202
    immediate, code = await request_code(client, ctx["slug"], "gus@example.com")
    assert immediate.status_code == 429
    assert immediate.json()["error"]["code"] == "otp_resend_throttled"
    assert code is None  # nothing was sent

    otp_settings(email_otp_resend_cooldown_seconds=0, email_otp_max_sends_per_hour=2)
    second, _ = await request_code(client, ctx["slug"], "gus@example.com")
    assert second.status_code == 202
    capped, _ = await request_code(client, ctx["slug"], "gus@example.com")
    assert capped.status_code == 429
    assert capped.json()["error"]["code"] == "otp_send_limit"


async def test_code_is_never_stored_in_plaintext(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, _slug("hashed"))
    _, code = await request_code(client, ctx["slug"], "hal@example.com")
    assert code is not None

    async with rls_bypass(db_session):
        row = (
            await db_session.execute(
                select(EmailVerification).where(EmailVerification.email == "hal@example.com")
            )
        ).scalar_one()
    assert row.code_hash is not None and code not in row.code_hash
    assert len(row.code_hash) == 64


async def test_per_ip_rate_limit_on_code_requests(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, _slug("ip-limit"))
    statuses = [
        (await request_code(client, ctx["slug"], f"u{i}@example.com"))[0].status_code
        for i in range(11)
    ]
    assert statuses[:10] == [202] * 10
    assert statuses[10] == 429


# --- C. duplicate candidate ----------------------------------------------------


async def test_existing_email_cannot_create_a_second_profile(
    client: AsyncClient, super_admin: User, ai_verdict
) -> None:
    """An email already held by an HR-created profile never yields a *second*
    profile: the verified submission is matched onto the existing one.

    The gate is not the one-time-code step. A candidate HR created has no
    self-service application history, so they are free to apply and
    `verify_applicant_email` lets them through (the step only stops a
    returning candidate who may not reapply yet). Uniqueness is upheld at
    submission, where `_identify_applicant` resolves the verified email to
    the existing candidate — plus `uq_candidates_org_email` underneath.
    """
    ai_verdict("MATCH")
    ctx = await _bootstrap_org_with_open_job(client, _slug("dup-email"))
    created = await client.post(
        "/api/v1/recruiter/candidates",
        json={"email": "ivy@example.com", "full_name": "Ivy Existing"},
        headers=ctx["admin_headers"],
    )
    assert created.status_code == 201
    existing_id = created.json()["id"]

    # The code step succeeds — case-insensitively, the same person.
    _, code = await request_code(client, ctx["slug"], "IVY@example.com")
    verify = await client.post(
        f"/api/v1/public/organizations/{ctx['slug']}/email-verification/verify",
        json={"email": "IVY@example.com", "code": code},
    )
    assert verify.status_code == 200, verify.text

    applied = await apply_publicly(
        client,
        ctx["slug"],
        ctx["job_id"],
        email="IVY@example.com",
        token=str(verify.json()["verification_token"]),
    )
    assert applied.status_code == 201, applied.text

    # The guarantee: one profile, still the original row, now with the
    # details the candidate just verified.
    candidates = await _candidates(client, ctx)
    assert len(candidates) == 1
    assert candidates[0]["id"] == existing_id

    # The application belongs to that same profile, and the reuse is audited.
    applications = await _applications(client, ctx)
    assert len(applications) == 1
    assert applications[0]["candidate_id"] == existing_id
    assert "CANDIDATE_REAPPLIED" in await _activities(client, ctx)


async def test_existing_mobile_number_blocks_a_new_profile_whatever_its_formatting(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, _slug("dup-mobile"))
    first = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="jay@example.com", phone="+91 98765 43210"
    )
    assert first.status_code == 201

    job_b = await _second_job(client, ctx)
    second = await apply_publicly(
        client, ctx["slug"], job_b, email="jay.other@example.com", phone="098765-43210"
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "phone_already_registered"
    assert "mobile number" in second.json()["error"]["message"]
    assert "jay@example.com" not in second.text

    assert len(await _candidates(client, ctx)) == 1
    assert "DUPLICATE_APPLICATION_BLOCKED" in await _activities(client, ctx)


async def test_blocked_duplicate_burns_the_verification_token(
    client: AsyncClient, super_admin: User
) -> None:
    """Otherwise one verified email could be replayed to probe which mobile
    numbers are registered."""
    ctx = await _bootstrap_org_with_open_job(client, _slug("dup-burn"))
    await apply_publicly(client, ctx["slug"], ctx["job_id"], email="kai@example.com")

    token = await verify_email(client, ctx["slug"], "probe@example.com")
    blocked = await apply_publicly(
        client,
        ctx["slug"],
        ctx["job_id"],
        email="probe@example.com",
        token=token,
        phone=phone_for("kai@example.com"),
    )
    assert blocked.status_code == 409
    retry = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="probe@example.com", token=token
    )
    assert retry.status_code == 422


async def test_recruiter_cannot_create_a_duplicate_mobile_either(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, _slug("dup-recruiter"))
    for email in ("a@example.com", "b@example.com"):
        response = await client.post(
            "/api/v1/recruiter/candidates",
            json={"email": email, "full_name": "Same Person", "phone": "+91 99887 76655"},
            headers=ctx["admin_headers"],
        )
    assert response.status_code == 409
    assert "mobile number" in response.json()["error"]["message"]


# --- D. AI screened out ----------------------------------------------------------


async def test_non_matching_resume_is_screened_out_but_retained(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider, ai_verdict
) -> None:
    ai_verdict("NOT_MATCH")
    ctx = await _bootstrap_org_with_open_job(client, _slug("not-match"))
    await _set_contact(client, ctx["slug"], "hr@intake-not-match.dev")

    response = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="lee@example.com")
    assert response.status_code == 201, response.text
    body = response.json()
    # Exactly the same confirmation shape as a MATCH: the portal never
    # reveals the screening decision, reasons or evidence.
    assert set(body) == CANDIDATE_CONFIRMATION_FIELDS
    assert body["careers_contact_email"] == "hr@intake-not-match.dev"
    assert body["confirmation_email_sent"] is True
    for leaked in ("MATCH", "SHORTLISTED", "AI_SCREENED_OUT", "React.js", "Node.js"):
        assert leaked not in response.text
    # Still welcomed and acknowledged -- in the "not eligible for this role"
    # version, which never mentions AI or the reasons.
    (acknowledgement,) = recording_email.sent
    assert acknowledgement.to == ["lee@example.com"]
    assert acknowledgement.reply_to == "hr@intake-not-match.dev"
    assert acknowledgement.subject.startswith("Your application to Acme Corp")
    assert "not eligible for this particular position" in acknowledgement.text
    assert "retained" in acknowledgement.text
    assert "hr@intake-not-match.dev" in acknowledgement.text
    assert "AI" not in acknowledgement.text and "screen" not in acknowledgement.text.lower()
    assert "React.js" not in acknowledgement.text

    (application,) = await _applications(client, ctx)
    assert application["status"] == "AI_SCREENED_OUT"
    assert application["resume_id"] is not None
    (candidate,) = await _candidates(client, ctx)
    assert candidate["email"] == "lee@example.com"

    history = (
        await client.get(
            f"/api/v1/recruiter/candidates/{candidate['id']}/history",
            headers=ctx["admin_headers"],
        )
    ).json()
    (entry,) = history["applications"]
    assert entry["is_original"] is True
    assert entry["screenings"][0]["decision"] == "NOT_MATCH"
    assert entry["screenings"][0]["missing_requirements"] == ["React.js", "Node.js"]
    assert "AI_SCREENED_OUT" in [t["action"] for t in history["timeline"]]


# --- E. HR override ------------------------------------------------------------


async def test_hr_can_override_ai_screen_out_with_a_reason(
    client: AsyncClient, super_admin: User, ai_verdict
) -> None:
    ai_verdict("NOT_MATCH")
    ctx = await _bootstrap_org_with_open_job(client, _slug("override"))
    await apply_publicly(client, ctx["slug"], ctx["job_id"], email="max@example.com")
    (application,) = await _applications(client, ctx)
    url = f"/api/v1/recruiter/applications/{application['id']}"

    no_reason = await client.post(
        f"{url}/status", json={"to_status": "UNDER_REVIEW"}, headers=ctx["admin_headers"]
    )
    assert no_reason.status_code == 422

    overridden = await client.post(
        f"{url}/ai-override",
        json={"reason": "Strong adjacent experience; worth an interview."},
        headers=ctx["admin_headers"],
    )
    assert overridden.status_code == 200, overridden.text
    assert overridden.json()["status"] == "UNDER_REVIEW"

    again = await client.post(
        f"{url}/ai-override", json={"reason": "again"}, headers=ctx["admin_headers"]
    )
    assert again.status_code == 409
    assert "AI_SCREENING_OVERRIDDEN" in await _activities(client, ctx)


async def test_people_cannot_mark_an_application_ai_screened_out(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, _slug("system-only"))
    await apply_publicly(client, ctx["slug"], ctx["job_id"], email="ned@example.com")
    (application,) = await _applications(client, ctx)

    response = await client.post(
        f"/api/v1/recruiter/applications/{application['id']}/status",
        json={"to_status": "AI_SCREENED_OUT"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 409


async def test_hr_can_confirm_a_screen_out_as_rejected(
    client: AsyncClient, super_admin: User, ai_verdict
) -> None:
    ai_verdict("NOT_MATCH")
    ctx = await _bootstrap_org_with_open_job(client, _slug("confirm"))
    await apply_publicly(client, ctx["slug"], ctx["job_id"], email="oli@example.com")
    (application,) = await _applications(client, ctx)
    response = await client.post(
        f"/api/v1/recruiter/applications/{application['id']}/status",
        json={"to_status": "REJECTED"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"


async def test_override_is_role_gated_and_tenant_isolated(
    client: AsyncClient, super_admin: User, ai_verdict
) -> None:
    ai_verdict("NOT_MATCH")
    ctx = await _bootstrap_org_with_open_job(client, _slug("override-auth"))
    other = await _bootstrap_org_with_open_job(client, _slug("override-auth-other"))
    await apply_publicly(client, ctx["slug"], ctx["job_id"], email="pam@example.com")
    (application,) = await _applications(client, ctx)
    url = f"/api/v1/recruiter/applications/{application['id']}/ai-override"

    anonymous = await client.post(url, json={"reason": "x"})
    assert anonymous.status_code == 401
    interviewer = await _staff_headers(
        client, ctx["admin_headers"], ctx["slug"], "INTERVIEWER"
    )
    assert (await client.post(url, json={"reason": "x"}, headers=interviewer)).status_code == 403
    cross_tenant = await client.post(url, json={"reason": "x"}, headers=other["admin_headers"])
    assert cross_tenant.status_code == 404


# --- F. HR job matching ------------------------------------------------------------


async def test_hr_matches_candidate_to_other_jobs_and_history_is_preserved(
    client: AsyncClient, super_admin: User, ai_verdict, otp_settings
) -> None:
    ai_verdict("NOT_MATCH")
    otp_settings(email_otp_resend_cooldown_seconds=0)
    ctx = await _bootstrap_org_with_open_job(client, _slug("hr-match"))
    job_b = await _second_job(client, ctx, "Data Analyst")
    job_c = await _second_job(client, ctx, "QA Engineer")
    await apply_publicly(client, ctx["slug"], ctx["job_id"], email="quinn@example.com")
    (candidate,) = await _candidates(client, ctx)
    match_url = f"/api/v1/recruiter/candidates/{candidate['id']}/job-matches"

    for job_id in (job_b, job_c):
        matched = await client.post(
            match_url,
            json={"job_id": job_id, "reason": "Analytical profile fits this team."},
            headers=ctx["admin_headers"],
        )
        assert matched.status_code == 201, matched.text
        assert matched.json()["source"] == "HR_MATCH"
        assert matched.json()["candidate_id"] == candidate["id"]
        # The candidate's stored resume travels with the match.
        assert matched.json()["resume_id"] is not None
        download = await client.get(
            f"/api/v1/recruiter/applications/{matched.json()['id']}/resume",
            headers=ctx["admin_headers"],
        )
        assert download.status_code == 200

    duplicate = await client.post(match_url, json={"job_id": job_b}, headers=ctx["admin_headers"])
    assert duplicate.status_code == 409

    # Still one candidate; original application untouched.
    assert len(await _candidates(client, ctx)) == 1
    history = (
        await client.get(
            f"/api/v1/recruiter/candidates/{candidate['id']}/history",
            headers=ctx["admin_headers"],
        )
    ).json()
    by_source = {(a["source"], a["is_original"]) for a in history["applications"]}
    assert by_source == {("PORTAL", True), ("HR_MATCH", False)}
    original = next(a for a in history["applications"] if a["is_original"])
    assert original["status"] == "AI_SCREENED_OUT"
    assert [t["action"] for t in history["timeline"]].count("CANDIDATE_MATCHED_TO_JOB") == 2

    # The candidate cannot self-apply to job B (or anything) again: no code
    # is even sent.
    code_response, code = await request_code(client, ctx["slug"], "quinn@example.com")
    assert code_response.status_code == 409
    assert code_response.json()["error"]["code"] == "already_applied"
    assert code is None


async def test_candidate_history_requires_staff_permissions(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, _slug("history-auth"))
    other = await _bootstrap_org_with_open_job(client, _slug("history-auth-other"))
    await apply_publicly(client, ctx["slug"], ctx["job_id"], email="ray@example.com")
    (candidate,) = await _candidates(client, ctx)
    url = f"/api/v1/recruiter/candidates/{candidate['id']}/history"

    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers=other["admin_headers"])).status_code == 404


# --- Cross-tenant isolation ---------------------------------------------------------


async def test_verification_token_cannot_be_used_at_another_organization(
    client: AsyncClient, super_admin: User, ai_verdict
) -> None:
    """A token verified at organization A proves nothing to organization B —
    even for the very same email address."""
    ai_verdict("MATCH")
    org_a = await _bootstrap_org_with_open_job(client, _slug("token-tenant-a"))
    org_b = await _bootstrap_org_with_open_job(client, _slug("token-tenant-b"))
    token_from_a = await verify_email(client, org_a["slug"], "sam@example.com")

    at_b = await apply_publicly(
        client, org_b["slug"], org_b["job_id"], email="sam@example.com", token=token_from_a
    )
    assert at_b.status_code == 422, at_b.text
    assert at_b.json()["error"]["code"] == "email_not_verified"
    assert await _candidates(client, org_b) == []
    assert await _applications(client, org_b) == []

    # The rejected attempt didn't spend the token at its own organization.
    at_a = await apply_publicly(
        client, org_a["slug"], org_a["job_id"], email="sam@example.com", token=token_from_a
    )
    assert at_a.status_code == 201, at_a.text


async def test_candidate_history_is_not_readable_across_organizations(
    client: AsyncClient, super_admin: User
) -> None:
    org_a = await _bootstrap_org_with_open_job(client, _slug("history-tenant-a"))
    org_b = await _bootstrap_org_with_open_job(client, _slug("history-tenant-b"))
    await apply_publicly(client, org_a["slug"], org_a["job_id"], email="tia@example.com")
    (candidate,) = await _candidates(client, org_a)
    url = f"/api/v1/recruiter/candidates/{candidate['id']}/history"

    cross_tenant = await client.get(url, headers=org_b["admin_headers"])
    assert cross_tenant.status_code == 404
    # Indistinguishable from a nonexistent id, and nothing about the person leaks.
    assert "tia@example.com" not in cross_tenant.text
    assert candidate["full_name"] not in cross_tenant.text
    assert (await client.get(url, headers=org_a["admin_headers"])).status_code == 200


async def test_hr_match_is_rejected_across_organizations(
    client: AsyncClient, super_admin: User, ai_verdict
) -> None:
    ai_verdict("MATCH")
    org_a = await _bootstrap_org_with_open_job(client, _slug("match-tenant-a"))
    org_b = await _bootstrap_org_with_open_job(client, _slug("match-tenant-b"))
    await apply_publicly(client, org_a["slug"], org_a["job_id"], email="uma@example.com")
    (candidate_a,) = await _candidates(client, org_a)
    match_url = f"/api/v1/recruiter/candidates/{candidate_a['id']}/job-matches"

    # B's HR can't pull A's candidate into B's job...
    pulled = await client.post(
        match_url, json={"job_id": org_b["job_id"]}, headers=org_b["admin_headers"]
    )
    assert pulled.status_code == 404, pulled.text
    # ...and A's HR can't push A's candidate into B's job.
    pushed = await client.post(
        match_url, json={"job_id": org_b["job_id"]}, headers=org_a["admin_headers"]
    )
    assert pushed.status_code == 404, pushed.text

    assert await _applications(client, org_b) == []
    assert await _candidates(client, org_b) == []
    (only_application,) = await _applications(client, org_a)
    assert only_application["source"] != "HR_MATCH"


# --- G. organization contact configuration ------------------------------------------


async def test_careers_contact_is_configured_by_super_admin_and_published(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, _slug("contact"))

    public = await client.get(f"/api/v1/public/organizations/{ctx['slug']}")
    assert public.status_code == 200
    assert public.json()["careers_contact_email"] is None

    updated = await _set_contact(client, ctx["slug"], "  Careers@Intake-Contact.dev ")
    assert updated["careers_contact_email"] == "careers@intake-contact.dev"

    public = (await client.get(f"/api/v1/public/organizations/{ctx['slug']}")).json()
    assert public["careers_contact_email"] == "careers@intake-contact.dev"
    job = (
        await client.get(f"/api/v1/public/organizations/{ctx['slug']}/jobs/{ctx['job_id']}")
    ).json()
    assert job["organization"]["careers_contact_email"] == "careers@intake-contact.dev"

    # An organization admin can't change platform-managed settings.
    orgs_as_admin = await client.patch(
        f"/api/v1/admin/organizations/{updated['id']}",
        json={"careers_contact_email": "attacker@example.com"},
        headers=ctx["admin_headers"],
    )
    assert orgs_as_admin.status_code == 403

    invalid = await client.patch(
        f"/api/v1/admin/organizations/{updated['id']}",
        json={"careers_contact_email": "not-an-email"},
        headers={
            "Authorization": "Bearer "
            + (await login(client, email=SUPER_ADMIN_EMAIL, password=SUPER_ADMIN_PASSWORD))[
                "access_token"
            ]
        },
    )
    assert invalid.status_code == 422


# --- H. Campus drive links -------------------------------------------------------


async def _active_drive(
    client: AsyncClient, ctx: dict, *, default_assessment_id: str | None = None
) -> tuple[dict, str]:
    payload: dict[str, Any] = {
        "name": "Campus Drive",
        "job_id": ctx["job_id"],
        "college_name": "IIT",
    }
    if default_assessment_id:
        payload["default_assessment_id"] = default_assessment_id
    created = await client.post(
        "/api/v1/recruiter/campus-drives", json=payload, headers=ctx["admin_headers"]
    )
    assert created.status_code == 201, created.text
    drive = created.json()
    activated = await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["admin_headers"],
    )
    assert activated.status_code == 200, activated.text
    return drive, drive["application_link"].rsplit("/", 1)[-1]


async def test_campus_drive_requires_a_verified_email_and_the_full_form(
    client: AsyncClient, super_admin: User, ai_verdict
) -> None:
    ai_verdict("MATCH")
    ctx = await _bootstrap_org_with_open_job(client, _slug("campus-verify"))
    _drive, token = await _active_drive(client, ctx)

    unverified = await apply_to_campus_drive(client, token, email="raj@example.com", token="forged")
    assert unverified.status_code == 422
    assert unverified.json()["error"]["code"] == "email_not_verified"

    incomplete = await apply_to_campus_drive(
        client, token, email="raj@example.com", place_of_birth=None, languages=None
    )
    assert incomplete.status_code == 422
    assert await _candidates(client, ctx) == []


async def test_one_application_per_person_across_careers_site_and_campus_drive(
    client: AsyncClient, super_admin: User, ai_verdict, otp_settings
) -> None:
    ai_verdict("MATCH")
    otp_settings(email_otp_resend_cooldown_seconds=0)
    ctx = await _bootstrap_org_with_open_job(client, _slug("campus-dup"))
    _drive, token = await _active_drive(client, ctx)

    first = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="kim@example.com")
    assert first.status_code == 201, first.text

    # Same email through the drive link: stopped before any code is sent,
    # with nothing but "You have already applied." Only HR granting reapply
    # permission lets them apply again.
    code_response, code = await request_campus_code(client, token, "kim@example.com")
    assert code_response.status_code == 409, code_response.text
    assert code_response.json()["error"] == {
        "code": "already_applied",
        "message": "You have already applied.",
        "request_id": code_response.json()["error"]["request_id"],
    }
    assert code is None

    # A different email with the same mobile number (differently formatted).
    same_mobile = await apply_to_campus_drive(
        client,
        token,
        email="kim.other@example.com",
        phone=phone_for("kim@example.com").replace("+91", "0"),
    )
    assert same_mobile.status_code == 409
    assert same_mobile.json()["error"]["code"] == "phone_already_registered"

    assert len(await _candidates(client, ctx)) == 1
    assert len(await _applications(client, ctx)) == 1


async def test_campus_application_is_screened_welcomed_and_tied_to_the_drive(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider, ai_verdict
) -> None:
    ai_verdict("MATCH")
    ctx = await _bootstrap_org_with_open_job(client, _slug("campus-match"))
    drive, token = await _active_drive(client, ctx)

    response = await apply_to_campus_drive(client, token, email="ria@example.com")
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == CAMPUS_CONFIRMATION_FIELDS
    assert body["confirmation_email_sent"] is True

    (application,) = await _applications(client, ctx)
    assert application["status"] == "APPLIED"
    assert application["source"] == "CAMPUS_IMPORT"
    assert application["campus_drive_id"] == drive["id"]
    (candidate,) = await _candidates(client, ctx)
    assert candidate["email_verified_at"] is not None
    assert candidate["place_of_birth"] == "Chennai"
    (welcome,) = recording_email.sent
    assert welcome.subject.startswith("Welcome to Acme Corp")


async def test_campus_ai_screen_out_is_retained_welcomed_and_not_fast_tracked(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider, ai_verdict
) -> None:
    ai_verdict("NOT_MATCH")
    ctx = await _bootstrap_org_with_open_job(client, _slug("campus-not-match"))
    assessment = await client.post(
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
        headers=ctx["admin_headers"],
    )
    assert assessment.status_code == 201, assessment.text
    _drive, token = await _active_drive(
        client, ctx, default_assessment_id=assessment.json()["id"]
    )

    response = await apply_to_campus_drive(client, token, email="sam@example.com")
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == CAMPUS_CONFIRMATION_FIELDS
    assert "SHORTLISTED" not in response.text and "MATCH" not in response.text
    assert body["assessment_invitation_link"] is None
    assert body["confirmation_email_sent"] is True
    assert "AI_SCREENED_OUT" not in response.text

    (application,) = await _applications(client, ctx)
    assert application["status"] == "AI_SCREENED_OUT"
    (acknowledgement,) = recording_email.sent
    assert "not eligible for this particular position" in acknowledgement.text
