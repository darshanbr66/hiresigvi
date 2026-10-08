"""scripts/cleanup_org_demo_data.py — the one-off organization cleanup.

- Stored resume files that are already gone (the GridFS database was dropped,
  or GridFS isn't configured) are reported as missing and skipped, never
  fatal; files that still exist are backed up and deleted. An unreachable
  GridFS aborts instead of being mistaken for "missing".
- --delete-all-org-data removes every department, employee and non-ORG_ADMIN
  user plus all recruitment data, keeping the organization and its ORG_ADMINs,
  and touches no other organization. Run inside the test's rolled-back
  transaction: nothing is committed.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from bson import ObjectId
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.mongo as mongo_module
from app.core.config import get_settings
from app.db.rls import rls_bypass, set_tenant_context
from app.models.user import User
from app.services import screening_service
from tests.public_apply import apply_publicly
from tests.test_candidate_intake import _StubScreeningProvider
from tests.test_public_applications import _bootstrap_org_with_open_job


def _load_script() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_org_demo_data.py"
    spec = importlib.util.spec_from_file_location("cleanup_org_demo_data", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cleanup = _load_script()

PRESENT_ID = str(ObjectId())
MISSING_ID = "6ab22d4d17f2577e335d6792"  # the production NoFile id


class _FakeFilesCollection:
    def __init__(self, existing: set[str], *, unreachable: bool = False) -> None:
        self.existing = existing
        self.unreachable = unreachable

    async def find_one(self, query: dict[str, Any], _projection: Any = None) -> Any:
        if self.unreachable:
            raise ConnectionError("server selection timeout")
        return {"_id": query["_id"]} if str(query["_id"]) in self.existing else None


class _FakeStorage:
    def __init__(self) -> None:
        self.read_paths: list[str] = []
        self.deleted: list[str] = []

    async def read(self, path: str) -> bytes:
        self.read_paths.append(path)
        if path != PRESENT_ID:
            raise AssertionError(f"a missing file must never be read: {path}")
        return b"%PDF-1.4 resume"

    async def delete(self, path: str) -> None:
        self.deleted.append(path)


@pytest.fixture
def gridfs(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(*, uri: str | None = "mongodb://fake", unreachable: bool = False) -> _FakeStorage:
        settings = get_settings().model_copy(update={"mongodb_uri": uri})
        monkeypatch.setattr(cleanup, "get_settings", lambda: settings)
        collection = _FakeFilesCollection({PRESENT_ID}, unreachable=unreachable)
        monkeypatch.setattr(
            mongo_module, "get_mongo_database", lambda: {"resumes.files": collection}
        )
        storage = _FakeStorage()
        monkeypatch.setattr(cleanup, "get_resume_storage_for_provider", lambda _p: storage)
        return storage

    return install


FILES = [
    ("mongodb_gridfs", PRESENT_ID),
    ("mongodb_gridfs", MISSING_ID),
    ("mongodb_gridfs", "not-an-object-id"),
]


async def test_missing_gridfs_files_are_already_deleted_not_fatal(
    gridfs: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = gridfs()

    present, missing = await cleanup._classify_files(FILES)
    assert present == [("mongodb_gridfs", PRESENT_ID)]
    assert missing == [("mongodb_gridfs", MISSING_ID), ("mongodb_gridfs", "not-an-object-id")]

    manifest = await cleanup._backup_files(present, missing, tmp_path / "backup")
    assert storage.read_paths == [PRESENT_ID]  # the missing one is never read
    assert (tmp_path / "backup" / f"mongodb_gridfs__{PRESENT_ID}").read_bytes().startswith(b"%PDF")
    statuses = {m["storage_path"]: m.get("status") for m in manifest}
    assert statuses == {
        PRESENT_ID: None,
        MISSING_ID: "already missing",
        "not-an-object-id": "already missing",
    }
    assert json.loads((tmp_path / "backup" / "manifest.json").read_text()) == manifest

    removed, kept = await cleanup._delete_files(present, referenced=set())
    assert (removed, kept) == (1, 0)
    assert storage.deleted == [PRESENT_ID]  # existing files are still deleted

    cleanup._print_files(present, missing)
    out = capsys.readouterr().out
    assert "1 to remove, 2 ALREADY MISSING" in out
    assert f"[already missing] mongodb_gridfs: {MISSING_ID}" in out


async def test_a_file_another_resume_still_references_is_kept(gridfs: Any) -> None:
    storage = gridfs()
    present = [("mongodb_gridfs", PRESENT_ID)]
    removed, kept = await cleanup._delete_files(present, referenced=set(present))
    assert (removed, kept) == (0, 1)
    assert storage.deleted == []


async def test_without_mongodb_uri_gridfs_files_count_as_missing(
    gridfs: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    gridfs(uri=None)
    present, missing = await cleanup._classify_files(FILES)
    assert present == []
    assert len(missing) == 3
    cleanup._print_files(present, missing)
    assert "MONGODB_URI is not set" in capsys.readouterr().out


async def test_an_unreachable_gridfs_aborts_instead_of_counting_as_missing(gridfs: Any) -> None:
    gridfs(unreachable=True)
    with pytest.raises(ConnectionError):
        await cleanup._classify_files(FILES)


async def test_backup_dir_must_be_empty(gridfs: Any, tmp_path: Path) -> None:
    gridfs()
    (tmp_path / "leftover.txt").write_text("x")
    with pytest.raises(cleanup.CleanupAborted):
        await cleanup._backup_files([], [], tmp_path)


# --- --delete-all-org-data, end to end inside a rolled-back transaction -----------------------


async def _org_id(db: AsyncSession, slug: str) -> Any:
    """`organizations` is itself under RLS (org_column=id): resolving a slug
    happens before any tenant is known, exactly as the script's _find_org."""
    async with rls_bypass(db):
        return (
            await db.execute(text("SELECT id FROM organizations WHERE slug = :s"), {"s": slug})
        ).scalar_one()


