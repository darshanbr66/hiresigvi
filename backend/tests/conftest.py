import os
from collections.abc import AsyncGenerator, Sequence
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

# A developer's local .env may hold real SMTP/Resend credentials. Environment
# variables take precedence over .env in pydantic-settings, so blanking them
# here — before `app.*` (and therefore Settings) is imported — guarantees no
# test can ever deliver a real email. Tests that need a configured provider
# monkeypatch `get_email_provider`/`get_settings` explicitly.
for _name in (
    "SMTP_HOST",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM_EMAIL",
    "RESEND_API_KEY",
    "EMAIL_FROM",
    # Same rule for the Sigvi assistant: no test may ever reach the real
    # Gemini API, whatever a developer's .env holds.
    "GEMINI_API_KEY",
    # ...nor any real screening model (the public apply flow screens every
    # submission). Tests that need a verdict monkeypatch `get_llm_provider`.
    "OLLAMA_BASE_URL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
):
    os.environ[_name] = ""

# Same reasoning for resume storage: a local .env selecting `mongodb_gridfs`
# would send app-level tests to a MongoDB that isn't connected in tests (and
# would write test resumes into a real deployment if it were). App-level
# tests always use the disk backend; the GridFS provider has its own tests
# against an in-memory fake (tests/test_resume_mongodb_provider.py).
os.environ["RESUME_STORAGE_PROVIDER"] = "local"

from app.core.asyncio_compat import configure_event_loop_policy  # noqa: E402

configure_event_loop_policy()

from app.core.security import hash_password  # noqa: E402
from app.db.rls import rls_bypass  # noqa: E402
from app.db.session import engine, get_db  # noqa: E402
from app.integrations.email import EmailError, EmailProvider  # noqa: E402
from app.integrations.email.base import as_address_list  # noqa: E402
from app.main import app  # noqa: E402
from app.models.rbac import Role, UserRole  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import notification_service  # noqa: E402

# Fixed test credentials for the one platform account that can't be created
# through the HTTP API (see app/cli.py) — seeded directly here the same way
# the CLI does it, so tests can log in as a real SUPER_ADMIN.
#
# Deliberately distinct from the demo super admin documented in
# docs/TEST_CREDENTIALS.md (super.admin@platform.dev): every test's outer
# transaction is rolled back (see db_connection below), but that rollback
# can only undo what happened inside the test's own transaction — it can't
# make a row that was already committed to the real dev database (by
# seeding demo/test-login data) stop existing. A fixture using the same
# email as a real committed row would hit a unique-constraint violation on
# every run.
SUPER_ADMIN_EMAIL = "pytest.super.admin@platform.dev"
SUPER_ADMIN_PASSWORD = "SuperSecretPass1"


class RecordingEmailProvider(EmailProvider):
    """Stands in for the SMTP server: records every email the application
    tries to send (or raises `error` instead), so tests can assert exactly
    what did — and, just as importantly, did not — go out."""

    def __init__(self, error: EmailError | None = None) -> None:
        self.sent: list[SimpleNamespace] = []
        self.error = error

    async def send(
        self,
        *,
        to: str | Sequence[str],
        subject: str,
        html: str,
        text: str | None = None,
        reply_to: str | None = None,
        cc: Sequence[str] = (),
        bcc: Sequence[str] = (),
    ) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(
            SimpleNamespace(
                to=as_address_list(to),
                cc=list(cc),
                bcc=list(bcc),
                subject=subject,
                html=html,
                text=text,
                reply_to=reply_to,
            )
        )


@pytest.fixture(autouse=True)
def _fresh_candidate_intake_rate_limits() -> None:
    """The per-IP limiters on the public candidate endpoints are process-wide
    (lru_cached); every test client shares one address, so each test starts
    with an empty budget rather than inheriting the previous tests' hits."""
    from app.api.v1.public.candidate_intake import get_candidate_intake_rate_limiters

    get_candidate_intake_rate_limiters.cache_clear()


@pytest.fixture
def otp_settings(monkeypatch: pytest.MonkeyPatch):
    """Overrides the email-verification limits for one test, e.g.
    `otp_settings(email_otp_resend_cooldown_seconds=0)`."""
    from app.core.config import get_settings
    from app.services import email_verification_service

    def apply(**overrides: object) -> None:
        patched = get_settings().model_copy(update=overrides)
        monkeypatch.setattr(email_verification_service, "get_settings", lambda: patched)

    return apply


@pytest.fixture
def recording_email(monkeypatch: pytest.MonkeyPatch) -> RecordingEmailProvider:
    provider = RecordingEmailProvider()
    monkeypatch.setattr(notification_service, "get_email_provider", lambda: provider)
    return provider


@pytest_asyncio.fixture
async def db_connection() -> AsyncGenerator[AsyncConnection, None]:
    """One connection + one outer transaction per test, rolled back at the
    end regardless of what the test does — including code under test that
    calls `session.commit()` (join_transaction_mode="create_savepoint" turns
    that into a savepoint release instead of an actual commit). Keeps tests
    from needing a separate database or leaving data behind.
    """
    async with engine.connect() as connection:
        await connection.begin()
        yield connection
        await connection.rollback()


@pytest_asyncio.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """An HTTP client against the real app, with `get_db` overridden to use
    this test's rolled-back transaction instead of a fresh production
    session — so hitting real endpoints (register, login, etc.) never
    writes to the live database. Mirrors the real `get_db`'s
    commit/rollback-on-exception so a request that fails mid-write (e.g. a
    duplicate-email 409) doesn't leave the shared test transaction aborted
    for the rest of the test.
    """

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def super_admin(db_session: AsyncSession) -> User:
    """Seeds a platform SUPER_ADMIN directly, mirroring `app/cli.py` — there
    is no HTTP path that can create one (that's the point; see
    app/cli.py's module docstring). Email/password are the module-level
    `SUPER_ADMIN_EMAIL`/`SUPER_ADMIN_PASSWORD` constants above.
    """
    async with rls_bypass(db_session):
        role = await db_session.scalar(
            select(Role).where(Role.organization_id.is_(None), Role.name == "SUPER_ADMIN")
        )
        assert role is not None, "SUPER_ADMIN system role must be seeded by migrations"

        user = User(
            organization_id=None,
            email=SUPER_ADMIN_EMAIL,
            hashed_password=hash_password(SUPER_ADMIN_PASSWORD),
            full_name="Platform Admin",
        )
        db_session.add(user)
        await db_session.flush()
        db_session.add(UserRole(user_id=user.id, role_id=role.id))
        await db_session.flush()

    return user


def make_minimal_pdf(text: str) -> bytes:
    """A hand-built, structurally valid single-page PDF containing `text` —
    used wherever a test needs a resume upload that real extraction
    (app/integrations/ai/extraction.py, pypdf-backed) can actually read.
    Not a fixture-library dependency; PDF's object/xref format is simple
    enough to construct directly for this one test need.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF"
    return bytes(out)


async def login(client: AsyncClient, *, email: str, password: str) -> dict:
    """Logs in via the real endpoint and returns the parsed JSON body.
    `httpx.AsyncClient` persists cookies across calls on the same instance,
    so the refresh-token cookie this sets is automatically sent by any
    later `client.post("/api/v1/recruiter/auth/refresh", ...)` in the same
    test — no manual cookie plumbing needed.
    """
    response = await client.post(
        "/api/v1/recruiter/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()
