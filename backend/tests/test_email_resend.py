"""Resend (HTTPS) email delivery — the production provider, since Render's free
web services block outbound SMTP ports — and provider selection.

The HTTP layer is faked with `httpx.MockTransport`: tests must never reach the
real Resend API or deliver mail (tests/conftest.py also blanks RESEND_API_KEY /
EMAIL_FROM from a local .env). The API key below is a test literal, not a real
credential.
"""

import json
import logging
from collections.abc import Callable

import httpx
import pytest

from app.core.config import Settings
from app.integrations import email as email_module
from app.integrations.email import (
    EmailError,
    ResendEmailProvider,
    SmtpEmailProvider,
    UnconfiguredEmailProvider,
    get_email_provider,
)
from app.integrations.email import resend_provider as resend_module
from app.services import notification_service

_DUMMY_KEY = "re_dummy_test_key_not_real"
_FROM = "Zodiac Patent Recruitment <hr@example.test>"
_SECRET_BODY = "CONFIDENTIAL-BODY-TEXT"


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def json(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


@pytest.fixture
def resend_api(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _Recorder]:
    """Routes the provider's HTTP client through a mock transport. Call with a
    `respond` callable (request -> Response) or an exception to raise."""
    real_client = httpx.AsyncClient

    def install(respond: Callable[[httpx.Request], httpx.Response] | Exception | None = None):
        recorder = _Recorder()

        def handler(request: httpx.Request) -> httpx.Response:
            recorder.requests.append(request)
            if isinstance(respond, Exception):
                raise respond
            if respond is None:
                return httpx.Response(200, json={"id": "email_123"})
            return respond(request)

        monkeypatch.setattr(
            resend_module.httpx,
            "AsyncClient",
            lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
        )
        return recorder

    return install


def _provider() -> ResendEmailProvider:
    from pydantic import SecretStr

    return ResendEmailProvider(api_key=SecretStr(_DUMMY_KEY), from_email=_FROM)


def _error_response(status: int, name: str | None, message: str = "provider detail") -> Callable:
    def respond(_: httpx.Request) -> httpx.Response:
        body = {"statusCode": status, "message": message}
        if name is not None:
            body["name"] = name
        return httpx.Response(status, json=body)

    return respond


# --- sending -----------------------------------------------------------------


async def test_resend_provider_posts_the_full_message_over_https(resend_api) -> None:
    api = resend_api()

    await _provider().send(
        to=["candidate@example.test"],
        subject="Interview invitation",
        html="<p>Hello</p>",
        text="Hello",
        reply_to="recruiter@example.test",
        cc=["cc@example.test"],
        bcc=["bcc@example.test"],
    )

    assert len(api.requests) == 1
    request = api.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == f"Bearer {_DUMMY_KEY}"
    assert api.json() == {
        "from": _FROM,
        "to": ["candidate@example.test"],
        "subject": "Interview invitation",
        "html": "<p>Hello</p>",
        "text": "Hello",  # the plain-text alternative travels with the HTML
        "reply_to": "recruiter@example.test",  # replies reach the sending recruiter
        "cc": ["cc@example.test"],
        "bcc": ["bcc@example.test"],
    }


async def test_resend_provider_omits_optional_fields_and_never_splits_a_bare_address(
    resend_api,
) -> None:
    api = resend_api()

    await _provider().send(to="candidate@example.test", subject="Hi", html="<p>Hi</p>")

    assert api.json() == {
        "from": _FROM,
        "to": ["candidate@example.test"],
        "subject": "Hi",
        "html": "<p>Hi</p>",
    }


# --- honest, generic failures ------------------------------------------------


@pytest.mark.parametrize(
    ("status", "name", "expected"),
    [
        (401, "missing_api_key", "rejected the configured API key"),
        (403, "invalid_api_key", "rejected the configured API key"),
        (401, "restricted_api_key", "rejected the configured API key"),
        # Unverified sender domain / testing-only account -> 403 validation_error.
        (403, "validation_error", "refused the sender or recipient address"),
        (422, "missing_required_field", "refused the sender or recipient address"),
        (429, "rate_limit_exceeded", "not accepting more messages"),
        (429, "daily_quota_exceeded", "not accepting more messages"),
        (500, "application_error", "Could not deliver the email through the email service"),
        (503, "service_unavailable", "Could not deliver the email through the email service"),
    ],
)
async def test_resend_failures_become_generic_email_errors(
    resend_api, status: int, name: str, expected: str
) -> None:
    resend_api(_error_response(status, name, message=f"secret-detail-{_DUMMY_KEY}"))

    with pytest.raises(EmailError) as excinfo:
        await _provider().send(to=["c@example.test"], subject="s", html="<p>x</p>")

    assert expected in str(excinfo.value)
    # The recruiter-visible message never echoes the provider's text or the key.
    assert "secret-detail" not in str(excinfo.value)
    assert _DUMMY_KEY not in str(excinfo.value)


async def test_resend_non_json_error_body_is_still_an_email_error(resend_api) -> None:
    resend_api(lambda _: httpx.Response(502, text="<html>Bad gateway</html>"))

    with pytest.raises(EmailError, match="Could not deliver the email through the email service"):
        await _provider().send(to=["c@example.test"], subject="s", html="<p>x</p>")


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("boom to api.resend.com"), httpx.ReadTimeout("slow")],
)
async def test_resend_network_failure_is_an_email_error_without_leaking_detail(
    resend_api, failure: Exception
) -> None:
    resend_api(failure)

    with pytest.raises(EmailError) as excinfo:
        await _provider().send(to=["c@example.test"], subject="s", html="<p>x</p>")

    assert str(excinfo.value) == "Could not reach the email service."


