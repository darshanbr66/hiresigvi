"""Manual-only candidate email: nothing sends automatically, and a send
happens only through an explicit compose -> preview -> send action by an
authorized user of the same organization."""

import smtplib
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations import email as email_module
from app.integrations.email import EmailError
from app.models.user import User
from app.services import notification_service
from tests.conftest import RecordingEmailProvider, login
from tests.public_apply import apply_publicly, apply_to_campus_drive
from tests.test_assessments import (
    _ASSESSMENT_PAYLOAD,
    _bootstrap_org_with_screening_application,
    _extract_token,
    _invite_and_submit_first_attempt,
)
from tests.test_campus_drives import _bootstrap_org_with_job, _create_drive
from tests.test_public_applications import _bootstrap_org_with_open_job

_DUMMY_SMTP_PASSWORD = "dummy-test-password-not-real"


# --- helpers --------------------------------------------------------------


def _email_url(application_id: str, action: str) -> str:
    return f"/api/v1/recruiter/applications/{application_id}/email/{action}"


async def _public_apply(client: AsyncClient, ctx: dict, email: str = "jane@example.com") -> str:
    """Applies with the automatic welcome email captured separately, so these
    manual-email tests see only what a recruiter sends (the welcome email
    itself is covered by test_candidate_application_sends_only_the_welcome_email
    and tests/test_candidate_intake.py)."""
    with patch.object(notification_service, "get_email_provider", RecordingEmailProvider):
        response = await apply_publicly(client, ctx["slug"], ctx["job_id"], email=email)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _staff_headers(client: AsyncClient, admin_headers: dict, slug: str, role: str) -> dict:
    email = f"{role.lower()}@{slug}.dev"
    created = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": email,
            "full_name": f"{role.title()} User",
            "password": "StaffPass12345",
            "role": role,
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    tokens = await login(client, email=email, password="StaffPass12345")
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _compose(
    client: AsyncClient, headers: dict, application_id: str, key: str, variables=None
):
    return await client.post(
        _email_url(application_id, "compose"),
        json={"template_key": key, "variables": variables or {}},
        headers=headers,
    )


def _draft(composed: dict, **overrides) -> dict:
    return {
        "template_key": composed["template_key"],
        "subject": composed["subject"],
        "body": composed["body"],
        "variables": composed["variables"],
        **overrides,
    }


async def _assigned_assessment(client: AsyncClient, slug: str) -> dict:
    ctx = await _bootstrap_org_with_screening_application(client, slug)
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    invitation = await client.post(
        "/api/v1/recruiter/assessments/invite",
        json={"assessment_id": assessment["id"], "application_id": ctx["application_id"]},
        headers=ctx["headers"],
    )
    assert invitation.status_code == 201, invitation.text
    return {**ctx, "assessment": assessment, "invitation": invitation.json()}


# --- 1-3. nothing is sent automatically -----------------------------------


async def test_candidate_application_sends_only_the_welcome_email(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    """First HR meeting: the one automatic candidate email is the welcome
    email on a successful self-service application — nothing else (no
    "application received" template, no status email)."""
    ctx = await _bootstrap_org_with_open_job(client, "wf-apply-no-email")

    response = await apply_publicly(client, ctx["slug"], ctx["job_id"], email="jane@example.com")
    assert response.status_code == 201, response.text

    (welcome,) = recording_email.sent
    assert welcome.to == ["jane@example.com"]
    assert welcome.subject.startswith("Welcome to Acme Corp")


async def test_campus_drive_application_sends_only_the_welcome_email(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    """A campus drive link is the same self-service flow as the careers
    site: the automatic welcome email is the only email it sends."""
    ctx = await _bootstrap_org_with_job(client, "wf-campus-no-email")
    drive = await _create_drive(client, ctx)
    await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}",
        json={"status": "ACTIVE"},
        headers=ctx["headers"],
    )
    token = drive["application_link"].rsplit("/", 1)[-1]

    response = await apply_to_campus_drive(client, token, email="priya@example.com")

    assert response.status_code == 201, response.text
    (welcome,) = recording_email.sent
    assert welcome.to == ["priya@example.com"]
    assert welcome.subject.startswith("Welcome to Acme Corp")


