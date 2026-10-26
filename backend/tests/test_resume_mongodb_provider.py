"""End-to-end (through the real HTTP API) resume upload/download with
RESUME_STORAGE_PROVIDER effectively "mongodb_gridfs" for the duration of
each test — backed by the in-memory fake GridFS bucket
(tests/fakes_mongo.py), never a real MongoDB deployment. Mirrors
tests/test_public_applications.py's local-storage coverage so the same
tenant-isolation/API-contract guarantees are verified against the Mongo
backend too (CLAUDE.md task § 13: "existing resume API behavior",
"tenant authorization").
"""

import pytest
from httpx import AsyncClient

from app.integrations.storage import _PROVIDERS, MongoGridFSResumeStorage
from app.models.user import User
from tests.conftest import SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD, login
from tests.fakes_mongo import FakeAsyncGridFSBucket
from tests.public_apply import apply_publicly

_JOB_PAYLOAD = {
    "title": "Backend Engineer",
    "department": "Engineering",
    "location": "Remote",
    "employment_type": "FULL_TIME",
    "description": "Own the recruitment platform's backend.",
    "openings_count": 1,
}


@pytest.fixture
def mongodb_gridfs_provider(monkeypatch):
    """Makes `get_resume_storage()`/`get_resume_storage_for_provider(...)`
    resolve to a `MongoGridFSResumeStorage` backed by a shared in-memory
    fake bucket for this test only — every other test keeps the real
    "local" default (backend/.env has no RESUME_STORAGE_PROVIDER set)."""
    shared_bucket = FakeAsyncGridFSBucket(None, bucket_name="resumes")

    class _FakeMongoGridFSResumeStorage(MongoGridFSResumeStorage):
        def __init__(self) -> None:
            self._bucket = shared_bucket

    monkeypatch.setitem(_PROVIDERS, "mongodb_gridfs", _FakeMongoGridFSResumeStorage)

    import app.integrations.storage as storage_module

    class _FakeSettings:
        resume_storage_provider = "mongodb_gridfs"

    monkeypatch.setattr(storage_module, "get_settings", lambda: _FakeSettings())
    return shared_bucket


async def _bootstrap_org_with_open_job(client: AsyncClient, slug: str) -> dict:
    super_admin_tokens = await login(client, email=SUPER_ADMIN_EMAIL, password=SUPER_ADMIN_PASSWORD)
    org_payload = {
        "name": "Acme Corp",
        "slug": slug,
        "admin_email": f"admin@{slug}.dev",
        "admin_password": "AcmeAdminPass1",
        "admin_full_name": "Acme Admin",
    }
    org_response = await client.post(
        "/api/v1/admin/organizations",
        json=org_payload,
        headers={"Authorization": f"Bearer {super_admin_tokens['access_token']}"},
    )
    assert org_response.status_code == 201, org_response.text

    admin_tokens = await login(
        client, email=org_payload["admin_email"], password=org_payload["admin_password"]
    )
    admin_headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

    job_response = await client.post(
        "/api/v1/recruiter/jobs", json=_JOB_PAYLOAD, headers=admin_headers
    )
    assert job_response.status_code == 201, job_response.text
    job = job_response.json()

    publish_response = await client.patch(
        f"/api/v1/recruiter/jobs/{job['id']}", json={"status": "OPEN"}, headers=admin_headers
    )
    assert publish_response.status_code == 200

    return {"slug": slug, "job_id": job["id"], "admin_headers": admin_headers}


async def test_apply_stores_resume_in_gridfs_and_is_downloadable(
    client: AsyncClient, super_admin: User, mongodb_gridfs_provider
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "mongo-apply-happy")

    response = await apply_publicly(
        client,
        ctx["slug"],
        ctx["job_id"],
        email="jane@example.com",
        resume=("resume.pdf", b"%PDF-1.4 gridfs resume content", "application/pdf"),
    )
    assert response.status_code == 201, response.text

    applications = await client.get(
        "/api/v1/recruiter/applications", headers=ctx["admin_headers"]
    )
    application = applications.json()[0]
    assert application["resume_filename"] == "resume.pdf"

    # Exactly one GridFS file exists, and it's the one the Postgres
    # `resumes` row (app/models/resume.py) references by ObjectId.
    stored_files = mongodb_gridfs_provider.files
    assert len(stored_files) == 1
    record = next(iter(stored_files.values()))
    assert record["metadata"]["organization_id"]
    assert record["metadata"]["candidate_id"]
    assert record["metadata"]["application_id"]
    assert record["metadata"]["storage_version"] == 1
    assert record["content"] == b"%PDF-1.4 gridfs resume content"

    download = await client.get(
        f"/api/v1/recruiter/applications/{application['id']}/resume",
        headers=ctx["admin_headers"],
    )
    assert download.status_code == 200
    assert download.content == b"%PDF-1.4 gridfs resume content"
    assert download.headers["content-type"] == "application/pdf"


async def test_cross_tenant_download_is_still_blocked_on_gridfs(
    client: AsyncClient, super_admin: User, mongodb_gridfs_provider
) -> None:
    """Authorization for a Mongo-backed resume still comes entirely from
    Postgres application/organization checks (CLAUDE.md task § 9) — never
    from anything in GridFS metadata."""
    ctx_a = await _bootstrap_org_with_open_job(client, "mongo-tenant-a")
    ctx_b = await _bootstrap_org_with_open_job(client, "mongo-tenant-b")

    await apply_publicly(
        client,
        ctx_a["slug"],
        ctx_a["job_id"],
        email="jane@example.com",
        resume=("resume.pdf", b"%PDF-1.4 content", "application/pdf"),
    )
    applications = await client.get(
        "/api/v1/recruiter/applications", headers=ctx_a["admin_headers"]
    )
    application_id = applications.json()[0]["id"]

    cross_tenant_download = await client.get(
        f"/api/v1/recruiter/applications/{application_id}/resume",
        headers=ctx_b["admin_headers"],
    )
    assert cross_tenant_download.status_code == 404
