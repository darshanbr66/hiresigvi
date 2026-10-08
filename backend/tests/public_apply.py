"""Drives the real self-service application flow over HTTP for tests:
request an email code -> read it from the (captured) outgoing email ->
verify -> submit the full, mandatory application form.

No shortcut into the database: the code is taken from the email the
application actually tried to send, via a recording provider swapped in only
for the duration of the code request (so it never pollutes a test's own
`recording_email` fixture).
"""

import hashlib
import re
from typing import Any
from unittest.mock import patch

from httpx import AsyncClient, Response

from app.services import notification_service
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
    **overrides: Any,
) -> Response:
    """Verifies `email` (unless a `token` is given) and submits a complete
    application. `overrides` replace/add form fields (None drops one).
    `marks_card` attaches the optional 10th marks card upload.
    `isolate_welcome_email` sends the automatic welcome email to a throwaway
    recorder, for tests that assert on the emails *they* trigger later."""
    if token is None:
        token = await verify_email(client, slug, email)
    data = profile_form(email, full_name=full_name, **overrides)
    data["email_verification_token"] = token
    file = resume or ("resume.pdf", make_minimal_pdf(resume_text), "application/pdf")
    files: dict[str, tuple[str, bytes, str]] = {"resume": file}
    if marks_card is not None:
        files["tenth_marks_card"] = marks_card
    url = f"/api/v1/public/organizations/{slug}/jobs/{job_id}/apply"
    if isolate_welcome_email:
        with patch.object(notification_service, "get_email_provider", RecordingEmailProvider):
            return await client.post(url, data=data, files=files)
    return await client.post(url, data=data, files=files)


async def apply_to_campus_drive(
    client: AsyncClient,
    drive_token: str,
    *,
    email: str,
    full_name: str = "Priya Candidate",
    resume_text: str = DEFAULT_RESUME_TEXT,
    token: str | None = None,
    marks_card: tuple[str, bytes, str] | None = None,
    **overrides: Any,
) -> Response:
    """The campus drive link's version of `apply_publicly`: same verified
    email, same complete form."""
    if token is None:
        token = await verify_campus_email(client, drive_token, email)
    data = profile_form(email, full_name=full_name, **overrides)
    data["email_verification_token"] = token
    files: dict[str, tuple[str, bytes, str]] = {
        "resume": ("resume.pdf", make_minimal_pdf(resume_text), "application/pdf")
    }
    if marks_card is not None:
        files["tenth_marks_card"] = marks_card
    return await client.post(f"{_drive_base(drive_token)}/apply", data=data, files=files)