async def test_assigning_and_retesting_an_assessment_do_not_send_email(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "wf-assess-no-email")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()

    invite = await client.post(
        "/api/v1/recruiter/assessments/invite",
        json={"assessment_id": assessment["id"], "application_id": ctx["application_id"]},
        headers=ctx["headers"],
    )
    assert invite.status_code == 201, invite.text
    # Prepared, not emailed.
    assert invite.json()["emailed_at"] is None
    assert recording_email.sent == []

    # Submit the first attempt, then authorize a retest — also only prepared.
    token = _extract_token(invite.json()["invitation_link"])
    view = (await client.get(f"/api/v1/public/assessment/{token}")).json()
    await client.post(f"/api/v1/public/assessment/{token}/start")
    await client.post(
        f"/api/v1/public/assessment/{token}/submit",
        json={
            "answers": [
                {"question_id": view["questions"][0]["id"], "selected_option_ids": []},
                {"question_id": view["questions"][1]["id"], "selected_option_ids": []},
            ]
        },
    )
    retest = await client.post(
        "/api/v1/recruiter/assessments/retest",
        json={"application_id": ctx["application_id"], "reason": "Network interruption."},
        headers=ctx["headers"],
    )
    assert retest.status_code == 201, retest.text
    assert retest.json()["emailed_at"] is None
    assert recording_email.sent == []


async def test_status_changes_do_not_send_email(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "wf-status-no-email")
    selected_id = await _public_apply(client, ctx, "selected@example.com")
    rejected_id = await _public_apply(client, ctx, "rejected@example.com")

    for to_status in ("UNDER_REVIEW", "SCREENING", "SHORTLISTED", "INTERVIEW", "SELECTED"):
        response = await client.post(
            f"/api/v1/recruiter/applications/{selected_id}/status",
            json={"to_status": to_status},
            headers=ctx["admin_headers"],
        )
        assert response.status_code == 200, response.text
    rejected = await client.post(
        f"/api/v1/recruiter/applications/{rejected_id}/status",
        json={"to_status": "REJECTED", "reason": "Not a fit."},
        headers=ctx["admin_headers"],
    )
    assert rejected.status_code == 200, rejected.text

    assert recording_email.sent == []


# --- 13. templates --------------------------------------------------------


async def test_template_listing_exposes_every_template_and_their_fields(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "wf-template-list")

    response = await client.get("/api/v1/recruiter/email-templates", headers=ctx["admin_headers"])

    assert response.status_code == 200, response.text
    templates = {t["key"]: t for t in response.json()}
    assert set(templates) == {
        "APPLICATION_RECEIVED",
        "INTERVIEW_INVITATION",
        "ASSESSMENT_INVITATION",
        "NEXT_STEPS",
        "REJECTION",
        "SCHOLARSHIP_UPDATE",
        "TRAINING_ENROLLMENT",
        "GENERAL",
    }
    interview_fields = {f["key"]: f for f in templates["INTERVIEW_INVITATION"]["fields"]}
    assert {"interview_date", "interview_time", "interview_mode", "meeting_link"} <= set(
        interview_fields
    )
    assert interview_fields["interview_date"]["required"] is True
    assert interview_fields["meeting_link"]["required"] is False
    assert interview_fields["interview_mode"]["options"]


# --- 4. authorized users can send; 7. placeholders are populated ----------


