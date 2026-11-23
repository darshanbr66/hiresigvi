"""Drives the real self-service application flow over HTTP for tests:
request an email code -> read it from the (captured) outgoing email ->
verify -> the progressive flow (draft, background, education, academic
document, resume + submit) for a careers-site job or a campus drive link.

The academic-document check runs with no education AI provider (the
helpers pin `UnconfiguredEducationAIProvider`, so a test that fakes a
*screening* upstream never has its fake consumed by education calls): the
check reports "not available" and the helpers continue through the real
manual-review path. The education AI itself is tested in test_apply_flow.py.

No shortcut into the database: the code is taken from the email the
application actually tried to send, via a recording provider swapped in only
for the duration of the code request (so it never pollutes a test's own
`recording_email` fixture).
"""

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from httpx import AsyncClient, Response

from app.integrations.ai.education_providers import UnconfiguredEducationAIProvider
from app.services import application_draft_service, notification_service
from app.services.education import job_qualification_service
from tests.conftest import RecordingEmailProvider, make_minimal_pdf

_CODE_IN_SUBJECT = re.compile(r"verification code: (\d{6})")

DEFAULT_RESUME_TEXT = (
    "Jane Candidate. Software engineer. Skills: Python, PostgreSQL, FastAPI, REST APIs, Git. "
    "4 years of backend development experience. B.Tech in Computer Science."
)


def phone_for(email: str) -> str:
    """A valid, deterministic Indian mobile number per email — distinct
    applicants in one test never collide on the unique mobile number."""
    digits = int(hashlib.sha256(email.lower().encode()).hexdigest(), 16) % 10**9
    return f"+919{digits:09d}"


def profile_form(email: str, *, full_name: str = "Jane Candidate", **overrides: Any) -> dict:
    form: dict[str, Any] = {
        "full_name": full_name,
        "email": email,
        "phone": phone_for(email),
        "date_of_birth": "1998-04-12",
        "place_of_birth": "Chennai",
        "languages": ["English", "Tamil"],
        "candidate_type": "FRESHER",
        "current_location": "Chennai",
        "preferred_location": "Bengaluru",
        "qualification": "B.Tech Computer Science",
        "linkedin_url": "https://www.linkedin.com/in/jane-candidate",
        "github_url": "https://github.com/jane-candidate",
    }
    form.update(overrides)
    return {key: value for key, value in form.items() if value is not None}


def _org_base(slug: str) -> str:
    return f"/api/v1/public/organizations/{slug}"


def _drive_base(drive_token: str) -> str:
    return f"/api/v1/public/campus-drive/{drive_token}"


async def request_code(client: AsyncClient, slug: str, email: str) -> tuple[Response, str | None]:
    return await _request_code_at(client, _org_base(slug), email)


async def request_campus_code(
    client: AsyncClient, drive_token: str, email: str
) -> tuple[Response, str | None]:
    return await _request_code_at(client, _drive_base(drive_token), email)


async def _request_code_at(
    client: AsyncClient, base: str, email: str
) -> tuple[Response, str | None]:
    recorder = RecordingEmailProvider()
    with patch.object(notification_service, "get_email_provider", lambda: recorder):
        response = await client.post(f"{base}/email-verification/request", json={"email": email})
    code = None
    if recorder.sent:
        match = _CODE_IN_SUBJECT.search(recorder.sent[-1].subject)
        code = match.group(1) if match else None
    return response, code


async def verify_email(client: AsyncClient, slug: str, email: str) -> str:
    return await _verify_email_at(client, _org_base(slug), email)


async def verify_campus_email(client: AsyncClient, drive_token: str, email: str) -> str:
    """The same one-time-code flow, through a campus drive link."""
    return await _verify_email_at(client, _drive_base(drive_token), email)


async def _verify_email_at(client: AsyncClient, base: str, email: str) -> str:
    response, code = await _request_code_at(client, base, email)
    assert response.status_code == 202, response.text
    assert code is not None
    verified = await client.post(
        f"{base}/email-verification/verify", json={"email": email, "code": code}
    )
    assert verified.status_code == 200, verified.text
    return str(verified.json()["verification_token"])


#: The form-field names `profile_form` uses, by the step that now takes them.
_BACKGROUND_FIELDS = {
    "date_of_birth": "date_of_birth",
    "place_of_birth": "place_of_birth",
    "languages": "languages",
    "candidate_type": "candidate_type",
    "current_location": "location",
    "preferred_location": "preferred_location",
    "linkedin_url": "linkedin_url",
    "github_url": "github_url",
    "years_experience": "years_experience",
    "notice_period_days": "notice_period_days",
    "immediate_joiner": "immediate_joiner",
    "current_title": "current_title",
    "current_company": "current_company",
}

DEFAULT_EDUCATION: dict[str, Any] = {
    "qualification_key": "ME_MTECH",
    "branch": "Computer Science",
    "institution": "Anna University",
    "completion_year": 2020,
}


