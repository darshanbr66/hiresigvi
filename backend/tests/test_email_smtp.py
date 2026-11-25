"""SMTP email delivery and the manual-only send workflow.

`smtplib` and the provider are faked here on purpose: there is no real SMTP
server in the test environment, and tests must never deliver real mail (see
tests/conftest.py, which also blanks any SMTP_* values from a local .env).
The dummy password below is a test literal, not a real credential.
"""

import smtplib

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations import email as email_module
from app.integrations.email import (
    EmailError,
    SmtpEmailProvider,
    UnconfiguredEmailProvider,
    get_email_provider,
)

_DUMMY_PASSWORD = "dummy-test-password-not-real"


class _FakeSmtp:
    """Records what SmtpEmailProvider does to an SMTP connection."""

    instances: list["_FakeSmtp"] = []
    login_error: Exception | None = None
    ssl = False

    def __init__(self, host: str, port: int, **kwargs: object) -> None:
        self.host, self.port, self.kwargs = host, port, kwargs
        self.calls: list[str] = []
        self.sent = None
        _FakeSmtp.instances.append(self)

    def __enter__(self) -> "_FakeSmtp":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self, **kwargs: object) -> None:
        self.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        self.calls.append("login")
        self.login_args = (username, password)
        if _FakeSmtp.login_error is not None:
            raise _FakeSmtp.login_error

    def send_message(self, message) -> None:  # noqa: ANN001
        self.calls.append("send_message")
        self.sent = message


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSmtp]:
    _FakeSmtp.instances = []
    _FakeSmtp.login_error = None
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSmtp)
    return _FakeSmtp


def _provider(port: int = 587) -> SmtpEmailProvider:
    return SmtpEmailProvider(
        host="smtp.example.test",
        port=port,
        username="sender@example.test",
        password=SecretStr(_DUMMY_PASSWORD),
        from_email="sender@example.test",
        from_name="AI Recruitment Platform",
        use_tls=True,
    )


async def test_smtp_provider_uses_starttls_login_and_sends_multipart(fake_smtp) -> None:
    await _provider().send(
        to="cara@example.test", subject="Interview", html="<p>Hi</p>", text="Hi"
    )

    (connection,) = fake_smtp.instances
    assert (connection.host, connection.port) == ("smtp.example.test", 587)
    assert connection.calls == ["starttls", "login", "send_message"]
    assert connection.login_args == ("sender@example.test", _DUMMY_PASSWORD)

    message = connection.sent
    assert message["To"] == "cara@example.test"
    assert message["Subject"] == "Interview"
    assert "AI Recruitment Platform" in message["From"]
    assert "sender@example.test" in message["From"]
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "Hi"
    assert message.get_body(preferencelist=("html",)).get_content().strip() == "<p>Hi</p>"


async def test_smtp_provider_addresses_multiple_recipients_cc_and_bcc(fake_smtp) -> None:
    await _provider().send(
        to=["a@example.test", "b@example.test"],
        cc=["c@example.test"],
        bcc=["d@example.test"],
        subject="Hello",
        html="<p>Hi</p>",
        text="Hi",
        reply_to="riya@example.test",
    )

    (connection,) = fake_smtp.instances
    message = connection.sent
    assert message["To"] == "a@example.test, b@example.test"
    assert message["Cc"] == "c@example.test"
    # smtplib delivers to Bcc recipients and strips this header from the copy
    # actually transmitted, so they stay hidden from everyone else.
    assert message["Bcc"] == "d@example.test"
    assert message["Reply-To"] == "riya@example.test"


async def test_smtp_provider_never_splits_a_bare_address_into_characters(fake_smtp) -> None:
    await _provider().send(to="cara@example.test", subject="Hi", html="<p>x</p>")

    (connection,) = fake_smtp.instances
    assert connection.sent["To"] == "cara@example.test"
    assert connection.sent["Cc"] is None and connection.sent["Bcc"] is None