async def test_admin_can_compose_preview_and_send_application_received(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "wf-happy-path")
    application_id = await _public_apply(client, ctx)
    headers = ctx["admin_headers"]

    # Applying sent nothing; the recruiter now reviews and sends manually.
    assert recording_email.sent == []

    composed = (await _compose(client, headers, application_id, "APPLICATION_RECEIVED")).json()
    assert composed["subject"] == "Application Received – Senior Backend Engineer"
    assert composed["missing_required"] == []
    body = composed["body"]
    # Known details are filled in automatically...
    assert "Dear Jane Candidate," in body
    assert "Senior Backend Engineer" in body
    assert "Acme Corp" in body
    assert "Acme Admin" in body  # signed-in recruiter
    assert "{{" not in composed["subject"] + body

    preview = await client.post(
        _email_url(application_id, "preview"), json=_draft(composed), headers=headers
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["to"] == "jane@example.com"
    assert preview_body["reply_to"] == "admin@wf-happy-path.dev"
    assert preview_body["html"].startswith("<!DOCTYPE html>")
    assert "Acme Corp" in preview_body["html"]
    assert recording_email.sent == []  # preview never sends

    sent = await client.post(
        _email_url(application_id, "send"), json=_draft(composed), headers=headers
    )
    assert sent.status_code == 200, sent.text
    assert sent.json() == {
        "sent": True,
        "to": "jane@example.com",
        "subject": "Application Received – Senior Backend Engineer",
    }
    (email,) = recording_email.sent
    assert email.to == ["jane@example.com"]
    assert email.reply_to == "admin@wf-happy-path.dev"
    assert email.html.startswith("<!DOCTYPE html>")
    assert "Dear Jane Candidate," in email.text

    activities = (await client.get("/api/v1/recruiter/activities", headers=headers)).json()
    assert any(
        a["action"] == "CANDIDATE_EMAIL_SENT" and a["entity_id"] == application_id
        for a in activities
    )


async def test_recruiter_role_can_send_and_edits_apply_only_to_that_email(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "wf-recruiter-edit")
    application_id = await _public_apply(client, ctx)
    recruiter = await _staff_headers(client, ctx["admin_headers"], "wf-recruiter-edit", "RECRUITER")

    composed = (await _compose(client, recruiter, application_id, "GENERAL")).json()
    edited = _draft(
        composed,
        subject="Quick question about your availability",
        body=composed["body"] + "\n\nAre you free for a short call this week?",
    )
    sent = await client.post(_email_url(application_id, "send"), json=edited, headers=recruiter)
    assert sent.status_code == 200, sent.text

    (email,) = recording_email.sent
    assert email.subject == "Quick question about your availability"
    assert "Are you free for a short call this week?" in email.text
    assert email.reply_to == "recruiter@wf-recruiter-edit.dev"

    # The predefined template itself is untouched by the edit.
    again = (await _compose(client, recruiter, application_id, "GENERAL")).json()
    assert (
        again["subject"]
        == composed["subject"]
        == "A message from Acme Corp"
    )
    assert "Are you free" not in again["body"]


# --- 5. unauthorized users cannot send -------------------------------------


async def test_unauthorized_users_cannot_compose_preview_or_send(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "wf-unauthorized")
    application_id = await _public_apply(client, ctx)
    composed = (await _compose(client, ctx["admin_headers"], application_id, "GENERAL")).json()
    draft = _draft(composed)

    for role in ("INTERVIEWER", "HIRING_MANAGER"):
        headers = await _staff_headers(client, ctx["admin_headers"], "wf-unauthorized", role)
        for action in ("compose", "preview", "send"):
            body = {"template_key": "GENERAL", "variables": {}} if action == "compose" else draft
            response = await client.post(
                _email_url(application_id, action), json=body, headers=headers
            )
            assert response.status_code == 403, (role, action, response.text)
        assert (
            await client.get("/api/v1/recruiter/email-templates", headers=headers)
        ).status_code == 403

    # No credentials at all.
    anonymous = await client.post(_email_url(application_id, "send"), json=draft)
    assert anonymous.status_code == 401
    assert recording_email.sent == []


# --- 6. tenant isolation ----------------------------------------------------


async def test_another_organizations_users_cannot_email_this_organizations_candidate(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    ctx_a = await _bootstrap_org_with_open_job(client, "wf-tenant-a")
    ctx_b = await _bootstrap_org_with_open_job(client, "wf-tenant-b")
    application_a = await _public_apply(client, ctx_a)
    composed = (await _compose(client, ctx_a["admin_headers"], application_a, "GENERAL")).json()
    draft = _draft(composed)

    compose = await _compose(client, ctx_b["admin_headers"], application_a, "GENERAL")
    preview = await client.post(
        _email_url(application_a, "preview"), json=draft, headers=ctx_b["admin_headers"]
    )
    send = await client.post(
        _email_url(application_a, "send"), json=draft, headers=ctx_b["admin_headers"]
    )

    # Indistinguishable from an application that doesn't exist.
    assert (compose.status_code, preview.status_code, send.status_code) == (404, 404, 404)
    assert recording_email.sent == []


# --- 8. optional placeholders; 9. HTML --------------------------------------


async def test_interview_invitation_handles_optional_and_required_details(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "wf-interview")
    application_id = await _public_apply(client, ctx)
    headers = ctx["admin_headers"]

    # Nothing filled in yet: the required placeholders stay visible for the
    # recruiter, are reported, and sending is refused rather than emailing
    # a raw {{placeholder}}.
    empty = (await _compose(client, headers, application_id, "INTERVIEW_INVITATION")).json()
    assert set(empty["missing_required"]) == {"interview_date", "interview_time", "interview_mode"}
    assert "{{interview_date}}" in empty["body"]
    blocked = await client.post(
        _email_url(application_id, "send"), json=_draft(empty), headers=headers
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "unresolved_placeholders"
    assert "{{interview_date}}" in blocked.json()["error"]["message"]
    assert recording_email.sent == []

    # Required details supplied, optional ones (location, link, round,
    # instructions) left out: clean email, no dangling labels.
    filled = (
        await _compose(
            client,
            headers,
            application_id,
            "INTERVIEW_INVITATION",
            {
                "interview_date": "2026-09-25",
                "interview_time": "14:30",
                "interview_mode": "Phone call",
            },
        )
    ).json()
    assert filled["missing_required"] == []
    assert "Date: Friday, 25 September 2026" in filled["body"]
    assert "Time: 14:30" in filled["body"]
    for absent in ("{{", "Location:", "Meeting link:", "Round:"):
        assert absent not in filled["body"]

    sent = await client.post(
        _email_url(application_id, "send"), json=_draft(filled), headers=headers
    )
    assert sent.status_code == 200, sent.text
    (email,) = recording_email.sent
    assert "{{" not in email.html and "{{" not in email.text
    assert "Meeting link" not in email.html  # no button without a link
    assert "Join the interview" not in email.html


async def test_interview_invitation_with_a_meeting_link_gets_a_button(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "wf-interview-link")
    application_id = await _public_apply(client, ctx)
    variables = {
        "interview_round": "Technical round 1",
        "interview_date": "2026-09-25",
        "interview_time": "14:30",
        "interview_mode": "Video call",
        "meeting_link": "https://meet.example.com/abc",
        "additional_instructions": "Please keep your ID handy.",
    }
    composed = (
        await _compose(
            client, ctx["admin_headers"], application_id, "INTERVIEW_INVITATION", variables
        )
    ).json()

    preview = (
        await client.post(
            _email_url(application_id, "preview"),
            json=_draft(composed),
            headers=ctx["admin_headers"],
        )
    ).json()

    assert preview["has_call_to_action"] is True
    assert 'href="https://meet.example.com/abc"' in preview["html"]
    assert "Join the interview" in preview["html"]
    assert "Technical round 1" in preview["html"]
    assert "Please keep your ID handy." in preview["html"]


async def test_invalid_template_fields_and_unknown_placeholders_are_rejected(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "wf-validation")
    application_id = await _public_apply(client, ctx)
    headers = ctx["admin_headers"]

    bad_link = await _compose(
        client,
        headers,
        application_id,
        "INTERVIEW_INVITATION",
        {"meeting_link": "javascript:alert(1)"},
    )
    bad_mode = await _compose(
        client, headers, application_id, "INTERVIEW_INVITATION", {"interview_mode": "Telepathy"}
    )
    server_field = await _compose(
        client, headers, application_id, "GENERAL", {"company_name": "Evil Corp"}
    )
    assert (bad_link.status_code, bad_mode.status_code, server_field.status_code) == (422, 422, 422)

    composed = (await _compose(client, headers, application_id, "GENERAL")).json()
    unknown = await client.post(
        _email_url(application_id, "send"),
        json=_draft(composed, body=composed["body"] + "\n\nYour offer: {{salary_offer}}"),
        headers=headers,
    )
    assert unknown.status_code == 422
    assert "{{salary_offer}}" in unknown.json()["error"]["message"]
    assert recording_email.sent == []


async def test_html_email_escapes_candidate_supplied_text(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "wf-escape")
    with patch.object(notification_service, "get_email_provider", RecordingEmailProvider):
        response = await apply_publicly(
            client,
            ctx["slug"],
            ctx["job_id"],
            email="mallory@example.com",
            full_name="<script>alert(1)</script> Mallory",
        )
    application_id = response.json()["id"]
    headers = ctx["admin_headers"]

    composed = (await _compose(client, headers, application_id, "APPLICATION_RECEIVED")).json()
    await client.post(_email_url(application_id, "send"), json=_draft(composed), headers=headers)

    (email,) = recording_email.sent
    assert "<script" not in email.html
    assert "&lt;script&gt;" in email.html


# --- 10. SMTP failure is reported honestly ----------------------------------


async def test_provider_failure_returns_an_honest_error_and_is_audited(
    client: AsyncClient, super_admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import notification_service

    provider = RecordingEmailProvider(error=EmailError("Could not deliver the email."))
    monkeypatch.setattr(notification_service, "get_email_provider", lambda: provider)
    ctx = await _bootstrap_org_with_open_job(client, "wf-failure")
    application_id = await _public_apply(client, ctx)
    headers = ctx["admin_headers"]
    composed = (await _compose(client, headers, application_id, "REJECTION")).json()

    response = await client.post(
        _email_url(application_id, "send"), json=_draft(composed), headers=headers
    )

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "email_delivery_failed"
    activities = (await client.get("/api/v1/recruiter/activities", headers=headers)).json()
    assert any(
        a["action"] == "CANDIDATE_EMAIL_FAILED" and a["entity_id"] == application_id
        for a in activities
    )
    assert not any(a["action"] == "CANDIDATE_EMAIL_SENT" for a in activities)


async def test_unconfigured_email_returns_a_configuration_error(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "wf-unconfigured")
    application_id = await _public_apply(client, ctx)
    headers = ctx["admin_headers"]
    composed = (await _compose(client, headers, application_id, "GENERAL")).json()

    response = await client.post(
        _email_url(application_id, "send"), json=_draft(composed), headers=headers
    )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "email_not_configured"


# --- 11. the SMTP password is never exposed ---------------------------------


async def test_smtp_password_never_appears_in_any_response_or_log(
    client: AsyncClient,
    super_admin: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost/db",
        jwt_secret_key="x" * 32,
        smtp_host="smtp.example.test",
        smtp_username="sender@example.test",
        smtp_password=SecretStr(_DUMMY_SMTP_PASSWORD),
        smtp_from_email="sender@example.test",
    )
    monkeypatch.setattr(email_module, "get_settings", lambda: settings)

    class _RejectingSmtp:
        def __init__(self, *args: object, **kwargs: object) -> None: ...
        def __enter__(self) -> "_RejectingSmtp":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def starttls(self, **kwargs: object) -> None: ...
        def login(self, username: str, password: str) -> None:
            raise smtplib.SMTPAuthenticationError(535, f"bad credentials {password}".encode())

    monkeypatch.setattr(smtplib, "SMTP", _RejectingSmtp)

    ctx = await _bootstrap_org_with_open_job(client, "wf-password")
    application_id = await _public_apply(client, ctx)
    headers = ctx["admin_headers"]
    composed = await _compose(client, headers, application_id, "GENERAL")
    templates = await client.get("/api/v1/recruiter/email-templates", headers=headers)
    preview = await client.post(
        _email_url(application_id, "preview"), json=_draft(composed.json()), headers=headers
    )
    failed_send = await client.post(
        _email_url(application_id, "send"), json=_draft(composed.json()), headers=headers
    )

    assert failed_send.status_code == 502
    for response in (composed, templates, preview, failed_send):
        assert _DUMMY_SMTP_PASSWORD not in response.text
    assert _DUMMY_SMTP_PASSWORD not in caplog.text
    assert _DUMMY_SMTP_PASSWORD not in repr(settings)
    # SMTP configuration is not part of any response body at all.
    assert "smtp" not in templates.text.lower()


# --- assessment invitation: prepare, then explicitly send -------------------


async def test_assessment_invitation_is_prepared_then_sent_manually_with_a_fresh_link(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    ctx = await _assigned_assessment(client, "wf-assess-send")
    headers, application_id = ctx["headers"], ctx["application_id"]
    old_token = _extract_token(ctx["invitation"]["invitation_link"])
    assert recording_email.sent == []

    composed = (
        await _compose(
            client,
            headers,
            application_id,
            "ASSESSMENT_INVITATION",
            {"assessment_instructions": "Use a laptop, not a phone."},
        )
    ).json()
    assert composed["subject"] == "Assessment Invitation – Backend Engineer"
    assert "Assessment: Python Basics" in composed["body"]
    assert "Complete by:" in composed["body"]
    assert "{{" not in composed["body"]

    preview = (
        await client.post(
            _email_url(application_id, "preview"), json=_draft(composed), headers=headers
        )
    ).json()
    assert preview["has_call_to_action"] is True
    assert "Start the assessment" in preview["html"]
    assert recording_email.sent == []
    # A preview must not disturb the link the recruiter already holds.
    assert (await client.get(f"/api/v1/public/assessment/{old_token}")).status_code == 200

    sent = await client.post(
        _email_url(application_id, "send"), json=_draft(composed), headers=headers
    )
    assert sent.status_code == 200, sent.text
    (email,) = recording_email.sent
    assert "Use a laptop, not a phone." in email.html

    # The emailed link is real and works; the previous one has been replaced.
    match = email.text.split("Start the assessment: ")[1].split()[0]
    new_token = match.rsplit("/", 1)[-1]
    assert new_token != old_token
    assert (await client.get(f"/api/v1/public/assessment/{new_token}")).status_code == 200
    assert (await client.get(f"/api/v1/public/assessment/{old_token}")).status_code != 200

    invitation = (
        await client.get(
            f"/api/v1/recruiter/applications/{application_id}/assessment", headers=headers
        )
    ).json()
    assert invitation["emailed_at"] is not None


async def test_failed_assessment_email_keeps_the_existing_link_working(
    client: AsyncClient, super_admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import notification_service

    provider = RecordingEmailProvider(error=EmailError("Could not deliver the email."))
    monkeypatch.setattr(notification_service, "get_email_provider", lambda: provider)
    ctx = await _assigned_assessment(client, "wf-assess-fail")
    headers, application_id = ctx["headers"], ctx["application_id"]
    old_token = _extract_token(ctx["invitation"]["invitation_link"])
    composed = (await _compose(client, headers, application_id, "ASSESSMENT_INVITATION")).json()

    response = await client.post(
        _email_url(application_id, "send"), json=_draft(composed), headers=headers
    )

    assert response.status_code == 502
    # Nothing was delivered, so the link must not have been rotated away.
    assert (await client.get(f"/api/v1/public/assessment/{old_token}")).status_code == 200
    invitation = (
        await client.get(
            f"/api/v1/recruiter/applications/{application_id}/assessment", headers=headers
        )
    ).json()
    assert invitation["emailed_at"] is None


async def test_assessment_invitation_requires_an_assigned_assessment(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "wf-assess-none")

    response = await _compose(
        client, ctx["headers"], ctx["application_id"], "ASSESSMENT_INVITATION"
    )

    assert response.status_code == 409
    assert "assign" in response.json()["error"]["message"].lower()
    assert recording_email.sent == []


async def test_a_started_assessment_attempt_cannot_be_re_emailed(
    client: AsyncClient, super_admin: User, recording_email: RecordingEmailProvider
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "wf-assess-started")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    await _invite_and_submit_first_attempt(client, ctx, assessment)

    response = await _compose(
        client, ctx["headers"], ctx["application_id"], "ASSESSMENT_INVITATION"
    )

    # Re-issuing the link would cut off a candidate who is mid-attempt or done.
    assert response.status_code == 409
    assert recording_email.sent == []