def academic_document_pdf() -> tuple[str, bytes, str]:
    return (
        "mtech-marks-card.pdf",
        make_minimal_pdf("Anna University. Master of Technology, Computer Science. Marks card."),
        "application/pdf",
    )


async def complete_apply_flow(
    client: AsyncClient,
    base: str,
    *,
    email: str,
    token: str,
    full_name: str,
    resume: tuple[str, bytes, str],
    marks_card: tuple[str, bytes, str] | None = None,
    education: dict[str, Any] | None = None,
    isolate_welcome_email: bool = False,
    **overrides: Any,
) -> Response:
    """Steps 3-6 for an already-verified email. Returns the first response
    that isn't a success (so a test can assert on a refused step), or the
    submission's. `overrides` use the old form-field names (None drops one)."""
    form = profile_form(email, full_name=full_name, **overrides)
    auth = {"email": email, "email_verification_token": token}
    started = await client.post(
        f"{base}/apply/draft",
        json={**auth, "full_name": form.get("full_name", ""), "phone": form.get("phone", "")},
    )
    if started.status_code != 200:
        return started
    background = {
        target: form[source] for source, target in _BACKGROUND_FIELDS.items() if source in form
    }
    saved = await client.put(
        f"{base}/apply/draft/background", json={**auth, "background": background}
    )
    if saved.status_code != 200:
        return saved
    details = dict(education or DEFAULT_EDUCATION)
    if "tenth_marks_percentage" in form:
        details["tenth_marks_percentage"] = form["tenth_marks_percentage"]
    with _no_education_ai():
        saved = await client.put(
            f"{base}/apply/draft/education", json={**auth, "education": details}
        )
    if saved.status_code != 200:
        return saved
    with _no_education_ai():
        uploaded = await client.post(
            f"{base}/apply/draft/education/document",
            data=auth,
            files={"document": academic_document_pdf()},
        )
    if uploaded.status_code != 200:
        return uploaded
    if not uploaded.json()["education_check"]["can_continue"]:
        reviewed = await client.post(f"{base}/apply/draft/education/manual-review", json=auth)
        if reviewed.status_code != 200:
            return reviewed
    files: dict[str, tuple[str, bytes, str]] = {"resume": resume}
    if marks_card is not None:
        files["tenth_marks_card"] = marks_card
    with _no_education_ai():
        if isolate_welcome_email:
            with patch.object(notification_service, "get_email_provider", RecordingEmailProvider):
                return await client.post(f"{base}/apply", data=auth, files=files)
        return await client.post(f"{base}/apply", data=auth, files=files)


@contextmanager
def _no_education_ai() -> Iterator[None]:
    unconfigured = UnconfiguredEducationAIProvider()
    with (
        patch.object(application_draft_service, "get_education_ai_provider", lambda: unconfigured),
        patch.object(job_qualification_service, "get_education_ai_provider", lambda: unconfigured),
    ):
        yield


async def apply_publicly(
    client: AsyncClient,
    slug: str,
    job_id: str,
    *,
    email: str,
    full_name: str = "Jane Candidate",
    resume_text: str = DEFAULT_RESUME_TEXT,
    resume: tuple[str, bytes, str] | None = None,
    token: str | None = None,
    isolate_welcome_email: bool = False,
    marks_card: tuple[str, bytes, str] | None = None,
    education: dict[str, Any] | None = None,
    **overrides: Any,
) -> Response:
    """Verifies `email` (unless a `token` is given) and completes the whole
    progressive flow for a careers-site job. `overrides` replace/add form
    fields (None drops one). `marks_card` attaches the optional 10th marks
    card. `isolate_welcome_email` sends the automatic welcome email to a
    throwaway recorder, for tests that assert on the emails *they* trigger
    later."""
    if token is None:
        token = await verify_email(client, slug, email)
    return await complete_apply_flow(
        client,
        f"{_org_base(slug)}/jobs/{job_id}",
        email=email,
        token=token,
        full_name=full_name,
        resume=resume or ("resume.pdf", make_minimal_pdf(resume_text), "application/pdf"),
        marks_card=marks_card,
        education=education,
        isolate_welcome_email=isolate_welcome_email,
        **overrides,
    )


async def apply_to_campus_drive(
    client: AsyncClient,
    drive_token: str,
    *,
    email: str,
    full_name: str = "Priya Candidate",
    resume_text: str = DEFAULT_RESUME_TEXT,
    token: str | None = None,
    marks_card: tuple[str, bytes, str] | None = None,
    education: dict[str, Any] | None = None,
    **overrides: Any,
) -> Response:
    """The campus drive link's version of `apply_publicly`: same verified
    email, same progressive flow."""
    if token is None:
        token = await verify_campus_email(client, drive_token, email)
    return await complete_apply_flow(
        client,
        _drive_base(drive_token),
        email=email,
        token=token,
        full_name=full_name,
        resume=("resume.pdf", make_minimal_pdf(resume_text), "application/pdf"),
        marks_card=marks_card,
        education=education,
        **overrides,
    )