async def test_smtp_provider_uses_implicit_tls_on_port_465(fake_smtp) -> None:
    await _provider(port=465).send(to="cara@example.test", subject="S", html="<p>x</p>")

    (connection,) = fake_smtp.instances
    assert "starttls" not in connection.calls  # already TLS from the first byte
    assert "send_message" in connection.calls


async def test_smtp_auth_failure_is_reported_without_leaking_the_password(fake_smtp) -> None:
    fake_smtp.login_error = smtplib.SMTPAuthenticationError(535, b"bad creds sender@example.test")

    with pytest.raises(EmailError) as excinfo:
        await _provider().send(to="cara@example.test", subject="S", html="<p>x</p>")

    assert _DUMMY_PASSWORD not in str(excinfo.value)
    assert "sender@example.test" not in str(excinfo.value)
    assert "credentials" in str(excinfo.value)


async def test_smtp_connection_failure_is_an_email_error(fake_smtp) -> None:
    fake_smtp.login_error = OSError("connection refused")

    with pytest.raises(EmailError):
        await _provider().send(to="cara@example.test", subject="S", html="<p>x</p>")


async def test_smtp_provider_rejects_header_injection_in_subject(fake_smtp) -> None:
    with pytest.raises(EmailError):
        await _provider().send(
            to="cara@example.test", subject="Hi\r\nBcc: attacker@example.test", html="<p>x</p>"
        )
    assert fake_smtp.instances == []  # never even connected


def _settings(**overrides: object) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost/db",
        jwt_secret_key="x" * 32,
        **overrides,
    )


def test_provider_selection_prefers_configured_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        email_module,
        "get_settings",
        lambda: _settings(
            smtp_host="smtp.example.test",
            smtp_username="sender@example.test",
            smtp_password=_DUMMY_PASSWORD,
            smtp_from_email="sender@example.test",
        ),
    )
    assert isinstance(get_email_provider(), SmtpEmailProvider)


@pytest.mark.parametrize(
    "missing", ["smtp_host", "smtp_username", "smtp_password", "smtp_from_email"]
)
def test_incomplete_smtp_configuration_is_treated_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    values = {
        "smtp_host": "smtp.example.test",
        "smtp_username": "sender@example.test",
        "smtp_password": _DUMMY_PASSWORD,
        "smtp_from_email": "sender@example.test",
    }
    values[missing] = ""
    monkeypatch.setattr(email_module, "get_settings", lambda: _settings(**values))
    assert isinstance(get_email_provider(), UnconfiguredEmailProvider)


def test_smtp_password_never_appears_in_settings_repr() -> None:
    settings = _settings(smtp_password=_DUMMY_PASSWORD)
    assert _DUMMY_PASSWORD not in repr(settings)
    assert _DUMMY_PASSWORD not in str(settings.model_dump())


# --- sender display name (branding) ---------------------------------------------


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("SIGVITAS", "Zodiac Patent"),
        ("Sigvitas Recruitment", "Zodiac Patent"),
        ("", "Zodiac Patent"),
        ("   ", "Zodiac Patent"),
        ("Zodiac Patent", "Zodiac Patent"),
        ("Zodiac Patent Recruitment", "Zodiac Patent Recruitment"),
    ],
)
async def test_the_mime_from_header_never_carries_the_retired_brand(
    fake_smtp, configured: str, expected: str
) -> None:
    provider = SmtpEmailProvider(
        host="smtp.example.test",
        port=587,
        username="mailer@sigvitas.example",
        password=SecretStr(_DUMMY_PASSWORD),
        from_email="mailer@sigvitas.example",
        from_name=configured,
        use_tls=True,
    )
    await provider.send(to="cara@example.test", subject="Code", html="<p>1</p>", text="1")

    message = fake_smtp.instances[0].sent
    # The display name is the brand; the configured mailbox is unchanged.
    assert message["From"] == f"{expected} <mailer@sigvitas.example>"
    assert message["From"].addresses[0].display_name == expected
    assert fake_smtp.instances[0].login_args[0] == "mailer@sigvitas.example"


def test_the_default_sender_name_is_zodiac_patent() -> None:
    assert Settings.model_fields["smtp_from_name"].default == "Zodiac Patent"
