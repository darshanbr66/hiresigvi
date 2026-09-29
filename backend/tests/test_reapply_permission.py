"""HR "Grant Reapply Permission" end to end over HTTP
(app/services/reapply_service.py::grant_reapply_permission).

Uses the real self-service flow — verified email, stored resume file,
submission-time AI screening (stubbed verdict) — so the records the grant
clears are exactly the ones a real application creates:

- the lookup by name, email and mobile number
- the grant clears the candidate's self-service applications, their
  resumes/files, screening runs, status history and notes — nothing else
- the same email and mobile then apply again through the normal flow
- an HR-matched application sharing the resume file survives, file included
- a failure part-way rolls everything back (rows and files)
- nothing to lift -> 409; unknown candidate -> 404

Authorization and tenant isolation for this endpoint are in
tests/test_reapply_window.py alongside the rest of the reapply rule.
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import rls_bypass
from app.integrations.storage import StorageError, get_resume_storage_for_provider
from app.models.application import Application, ApplicationStatusHistory
from app.models.candidate import Candidate
from app.models.note import Note
from app.models.resume import Resume
from app.models.screening import ScreeningRun
from app.services import activity_service, reapply_service, screening_service
from tests.public_apply import apply_publicly, phone_for, request_code
from tests.test_candidate_intake import _StubScreeningProvider
from tests.test_public_applications import _bootstrap_org_with_open_job

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _ai_matches(monkeypatch: pytest.MonkeyPatch, otp_settings) -> None:
    provider = _StubScreeningProvider("MATCH")
    monkeypatch.setattr(screening_service, "get_llm_provider", lambda: provider)
    # These tests verify the same address several times in a row.
    otp_settings(email_otp_resend_cooldown_seconds=0, email_otp_max_sends_per_hour=50)


async def _verify_outcome(client: AsyncClient, ctx: dict, email: str) -> Any:
    """The verify step is where a returning candidate's reapply window is
    checked (they have just proven who they are)."""
    response, code = await request_code(client, ctx["slug"], email)
    assert response.status_code == 202, response.text
    return await client.post(
        f"/api/v1/public/organizations/{ctx['slug']}/email-verification/verify",
        json={"email": email, "code": code},
    )


def _grant_url(candidate_id: str) -> str:
    return f"/api/v1/recruiter/candidates/{candidate_id}/grant-reapply"


async def _search(client: AsyncClient, ctx: dict, q: str) -> list[dict[str, Any]]:
    response = await client.get(
        "/api/v1/recruiter/candidates/reapply-search",
        params={"q": q},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _apply(client: AsyncClient, ctx: dict, email: str, **kw: Any) -> dict[str, Any]:
    response = await apply_publicly(
        client, ctx["slug"], kw.pop("job_id", ctx["job_id"]), email=email, **kw
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _candidate_id(db: AsyncSession, email: str) -> uuid.UUID:
    async with rls_bypass(db):
        candidate_id = await db.scalar(select(Candidate.id).where(Candidate.email == email))
    assert candidate_id is not None
    return candidate_id


async def _snapshot(db: AsyncSession, candidate_id: uuid.UUID) -> dict[str, Any]:
    """Every record the grant is meant to touch, for one candidate."""
    async with rls_bypass(db):
        applications = list(
            await db.scalars(select(Application).where(Application.candidate_id == candidate_id))
        )
        app_ids = [a.id for a in applications]
        resumes = list(await db.scalars(select(Resume).where(Resume.candidate_id == candidate_id)))

        async def count(model: Any) -> int:
            return int(
                await db.scalar(
                    select(func.count()).select_from(model).where(model.application_id.in_(app_ids))
                )
                or 0
            )

        return {
            "applications": applications,
            "resumes": resumes,
            "screening_runs": await count(ScreeningRun),
            "status_history": await count(ApplicationStatusHistory),
            "notes": await count(Note),
            "candidate_exists": await db.get(Candidate, candidate_id) is not None,
        }


def _file_of(resume: Resume) -> tuple[str, str]:
    """Plain values: the grant's bulk delete expires the ORM rows it removes,
    so tests keep where the file lives rather than the row itself."""
    return resume.storage_provider, resume.storage_path


async def _file_exists(file: tuple[str, str]) -> bool:
    provider, storage_path = file
    return await get_resume_storage_for_provider(provider).exists(storage_path)


async def _activities(client: AsyncClient, ctx: dict) -> list[str]:
    body = (
        await client.get("/api/v1/recruiter/activities?limit=200", headers=ctx["admin_headers"])
    ).json()
    items = body["items"] if isinstance(body, dict) else body
    return [a["action"] for a in items]


async def _open_second_job(client: AsyncClient, ctx: dict) -> str:
    job = await client.post(
        "/api/v1/recruiter/jobs",
        json={"title": "Platform Engineer", "description": "Runs the platform on Python."},
        headers=ctx["admin_headers"],
    )
    assert job.status_code == 201, job.text
    opened = await client.patch(
        f"/api/v1/recruiter/jobs/{job.json()['id']}",
        json={"status": "OPEN"},
        headers=ctx["admin_headers"],
    )
    assert opened.status_code == 200, opened.text
    return str(job.json()["id"])


# --- search ------------------------------------------------------------------


async def test_hr_finds_the_candidate_by_email_name_or_mobile(
    client: AsyncClient, db_session: AsyncSession, super_admin
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reapply-perm-search")
    await _apply(client, ctx, "maya@example.com", full_name="Maya Rao")
    await _apply(client, ctx, "someone.else@example.com", full_name="Omar Other")

    by_email = await _search(client, ctx, "MAYA@exam")
    assert [r["email"] for r in by_email] == ["maya@example.com"]
    # Just applied: inside the window, so the grant is on offer.
    assert by_email[0]["can_self_apply_now"] is False
    assert by_email[0]["eligible_from"] is not None

    assert [r["email"] for r in await _search(client, ctx, "maya ra")] == ["maya@example.com"]

    stored = phone_for("maya@example.com")  # "+919XXXXXXXXX"
    local = stored[3:]
    typed = f"{local[:5]} {local[5:]}"  # how HR might type it
    by_mobile = await _search(client, ctx, typed)
    assert [r["email"] for r in by_mobile] == ["maya@example.com"]
    assert by_mobile[0]["phone"] == stored

    assert await _search(client, ctx, "nobody-by-this-name") == []


async def test_search_is_tenant_scoped(
    client: AsyncClient, db_session: AsyncSession, super_admin
) -> None:
    org_a = await _bootstrap_org_with_open_job(client, "reapply-perm-search-a")
    org_b = await _bootstrap_org_with_open_job(client, "reapply-perm-search-b")
    await _apply(client, org_a, "tenant.a@example.com", full_name="Tara Tenant")

    assert await _search(client, org_b, "tenant.a@example.com") == []
    assert await _search(client, org_b, phone_for("tenant.a@example.com")) == []
    assert len(await _search(client, org_a, "tenant.a@example.com")) == 1


# --- the grant ---------------------------------------------------------------


async def test_grant_clears_previous_application_data_and_the_same_person_applies_again(
    client: AsyncClient, db_session: AsyncSession, super_admin
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reapply-perm-flow")
    await _apply(client, ctx, "ana@example.com", full_name="Ana Again")
    await _apply(client, ctx, "bystander@example.com", full_name="Ben Bystander")
    candidate_id = await _candidate_id(db_session, "ana@example.com")
    bystander_id = await _candidate_id(db_session, "bystander@example.com")

    before = await _snapshot(db_session, candidate_id)
    (old_application,) = before["applications"]
    old_application_id = old_application.id
    (old_resume,) = before["resumes"]
    old_file = _file_of(old_resume)
    assert before["screening_runs"] == 1  # the submission-time AI screening
    assert before["status_history"] >= 1
    assert await _file_exists(old_file)
    note = await client.post(
        f"/api/v1/recruiter/applications/{old_application_id}/notes",
        json={"body": "Strong backend profile."},
        headers=ctx["admin_headers"],
    )
    assert note.status_code == 201, note.text
    bystander_before = await _snapshot(db_session, bystander_id)
    bystander_app_ids = [a.id for a in bystander_before["applications"]]

    # Blocked by the 3-month rule.
    locked = await _verify_outcome(client, ctx, "ana@example.com")
    assert locked.status_code == 409
    assert locked.json()["error"]["code"] == "reapply_locked"

    granted = await client.post(_grant_url(str(candidate_id)), headers=ctx["admin_headers"])
    assert granted.status_code == 200, granted.text
    assert granted.json() == {
        "candidate_id": str(candidate_id),
        "full_name": "Ana Again",
        "email": "ana@example.com",
        "cleared_application_count": 1,
    }

    after = await _snapshot(db_session, candidate_id)
    assert after["applications"] == []
    assert after["resumes"] == []
    async with rls_bypass(db_session):
        for model in (ScreeningRun, ApplicationStatusHistory, Note):
            leftover = await db_session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.application_id == old_application_id)
            )
            assert leftover == 0, model.__name__
    assert not await _file_exists(old_file)  # no orphaned file
    # The profile itself stays; the audit trail stays and records the grant.
    assert after["candidate_exists"] is True
    actions = await _activities(client, ctx)
    assert "APPLICATION_CREATED" in actions
    assert "CANDIDATE_REAPPLY_PERMISSION_GRANTED" in actions
    history = await client.get(
        f"/api/v1/recruiter/candidates/{candidate_id}/history", headers=ctx["admin_headers"]
    )
    assert history.status_code == 200, history.text
    assert "CANDIDATE_REAPPLY_PERMISSION_GRANTED" in [
        t["action"] for t in history.json()["timeline"]
    ]

    # Another candidate's records are untouched.
    bystander_after = await _snapshot(db_session, bystander_id)
    assert [a.id for a in bystander_after["applications"]] == bystander_app_ids
    assert bystander_after["screening_runs"] == bystander_before["screening_runs"]
    assert await _file_exists(_file_of(bystander_after["resumes"][0]))

    # The recruiter candidate list still shows both people.
    listed = await client.get("/api/v1/recruiter/candidates", headers=ctx["admin_headers"])
    assert listed.status_code == 200
    assert {"ana@example.com", "bystander@example.com"} <= {c["email"] for c in listed.json()}

    # Same email, same mobile (phone_for is deterministic per email), same
    # job: a fresh verification, resume upload and AI screening.
    await _apply(client, ctx, "ana@example.com", full_name="Ana Again")
    fresh = await _snapshot(db_session, candidate_id)
    (new_application,) = fresh["applications"]
    assert new_application.id != old_application_id
    assert new_application.is_self_service is True
    assert fresh["screening_runs"] == 1
    assert await _file_exists(_file_of(fresh["resumes"][0]))
    async with rls_bypass(db_session):
        candidate = await db_session.get(Candidate, candidate_id)
        assert candidate is not None
        assert candidate.phone == phone_for("ana@example.com")

    # And the reapply window starts over from the new application.
    relocked = await _verify_outcome(client, ctx, "ana@example.com")
    assert relocked.status_code == 409
    assert relocked.json()["error"]["code"] == "reapply_locked"


async def test_hr_matched_application_and_its_shared_resume_file_are_kept(
    client: AsyncClient, db_session: AsyncSession, super_admin
) -> None:
    """An HR job match reuses the self-service resume's stored file. Only the
    self-service application is cleared; the match and the file stay."""
    ctx = await _bootstrap_org_with_open_job(client, "reapply-perm-shared")
    await _apply(client, ctx, "sam@example.com", full_name="Sam Shared")
    candidate_id = await _candidate_id(db_session, "sam@example.com")
    second_job = await _open_second_job(client, ctx)
    matched = await client.post(
        f"/api/v1/recruiter/candidates/{candidate_id}/job-matches",
        json={"job_id": second_job, "reason": "Better fit for platform work."},
        headers=ctx["admin_headers"],
    )
    assert matched.status_code == 201, matched.text

    granted = await client.post(_grant_url(str(candidate_id)), headers=ctx["admin_headers"])
    assert granted.status_code == 200, granted.text
    assert granted.json()["cleared_application_count"] == 1

    after = await _snapshot(db_session, candidate_id)
    (kept,) = after["applications"]
    assert kept.id == uuid.UUID(matched.json()["id"])
    assert kept.is_self_service is False
    (kept_resume,) = after["resumes"]
    assert kept_resume.application_id == kept.id
    assert await _file_exists(_file_of(kept_resume))  # shared file was not deleted


async def test_a_failure_part_way_rolls_back_rows_and_keeps_files(
    client: AsyncClient,
    db_session: AsyncSession,
    super_admin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reapply-perm-rollback")
    await _apply(client, ctx, "rollback@example.com", full_name="Rita Rollback")
    candidate_id = await _candidate_id(db_session, "rollback@example.com")
    before = await _snapshot(db_session, candidate_id)
    before_app_ids = [a.id for a in before["applications"]]
    before_resume_ids = [r.id for r in before["resumes"]]

    # Fails after every delete has been issued, just before the commit.
    async def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(activity_service, "record_activity", fail)
    with pytest.raises(RuntimeError, match="audit write failed"):
        await client.post(_grant_url(str(candidate_id)), headers=ctx["admin_headers"])
    monkeypatch.undo()

    after = await _snapshot(db_session, candidate_id)
    assert [a.id for a in after["applications"]] == before_app_ids
    assert [r.id for r in after["resumes"]] == before_resume_ids
    assert after["screening_runs"] == before["screening_runs"]
    assert after["status_history"] == before["status_history"]
    assert await _file_exists(_file_of(after["resumes"][0]))


async def test_nothing_is_deleted_when_the_candidate_can_already_apply(
    client: AsyncClient, db_session: AsyncSession, super_admin
) -> None:
    """A candidate HR added (no self-service history) isn't restricted."""
    ctx = await _bootstrap_org_with_open_job(client, "reapply-perm-noop")
    created = await client.post(
        "/api/v1/recruiter/candidates",
        json={"email": "free@example.com", "full_name": "Fran Free"},
        headers=ctx["admin_headers"],
    )
    assert created.status_code == 201, created.text

    response = await client.post(_grant_url(created.json()["id"]), headers=ctx["admin_headers"])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "reapply_not_restricted"
    assert "CANDIDATE_REAPPLY_PERMISSION_GRANTED" not in await _activities(client, ctx)