async def test_resend_failure_logs_diagnostics_but_never_the_key_or_the_body(
    resend_api, caplog: pytest.LogCaptureFixture
) -> None:
    resend_api(_error_response(403, "validation_error", message=f"leak {_DUMMY_KEY}"))

    with caplog.at_level(logging.INFO), pytest.raises(EmailError):
        await _provider().send(
            to=["candidate@example.test"], subject="s", html=f"<p>{_SECRET_BODY}</p>"
        )

    logged = "\n".join(
        f"{record.getMessage()} {getattr(record, 'extra_fields', '')}" for record in caplog.records
    )
    # What we need to diagnose it from the Render logs...
    assert "Resend send failed" in logged
    assert "403" in logged
    assert "validation_error" in logged
    # ...and nothing sensitive.
    assert _DUMMY_KEY not in logged
    assert _SECRET_BODY not in logged
    assert "candidate@example.test" not in logged


# --- through the manual-send service ----------------------------------------


async def test_notification_service_sends_through_resend_when_configured(
    monkeypatch: pytest.MonkeyPatch, resend_api
) -> None:
    """The same entry point every manual email uses (application composer,
    assessment invitation, general email) reaches Resend, with Reply-To set."""
    api = resend_api()
    monkeypatch.setattr(
        email_module,
        "get_settings",
        lambda: _settings(resend_api_key=_DUMMY_KEY, email_from=_FROM),
    )

    await notification_service.send_email(
        to=["candidate@example.test"],
        subject="Update on your application",
        html="<p>Hi</p>",
        text="Hi",
        reply_to="recruiter@example.test",
    )

    assert len(api.requests) == 1
    assert api.json()["reply_to"] == "recruiter@example.test"
    assert api.json()["text"] == "Hi"


async def test_notification_service_reports_a_resend_failure_instead_of_faking_success(
    monkeypatch: pytest.MonkeyPatch, resend_api
) -> None:
    resend_api(_error_response(403, "validation_error"))
    monkeypatch.setattr(
        email_module,
        "get_settings",
        lambda: _settings(resend_api_key=_DUMMY_KEY, email_from=_FROM),
    )

    with pytest.raises(EmailError):
        await notification_service.send_email(
            to=["candidate@example.test"], subject="s", html="<p>x</p>", text="x"
        )


# --- provider selection ------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost/db",
        jwt_secret_key="x" * 32,
        **overrides,
    )


_SMTP = {
    "smtp_host": "smtp.example.test",
    "smtp_username": "sender@example.test",
    "smtp_password": "dummy-test-password-not-real",
    "smtp_from_email": "sender@example.test",
}
_RESEND = {"resend_api_key": _DUMMY_KEY, "email_from": _FROM}


def _select(monkeypatch: pytest.MonkeyPatch, **values: object):
    monkeypatch.setattr(email_module, "get_settings", lambda: _settings(**values))
    return get_email_provider()


def test_resend_is_preferred_when_both_resend_and_smtp_are_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production has both sets of variables; SMTP is blocked there, so
    Resend must win."""
    assert isinstance(_select(monkeypatch, **_SMTP, **_RESEND), ResendEmailProvider)


def test_resend_is_used_when_only_resend_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(_select(monkeypatch, **_RESEND), ResendEmailProvider)


def test_smtp_is_still_used_for_local_development(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(_select(monkeypatch, **_SMTP), SmtpEmailProvider)


def test_nothing_configured_is_an_unconfigured_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(_select(monkeypatch), UnconfiguredEmailProvider)


@pytest.mark.parametrize("missing", ["resend_api_key", "email_from"])
def test_incomplete_resend_configuration_falls_back_to_smtp_then_unconfigured(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    partial = {**_RESEND, missing: ""}
    assert isinstance(_select(monkeypatch, **_SMTP, **partial), SmtpEmailProvider)
    assert isinstance(_select(monkeypatch, **partial), UnconfiguredEmailProvider)


def test_resend_api_key_never_appears_in_settings_repr() -> None:
    settings = _settings(**_RESEND)
    assert _DUMMY_KEY not in repr(settings)
    assert _DUMMY_KEY not in str(settings.model_dump())