async def _seed_people(client: AsyncClient, ctx: dict, slug: str) -> None:
    headers = ctx["admin_headers"]
    for email, role in ((f"recruiter@{slug}.dev", "RECRUITER"), (f"int@{slug}.dev", "INTERVIEWER")):
        created = await client.post(
            "/api/v1/recruiter/users",
            json={"email": email, "full_name": role.title(), "password": "TeamMemberPass1",
                  "role": role},
            headers=headers,
        )
        assert created.status_code == 201, created.text
    for name, people in (("Testing", ["t1", "t2", "t3"]), ("Engineering", ["eve"])):
        dept = await client.post(
            "/api/v1/recruiter/departments", json={"name": name}, headers=headers
        )
        assert dept.status_code == 201, dept.text
        for person in people:
            employee = await client.post(
                "/api/v1/recruiter/employees",
                json={"full_name": person, "email": f"{person}@{slug}.dev",
                      "department_id": dept.json()["id"]},
                headers=headers,
            )
            assert employee.status_code == 201, employee.text


async def test_delete_all_org_data_keeps_only_the_org_and_its_admins(
    client: AsyncClient,
    super_admin: User,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _StubScreeningProvider("MATCH")
    monkeypatch.setattr(screening_service, "get_llm_provider", lambda: provider)
    target = await _bootstrap_org_with_open_job(client, "cleanup-target")
    other = await _bootstrap_org_with_open_job(client, "cleanup-other")
    for ctx, slug in ((target, "cleanup-target"), (other, "cleanup-other")):
        await _seed_people(client, ctx, slug)
        applied = await apply_publicly(client, slug, ctx["job_id"], email=f"cand@{slug}.dev")
        assert applied.status_code == 201, applied.text

    org_id = await _org_id(db_session, "cleanup-target")
    other_id = await _org_id(db_session, "cleanup-other")
    await set_tenant_context(db_session, other_id)
    other_before = await cleanup._scoped_counts(db_session, other_id)

    await set_tenant_context(db_session, org_id)
    users = await cleanup._users(db_session, org_id)
    non_admins = [u["email"] for u in users if cleanup.ORG_ADMIN not in u["roles"]]
    assert sorted(non_admins) == ["int@cleanup-target.dev", "recruiter@cleanup-target.dev"]
    dept_ids, emp_ids = await cleanup._resolve_structure_to_delete(
        db_session, org_id, [], [], everything=True
    )
    assert (len(dept_ids), len(emp_ids)) == (2, 4)
    plan = cleanup.Plan(
        org_id=org_id,
        org_slug="cleanup-target",
        delete_user_ids=await cleanup._resolve_users_to_delete(
            db_session, org_id, non_admins, emp_ids
        ),
        delete_department_ids=dept_ids,
        delete_employee_ids=emp_ids,
    )

    before = await cleanup._scoped_counts(db_session, org_id)
    assert before["applications"] == 1 and before["screening_runs"] == 1
    assert before["resumes"] == 1 and before["jobs"] == 1
    global_before = await cleanup._global_counts(db_session)
    await set_tenant_context(db_session, org_id)
    await cleanup._run_deletes(db_session, plan)
    after = await cleanup._scoped_counts(db_session, org_id)
    global_after = await cleanup._global_counts(db_session)

    assert cleanup._verify(plan, before, after, global_before, global_after) == []
    for table in cleanup.MUST_BE_EMPTY + ("departments", "employees"):
        assert after[table] == 0, table
    assert after["users"] == 1  # the ORG_ADMIN
    assert after["organizations"] == 1
    await set_tenant_context(db_session, org_id)
    remaining = await cleanup._users(db_session, org_id)
    assert [u["roles"] for u in remaining] == [["ORG_ADMIN"]]

    # The other organization is untouched.
    await set_tenant_context(db_session, other_id)
    assert await cleanup._scoped_counts(db_session, other_id) == other_before