async def test_unknown_candidate_is_not_found(
    client: AsyncClient, db_session: AsyncSession, super_admin
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reapply-perm-missing")
    response = await client.post(_grant_url(str(uuid.uuid4())), headers=ctx["admin_headers"])
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Candidate not found."


async def test_a_storage_failure_after_commit_never_fails_the_grant(
    client: AsyncClient,
    db_session: AsyncSession,
    super_admin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Files are removed only after the database commit. If the file store is
    down at that point the grant still succeeds (the rows are gone and the
    candidate can apply); the leftover file is logged, never surfaced."""
    ctx = await _bootstrap_org_with_open_job(client, "reapply-perm-storage-down")
    await _apply(client, ctx, "store@example.com", full_name="Stella Store")
    candidate_id = await _candidate_id(db_session, "store@example.com")
    (resume,) = (await _snapshot(db_session, candidate_id))["resumes"]
    file = _file_of(resume)

    class _DownStorage:
        async def delete(self, storage_path: str) -> None:
            raise StorageError("Could not delete the stored file.")

    monkeypatch.setattr(
        reapply_service, "get_resume_storage_for_provider", lambda _p: _DownStorage()
    )
    granted = await client.post(_grant_url(str(candidate_id)), headers=ctx["admin_headers"])
    monkeypatch.undo()

    assert granted.status_code == 200, granted.text
    after = await _snapshot(db_session, candidate_id)
    assert after["applications"] == [] and after["resumes"] == []
    assert await _file_exists(file)  # left behind, not lost mid-transaction


async def test_a_blank_search_matches_no_one(
    client: AsyncClient, db_session: AsyncSession, super_admin
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "reapply-perm-blank")
    await _apply(client, ctx, "blank@example.com", full_name="Bea Blank")

    assert await _search(client, ctx, "    ") == []
