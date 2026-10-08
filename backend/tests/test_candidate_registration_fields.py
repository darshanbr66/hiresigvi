"""The candidate registration (self-service apply) form's field rules, over
HTTP:

- full name: required, but no length limit
- LinkedIn / GitHub: optional; still validated when supplied
- 10th marks percentage: optional, 0–100, at most two decimals
- 10th marks card: optional PDF/PNG/JPG upload, size-limited, stored through
  the storage abstraction (here the GridFS provider over the in-memory fake
  bucket), referenced from the candidate row only, tenant-scoped, never
  exposed to a client, and never left orphaned by a failed registration or
  by a replacement
- a returning candidate's mobile number and marks come back only after the
  email's one-time code is verified
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import rls_bypass
from app.models.candidate import Candidate
from app.models.user import User
from app.services import email_verification_service, screening_service
from tests.conftest import make_minimal_pdf
from tests.fakes_mongo import FakeAsyncGridFSBucket
from tests.public_apply import apply_publicly, phone_for, request_code, verify_email
from tests.test_candidate_intake import _second_job, _StubScreeningProvider
from tests.test_public_applications import _bootstrap_org_with_open_job
from tests.test_public_apply_returning_candidate import _age_applications
from tests.test_resume_mongodb_provider import mongodb_gridfs_provider  # noqa: F401

pytestmark = pytest.mark.asyncio

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
_PDF = make_minimal_pdf("10th marks card")


@pytest.fixture(autouse=True)
def _ai_matches(monkeypatch: pytest.MonkeyPatch, otp_settings) -> None:
    provider = _StubScreeningProvider("MATCH")
    monkeypatch.setattr(screening_service, "get_llm_provider", lambda: provider)
    otp_settings(email_otp_resend_cooldown_seconds=0, email_otp_max_sends_per_hour=50)


async def _candidate_row(db: AsyncSession, organization_slug_email: str) -> Candidate:
    async with rls_bypass(db):
        return (
            await db.execute(
                select(Candidate)
                .where(Candidate.email == organization_slug_email)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()


def _marks_cards(bucket: FakeAsyncGridFSBucket) -> list[dict[str, Any]]:
    return [
        {"id": str(file_id), **record}
        for file_id, record in bucket.files.items()
        if record["metadata"].get("document_kind") == "tenth_marks_card"
    ]


async def _candidates(client: AsyncClient, ctx: dict) -> list[dict[str, Any]]:
    response = await client.get("/api/v1/recruiter/candidates", headers=ctx["admin_headers"])
    assert response.status_code == 200, response.text
    return list(response.json())


# --- full name ------------------------------------------------------------------


async def test_full_name_has_no_length_limit(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reg-long-name")
    long_name = " ".join(["Venkata"] * 150)  # 1,199 characters
    response = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="long@reg-long-name.dev", full_name=long_name
    )
    assert response.status_code == 201, response.text
    (candidate,) = await _candidates(client, ctx)
    assert candidate["full_name"] == long_name


async def test_full_name_is_still_required(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reg-name-required")
    token = await verify_email(client, ctx["slug"], "n@reg-name-required.dev")
    for blank in ("", "   "):
        response = await apply_publicly(
            client, ctx["slug"], ctx["job_id"],
            email="n@reg-name-required.dev", token=token, full_name=blank,
        )
        assert response.status_code == 422, (blank, response.text)
    assert await _candidates(client, ctx) == []


# --- LinkedIn / GitHub ------------------------------------------------------------


@pytest.mark.parametrize("omitted", ["linkedin_url", "github_url", "both"])
async def test_profile_urls_are_optional(
    client: AsyncClient, super_admin: User, omitted: str
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, f"reg-optional-{omitted.replace('_', '-')}")
    fields = ("linkedin_url", "github_url") if omitted == "both" else (omitted,)
    email = f"opt@{ctx['slug']}.dev"
    response = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email=email, **dict.fromkeys(fields)
    )
    assert response.status_code == 201, response.text
    (candidate,) = await _candidates(client, ctx)
    for field in fields:
        assert candidate[field] is None


async def test_blank_profile_urls_count_as_not_provided(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reg-blank-urls")
    response = await apply_publicly(
        client, ctx["slug"], ctx["job_id"],
        email="b@reg-blank-urls.dev", linkedin_url="  ", github_url="",
    )
    assert response.status_code == 201, response.text
    (candidate,) = await _candidates(client, ctx)
    assert candidate["linkedin_url"] is None
    assert candidate["github_url"] is None


async def test_supplied_profile_urls_are_still_validated(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reg-bad-urls")
    token = await verify_email(client, ctx["slug"], "u@reg-bad-urls.dev")
    for field, bad in (
        ("linkedin_url", "https://evil.example.com/in/u"),
        ("linkedin_url", "javascript:alert(1)"),
        ("github_url", "https://notgithub.com/u"),
        ("github_url", "ftp://github.com/u"),
    ):
        response = await apply_publicly(
            client, ctx["slug"], ctx["job_id"], email="u@reg-bad-urls.dev", token=token,
            **{field: bad},
        )
        assert response.status_code == 422, (field, bad, response.text)
    assert await _candidates(client, ctx) == []


# --- 10th marks percentage --------------------------------------------------------


async def test_tenth_marks_are_optional(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reg-marks-omitted")
    response = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="m@reg-marks-omitted.dev"
    )
    assert response.status_code == 201, response.text
    row = await _candidate_row(db_session, "m@reg-marks-omitted.dev")
    assert row.tenth_marks_percentage is None
    assert row.tenth_marks_card_storage_path is None


async def test_tenth_marks_are_stored(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reg-marks-stored")
    response = await apply_publicly(
        client, ctx["slug"], ctx["job_id"],
        email="m@reg-marks-stored.dev", tenth_marks_percentage="87.5",
    )
    assert response.status_code == 201, response.text
    row = await _candidate_row(db_session, "m@reg-marks-stored.dev")
    assert float(row.tenth_marks_percentage) == 87.5


async def test_invalid_tenth_marks_are_rejected(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reg-marks-invalid")
    token = await verify_email(client, ctx["slug"], "m@reg-marks-invalid.dev")
    for bad in ("100.01", "-1", "abc", "87.555"):
        response = await apply_publicly(
            client, ctx["slug"], ctx["job_id"],
            email="m@reg-marks-invalid.dev", token=token, tenth_marks_percentage=bad,
        )
        assert response.status_code == 422, (bad, response.text)
    assert await _candidates(client, ctx) == []


# --- 10th marks card --------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("marks.pdf", _PDF, "application/pdf"),
        ("marks.png", _PNG, "image/png"),
        ("marks.JPG", _JPEG, "image/jpeg"),
        ("marks.jpeg", _JPEG, "image/jpeg"),
    ],
)
async def test_marks_card_is_stored_in_gridfs_and_referenced_from_the_candidate(
    client: AsyncClient,
    super_admin: User,
    db_session: AsyncSession,
    mongodb_gridfs_provider: FakeAsyncGridFSBucket,  # noqa: F811
    filename: str,
    content: bytes,
    content_type: str,
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reg-card-stored")
    response = await apply_publicly(
        client, ctx["slug"], ctx["job_id"],
        email="c@reg-card-stored.dev", marks_card=(filename, content, "application/octet-stream"),
    )
    assert response.status_code == 201, response.text

    (stored,) = _marks_cards(mongodb_gridfs_provider)
    assert stored["content"] == content
    row = await _candidate_row(db_session, "c@reg-card-stored.dev")
    assert row.tenth_marks_card_storage_provider == "mongodb_gridfs"
    assert row.tenth_marks_card_storage_path == stored["id"]
    assert row.tenth_marks_card_filename == filename
    # Detected from the file, not taken from the client's claim.
    assert row.tenth_marks_card_content_type == content_type
    assert row.tenth_marks_card_size_bytes == len(content)
    assert stored["metadata"]["organization_id"] == str(row.organization_id)
    assert stored["metadata"]["candidate_id"] == str(row.id)
    assert stored["metadata"]["application_id"] is None

    # The resume upload is unaffected: still stored alongside, as before.
    resumes = [
        r for r in mongodb_gridfs_provider.files.values()
        if r["metadata"].get("document_kind") == "resume"
    ]
    assert len(resumes) == 1

    # Never exposed to a client.
    (candidate,) = await _candidates(client, ctx)
    assert stored["id"] not in str(candidate)


async def test_marks_card_is_optional(
    client: AsyncClient,
    super_admin: User,
    mongodb_gridfs_provider: FakeAsyncGridFSBucket,  # noqa: F811
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reg-card-omitted")
    response = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="c@reg-card-omitted.dev"
    )
    assert response.status_code == 201, response.text
    assert _marks_cards(mongodb_gridfs_provider) == []


async def test_invalid_marks_card_files_are_rejected_and_nothing_is_stored(
    client: AsyncClient,
    super_admin: User,
    mongodb_gridfs_provider: FakeAsyncGridFSBucket,  # noqa: F811
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reg-card-invalid")
    token = await verify_email(client, ctx["slug"], "c@reg-card-invalid.dev")
    for bad in (
        ("setup.exe", b"MZ\x90\x00" + b"\x00" * 32, "application/octet-stream"),
        ("notes.txt", b"just text", "text/plain"),
        ("marks.docx", b"PK\x03\x04" + b"\x00" * 32, "application/octet-stream"),
        # A renamed executable: right extension, wrong bytes.
        ("marks.pdf", b"MZ\x90\x00" + b"\x00" * 32, "application/pdf"),
        ("marks.png", _JPEG, "image/png"),
        ("marks.pdf", b"", "application/pdf"),
    ):
        response = await apply_publicly(
            client, ctx["slug"], ctx["job_id"],
            email="c@reg-card-invalid.dev", token=token, marks_card=bad,
        )
        assert response.status_code in (400, 422), (bad[0], response.text)

    assert mongodb_gridfs_provider.files == {}
    assert await _candidates(client, ctx) == []
    # A rejected file never spent the verification token.
    ok = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email="c@reg-card-invalid.dev", token=token
    )
    assert ok.status_code == 201, ok.text


async def test_oversized_marks_card_is_rejected(
    client: AsyncClient,
    super_admin: User,
    mongodb_gridfs_provider: FakeAsyncGridFSBucket,  # noqa: F811
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reg-card-large")
    too_big = b"%PDF-1.4\n" + b"0" * (5 * 1024 * 1024)
    response = await apply_publicly(
        client, ctx["slug"], ctx["job_id"],
        email="c@reg-card-large.dev", marks_card=("marks.pdf", too_big, "application/pdf"),
    )
    assert response.status_code in (400, 413, 422), response.text
    assert response.json()["error"]["code"] == "file_too_large"
    assert _marks_cards(mongodb_gridfs_provider) == []
    assert await _candidates(client, ctx) == []


async def test_failed_registration_leaves_no_marks_card_in_storage(
    client: AsyncClient,
    super_admin: User,
    monkeypatch: pytest.MonkeyPatch,
    mongodb_gridfs_provider: FakeAsyncGridFSBucket,  # noqa: F811
) -> None:
    """A failure after the card was written but before the registration
    was committed removes the card again."""
    ctx = await _bootstrap_org_with_open_job(client, "reg-card-orphan")
    token = await verify_email(client, ctx["slug"], "c@reg-card-orphan.dev")

    async def _fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("simulated failure before commit")

    uploaded_kinds: list[str] = []
    upload = mongodb_gridfs_provider.upload_from_stream

    async def _recording_upload(filename: str, source: bytes, metadata: Any = None, **kw: Any):
        uploaded_kinds.append((metadata or {}).get("document_kind"))
        return await upload(filename, source, metadata=metadata, **kw)

    monkeypatch.setattr(mongodb_gridfs_provider, "upload_from_stream", _recording_upload)
    monkeypatch.setattr(email_verification_service, "consume", _fail)
    try:
        response = await apply_publicly(
            client, ctx["slug"], ctx["job_id"],
            email="c@reg-card-orphan.dev", token=token,
            marks_card=("marks.png", _PNG, "image/png"),
        )
        assert response.status_code == 500
    except RuntimeError:
        pass
    # The card was written, then removed when the registration failed.
    assert "tenth_marks_card" in uploaded_kinds
    assert _marks_cards(mongodb_gridfs_provider) == []


async def test_returning_candidate_card_is_replaced_or_kept_without_orphans(
    client: AsyncClient,
    super_admin: User,
    db_session: AsyncSession,
    mongodb_gridfs_provider: FakeAsyncGridFSBucket,  # noqa: F811
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reg-card-replace")
    email = "r@reg-card-replace.dev"
    first = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email=email,
        marks_card=("old.pdf", _PDF, "application/pdf"),
    )
    assert first.status_code == 201, first.text
    (old,) = _marks_cards(mongodb_gridfs_provider)

    # A new card replaces the old one, which is removed from storage.
    await _age_applications(db_session, email, days=130)
    second_job = await _second_job(client, ctx, "Data Analyst")
    second = await apply_publicly(
        client, ctx["slug"], second_job, email=email,
        marks_card=("new.png", _PNG, "image/png"),
    )
    assert second.status_code == 201, second.text
    (new,) = _marks_cards(mongodb_gridfs_provider)
    assert new["id"] != old["id"]
    row = await _candidate_row(db_session, email)
    assert row.tenth_marks_card_storage_path == new["id"]
    assert row.tenth_marks_card_filename == "new.png"

    # Applying again without a card keeps the stored one.
    await _age_applications(db_session, email, days=130)
    third_job = await _second_job(client, ctx, "QA Engineer")
    third = await apply_publicly(client, ctx["slug"], third_job, email=email)
    assert third.status_code == 201, third.text
    assert [c["id"] for c in _marks_cards(mongodb_gridfs_provider)] == [new["id"]]
    row = await _candidate_row(db_session, email)
    assert row.tenth_marks_card_storage_path == new["id"]
    # Still one profile.
    assert len(await _candidates(client, ctx)) == 1


async def test_marks_cards_are_tenant_isolated(
    client: AsyncClient,
    super_admin: User,
    db_session: AsyncSession,
    mongodb_gridfs_provider: FakeAsyncGridFSBucket,  # noqa: F811
) -> None:
    """The same person registering with two organizations: two independent
    candidates, each referencing its own file, tagged with its own tenant."""
    ctx_a = await _bootstrap_org_with_open_job(client, "reg-card-tenant-a")
    ctx_b = await _bootstrap_org_with_open_job(client, "reg-card-tenant-b")
    email = "same.person@example.com"
    for ctx, content in ((ctx_a, _PDF), (ctx_b, _PNG)):
        name = "a.pdf" if content is _PDF else "b.png"
        response = await apply_publicly(
            client, ctx["slug"], ctx["job_id"], email=email,
            marks_card=(name, content, "application/octet-stream"),
        )
        assert response.status_code == 201, response.text

    async with rls_bypass(db_session):
        rows = (
            await db_session.execute(
                select(Candidate)
                .where(Candidate.email == email)
                .execution_options(populate_existing=True)
            )
        ).scalars().all()
    assert len(rows) == 2
    files = {c["id"]: c for c in _marks_cards(mongodb_gridfs_provider)}
    assert len(files) == 2
    assert rows[0].tenth_marks_card_storage_path != rows[1].tenth_marks_card_storage_path
    for row in rows:
        stored = files[row.tenth_marks_card_storage_path]
        assert stored["metadata"]["organization_id"] == str(row.organization_id)
        assert stored["metadata"]["candidate_id"] == str(row.id)

    # Each organization sees only its own candidate.
    (only_a,) = await _candidates(client, ctx_a)
    (only_b,) = await _candidates(client, ctx_b)
    assert only_a["id"] != only_b["id"]


# --- existing email: nothing private before the code ------------------------------


async def test_mobile_and_marks_return_only_after_the_code_is_verified(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reg-prefill-after-otp")
    email = "p@reg-prefill-after-otp.dev"
    first = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email=email,
        tenth_marks_percentage="91.25", linkedin_url=None,
    )
    assert first.status_code == 201, first.text
    await _age_applications(db_session, email, days=130)
    other_job = await _second_job(client, ctx)
    base = f"/api/v1/public/organizations/{ctx['slug']}"
    mobile = phone_for(email)

    # Before any code: the early check is a bare yes/no.
    status = await client.post(
        f"{base}/applicant-status", json={"email": email, "job_id": other_job}
    )
    assert status.status_code == 200, status.text
    assert status.json() == {"already_applied": False}

    response, code = await request_code(client, ctx["slug"], email)
    assert response.status_code == 202
    assert mobile not in response.text and "91.25" not in response.text

    verified = await client.post(
        f"{base}/email-verification/verify",
        json={"email": email, "code": code, "job_id": other_job},
    )
    assert verified.status_code == 200, verified.text
    profile = verified.json()["existing_profile"]
    assert profile["phone"] == mobile
    assert profile["tenth_marks_percentage"] == 91.25
    assert profile["linkedin_url"] is None
    # The stored file's location is never part of the prefill.
    assert not any(key.startswith("tenth_marks_card") for key in profile)


async def test_blocked_existing_email_reveals_no_mobile_before_the_code(
    client: AsyncClient, super_admin: User
) -> None:
    """Regression: an existing email that is blocked from applying again
    gets only the bare yes/no from the early check, and a refused code
    request; neither carries the stored mobile number (or anything else
    from the profile), in any format."""
    ctx = await _bootstrap_org_with_open_job(client, "reg-blocked-no-mobile")
    email = "dbr@reg-blocked-no-mobile.dev"
    first = await apply_publicly(
        client, ctx["slug"], ctx["job_id"], email=email, full_name="Dee Blocked"
    )
    assert first.status_code == 201, first.text
    mobile = phone_for(email)  # stored E.164, e.g. +919xxxxxxxxx
    leaks = (mobile, mobile.removeprefix("+91"), mobile[-4:], "Dee Blocked")
    base = f"/api/v1/public/organizations/{ctx['slug']}"

    status = await client.post(
        f"{base}/applicant-status", json={"email": email, "job_id": ctx["job_id"]}
    )
    assert status.status_code == 200, status.text
    assert status.json() == {"already_applied": True}
    for value in leaks:
        assert value not in status.text

    response, code = await request_code(client, ctx["slug"], email)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "already_applied"
    assert code is None  # no code was sent
    for value in leaks:
        assert value not in response.text


async def test_early_check_answers_from_the_email_alone(
    client: AsyncClient, super_admin: User
) -> None:
    """Regression: the early check needs no mobile number. A blocked
    existing email is answered from the email alone; and an email with no
    profile in this organization is not "already applied" just because a
    mobile number typed later belongs to someone else's blocked profile —
    that is a separate, mobile-triggered answer."""
    ctx = await _bootstrap_org_with_open_job(client, "reg-email-alone")
    base = f"/api/v1/public/organizations/{ctx['slug']}"
    blocked = "blocked@reg-email-alone.dev"
    first = await apply_publicly(client, ctx["slug"], ctx["job_id"], email=blocked)
    assert first.status_code == 201, first.text

    by_email = await client.post(
        f"{base}/applicant-status", json={"email": blocked, "job_id": ctx["job_id"]}
    )
    assert by_email.status_code == 200, by_email.text
    assert by_email.json() == {"already_applied": True}

    # A different email that has no profile here: not blocked on its own...
    stranger = "someone.new@reg-email-alone.dev"
    alone = await client.post(
        f"{base}/applicant-status", json={"email": stranger, "job_id": ctx["job_id"]}
    )
    assert alone.json() == {"already_applied": False}
    # ...only once the blocked profile's mobile number is also entered.
    with_mobile = await client.post(
        f"{base}/applicant-status",
        json={"email": stranger, "phone": phone_for(blocked), "job_id": ctx["job_id"]},
    )
    assert with_mobile.json() == {"already_applied": True}
    assert phone_for(blocked) not in with_mobile.text
