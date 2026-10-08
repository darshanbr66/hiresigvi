"""One-off, organization-scoped removal of demo/test recruitment data before
a production launch (e.g. the SIGVITAS tenant). Never run it without a fresh
PostgreSQL backup and a MongoDB/GridFS backup.

Modes (run from backend/, against whatever DATABASE_URL / MONGODB_URI the
environment holds — the target host and database are printed first):

    python scripts/cleanup_org_demo_data.py inventory --org-slug sigvitas
        Read-only. Users (and which would be kept), departments/employees
        (always kept), jobs, and per-table counts.

    python scripts/cleanup_org_demo_data.py dry-run --org-slug sigvitas [--delete-user EMAIL ...]
        Runs every DELETE inside one transaction, prints exact per-table
        counts (cascaded child rows included) and the files that would be
        removed, verifies that nothing outside the organization and nothing
        kept changed — then ROLLS BACK. Nothing is changed.

    python scripts/cleanup_org_demo_data.py execute --org-slug sigvitas \
        --confirm-org sigvitas [--files-backup-dir <empty dir>] [--delete-user EMAIL ...]
        With --files-backup-dir, first downloads every stored resume file
        that will be removed. Then the same transaction as dry-run —
        committed only if every check passes — and only after the commit
        removes the files from storage.

    --delete-all-org-data (any mode): a full reset of the organization's
        people/structure on top of the recruitment data — every department,
        every employee and every user who is not an ORG_ADMIN. The
        organization row and every ORG_ADMIN account are always kept.

Stored resume files that no longer exist (e.g. the GridFS database was
dropped, or MONGODB_URI is unset) are reported as ALREADY MISSING and treated
as already deleted; files that do exist are removed after the commit. A
storage that is configured but unreachable aborts the run.

REMOVED (all rows of the organization): candidates, jobs (+ requirements),
applications (+ status history, AI screening runs, notes), resumes (+ chunks,
derived profiles, stored files), assessments (+ questions/options,
invitations, answers, results, monitoring events), campus drives, AI match
results, reapply grants, calendar events (+ attendees), notifications,
pending email verifications — and only the user accounts named with
--delete-user (never an ORG_ADMIN), the departments named with
--delete-department (with every employee in them) and the employee records
named with --delete-employee. Talk-to-Admin conversations only with
--include-messages.

KEPT: the organization row (name, slug, careers contact), every ORG_ADMIN and
every other user not named, every department and employee not named, the whole
activity/audit log, roles and permissions, and every other organization's
data. Deleting a named user clears (SET NULL) their name on the activity
entries they made — the entries themselves stay; the dry run reports how many.
"""

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.asyncio_compat import configure_event_loop_policy  # noqa: E402

configure_event_loop_policy()

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.rls import set_rls_bypass, set_tenant_context  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.integrations.storage import get_resume_storage_for_provider  # noqa: E402

ORG_ADMIN = "ORG_ADMIN"

#: Every table holding the organization's rows, with how a row is tied to it.
#: Used for the before/after counts; child tables without `organization_id`
#: are counted through their parent.
SCOPED_COUNTS: dict[str, str] = {
    "candidates": "SELECT count(*) FROM candidates WHERE organization_id = :org",
    "jobs": "SELECT count(*) FROM jobs WHERE organization_id = :org",
    "job_requirements": "SELECT count(*) FROM job_requirements WHERE organization_id = :org",
    "applications": "SELECT count(*) FROM applications WHERE organization_id = :org",
    "application_status_history": (
        "SELECT count(*) FROM application_status_history h JOIN applications a "
        "ON a.id = h.application_id WHERE a.organization_id = :org"
    ),
    "screening_runs": "SELECT count(*) FROM screening_runs WHERE organization_id = :org",
    "resumes": "SELECT count(*) FROM resumes WHERE organization_id = :org",
    "resume_chunks": "SELECT count(*) FROM resume_chunks WHERE organization_id = :org",
    "candidate_resume_profiles": (
        "SELECT count(*) FROM candidate_resume_profiles WHERE organization_id = :org"
    ),
    "match_results": "SELECT count(*) FROM match_results WHERE organization_id = :org",
    "candidate_reapply_grants": (
        "SELECT count(*) FROM candidate_reapply_grants WHERE organization_id = :org"
    ),
    "notes": "SELECT count(*) FROM notes WHERE organization_id = :org",
    "assessments": "SELECT count(*) FROM assessments WHERE organization_id = :org",
    "questions": (
        "SELECT count(*) FROM questions q JOIN assessments s ON s.id = q.assessment_id "
        "WHERE s.organization_id = :org"
    ),
    "question_options": (
        "SELECT count(*) FROM question_options o JOIN questions q ON q.id = o.question_id "
        "JOIN assessments s ON s.id = q.assessment_id WHERE s.organization_id = :org"
    ),
    "assessment_invitations": (
        "SELECT count(*) FROM assessment_invitations WHERE organization_id = :org"
    ),
    "candidate_answers": (
        "SELECT count(*) FROM candidate_answers x JOIN assessment_invitations i "
        "ON i.id = x.invitation_id WHERE i.organization_id = :org"
    ),
    "assessment_results": (
        "SELECT count(*) FROM assessment_results x JOIN assessment_invitations i "
        "ON i.id = x.invitation_id WHERE i.organization_id = :org"
    ),
    "assessment_monitoring_events": (
        "SELECT count(*) FROM assessment_monitoring_events x JOIN assessment_invitations i "
        "ON i.id = x.invitation_id WHERE i.organization_id = :org"
    ),
    "campus_drives": "SELECT count(*) FROM campus_drives WHERE organization_id = :org",
    "calendar_events": "SELECT count(*) FROM calendar_events WHERE organization_id = :org",
    "calendar_event_attendees": (
        "SELECT count(*) FROM calendar_event_attendees x JOIN calendar_events e "
        "ON e.id = x.event_id WHERE e.organization_id = :org"
    ),
    "notifications": "SELECT count(*) FROM notifications WHERE organization_id = :org",
    "email_verifications": (
        "SELECT count(*) FROM email_verifications WHERE organization_id = :org"
    ),
    "admin_conversations": (
        "SELECT count(*) FROM admin_conversations WHERE organization_id = :org"
    ),
    "admin_messages": "SELECT count(*) FROM admin_messages WHERE organization_id = :org",
    "users": "SELECT count(*) FROM users WHERE organization_id = :org",
    "user_roles": (
        "SELECT count(*) FROM user_roles r JOIN users u ON u.id = r.user_id "
        "WHERE u.organization_id = :org"
    ),
    "user_refresh_tokens": (
        "SELECT count(*) FROM user_refresh_tokens t JOIN users u ON u.id = t.user_id "
        "WHERE u.organization_id = :org"
    ),
    # Kept — must not change (except as described for named users).
    "departments": "SELECT count(*) FROM departments WHERE organization_id = :org",
    "employees": "SELECT count(*) FROM employees WHERE organization_id = :org",
    "activities": "SELECT count(*) FROM activities WHERE organization_id = :org",
    "organizations": "SELECT count(*) FROM organizations WHERE id = :org",
}

#: Must have the same count before and after, whatever is removed.
#: (Departments/employees may lose only the ones named on the command line.)
KEPT_TABLES = ("activities", "organizations")

#: Recruitment/demo data: deleted in this order (children first where the
#: foreign key is RESTRICT). Each runs with the organization's id; the
#: database cascades the child tables listed alongside.
DELETE_STEPS: list[tuple[str, str]] = [
    ("notifications", "DELETE FROM notifications WHERE organization_id = :org"),
    ("calendar_events (+attendees)", "DELETE FROM calendar_events WHERE organization_id = :org"),
    ("notes", "DELETE FROM notes WHERE organization_id = :org"),
    ("match_results", "DELETE FROM match_results WHERE organization_id = :org"),
    (
        "candidate_resume_profiles",
        "DELETE FROM candidate_resume_profiles WHERE organization_id = :org",
    ),
    ("resume_chunks", "DELETE FROM resume_chunks WHERE organization_id = :org"),
    (
        "candidate_reapply_grants",
        "DELETE FROM candidate_reapply_grants WHERE organization_id = :org",
    ),
    (
        "assessment_invitations (+answers, results, monitoring events)",
        "DELETE FROM assessment_invitations WHERE organization_id = :org",
    ),
    ("resumes", "DELETE FROM resumes WHERE organization_id = :org"),
    (
        "applications (+status history, screening runs)",
        "DELETE FROM applications WHERE organization_id = :org",
    ),
    ("screening_runs (any left)", "DELETE FROM screening_runs WHERE organization_id = :org"),
    ("campus_drives", "DELETE FROM campus_drives WHERE organization_id = :org"),
    ("assessments (+questions, options)", "DELETE FROM assessments WHERE organization_id = :org"),
    ("job_requirements", "DELETE FROM job_requirements WHERE organization_id = :org"),
    ("jobs", "DELETE FROM jobs WHERE organization_id = :org"),
    ("candidates", "DELETE FROM candidates WHERE organization_id = :org"),
    ("email_verifications", "DELETE FROM email_verifications WHERE organization_id = :org"),
]

MESSAGE_STEPS: list[tuple[str, str]] = [
    ("admin_messages", "DELETE FROM admin_messages WHERE organization_id = :org"),
    ("admin_conversations", "DELETE FROM admin_conversations WHERE organization_id = :org"),
]

#: Must be zero for the organization after the cleanup.
MUST_BE_EMPTY = (
    "candidates", "jobs", "job_requirements", "applications", "application_status_history",
    "screening_runs", "resumes", "resume_chunks", "candidate_resume_profiles", "match_results",
    "candidate_reapply_grants", "notes", "assessments", "questions", "question_options",
    "assessment_invitations", "candidate_answers", "assessment_results",
    "assessment_monitoring_events", "campus_drives", "calendar_events",
    "calendar_event_attendees", "notifications", "email_verifications",
)


class CleanupAborted(Exception):
    pass


@dataclass
class Plan:
    org_id: uuid.UUID
    org_slug: str
    delete_user_ids: list[uuid.UUID] = field(default_factory=list)
    delete_department_ids: list[uuid.UUID] = field(default_factory=list)
    delete_employee_ids: list[uuid.UUID] = field(default_factory=list)
    include_messages: bool = False


# --- helpers -----------------------------------------------------------------------------


def _target_banner() -> None:
    settings = get_settings()
    url = make_url(settings.database_url)
    print(f"Target database : {url.host}:{url.port}/{url.database}")
    print(f"ENVIRONMENT     : {settings.environment}")
    print(f"Resume storage  : {settings.resume_storage_provider}"
          + (f" (Mongo db {settings.mongodb_database})" if settings.mongodb_uri else ""))
    print()


async def _find_org(db: AsyncSession, slug: str) -> tuple[uuid.UUID, str, str | None]:
    await set_rls_bypass(db, enabled=True)
    row = (
        await db.execute(
            text("SELECT id, name, careers_contact_email FROM organizations WHERE slug = :slug"),
            {"slug": slug},
        )
    ).first()
    await set_rls_bypass(db, enabled=False)
    if row is None:
        raise CleanupAborted(f"No organization with slug {slug!r}.")
    return row[0], row[1], row[2]


async def _scoped_counts(db: AsyncSession, org_id: uuid.UUID) -> dict[str, int]:
    return {
        table: int((await db.execute(text(sql), {"org": org_id})).scalar_one())
        for table, sql in SCOPED_COUNTS.items()
    }


async def _global_counts(db: AsyncSession) -> dict[str, int]:
    """Every table's total across all organizations (RLS bypassed), to prove
    nothing outside the organization changed."""
    await set_rls_bypass(db, enabled=True)
    tables = [
        r[0]
        for r in await db.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                "AND table_name <> 'alembic_version' ORDER BY table_name"
            )
        )
    ]
    counts = {
        t: int((await db.execute(text(f'SELECT count(*) FROM "{t}"'))).scalar_one())
        for t in tables
    }
    await set_rls_bypass(db, enabled=False)
    return counts


async def _users(db: AsyncSession, org_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            "SELECT u.id, u.email, u.full_name, u.is_active, "
            "coalesce(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL), '{}') "
            "FROM users u LEFT JOIN user_roles ur ON ur.user_id = u.id "
            "LEFT JOIN roles r ON r.id = ur.role_id "
            "WHERE u.organization_id = :org GROUP BY u.id ORDER BY u.email"
        ),
        {"org": org_id},
    )
    return [
        {"id": r[0], "email": r[1], "name": r[2], "active": r[3], "roles": list(r[4])}
        for r in rows
    ]


async def _structure(db: AsyncSession, org_id: uuid.UUID) -> list[dict[str, Any]]:
    """Every department and employee (removed ones included), for the
    inventory and for resolving --delete-department/--delete-employee."""
    rows = await db.execute(
        text(
            "SELECT d.id, d.name, d.deleted_at IS NOT NULL, e.id, e.full_name, e.email, "
            "e.deleted_at IS NOT NULL, u.email "
            "FROM departments d FULL JOIN employees e ON e.department_id = d.id "
            "LEFT JOIN users u ON u.id = e.user_id "
            "WHERE coalesce(d.organization_id, e.organization_id) = :org "
            "ORDER BY d.name NULLS LAST, e.full_name"
        ),
        {"org": org_id},
    )
    return [
        {
            "dept_id": r[0], "dept": r[1], "dept_removed": r[2], "emp_id": r[3],
            "emp": r[4], "email": r[5], "emp_removed": r[6], "login": r[7],
        }
        for r in rows
    ]


async def _resolve_structure_to_delete(
    db: AsyncSession,
    org_id: uuid.UUID,
    departments: list[str],
    employees: list[str],
    *,
    everything: bool = False,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Named departments (exact name, case-insensitive) with every employee
    in them, plus individually named employees (by email). Anything not found
    aborts — nothing is ever deleted by a pattern."""
    rows = await _structure(db, org_id)
    if everything:
        return (
            sorted({r["dept_id"] for r in rows if r["dept_id"]}),
            sorted({r["emp_id"] for r in rows if r["emp_id"]}),
        )
    dept_ids: list[uuid.UUID] = []
    emp_ids: set[uuid.UUID] = set()
    for name in departments:
        matches = {r["dept_id"] for r in rows if r["dept"] and r["dept"].lower() == name.lower()}
        if not matches:
            raise CleanupAborted(f"--delete-department {name!r}: no such department.")
        dept_ids.extend(matches)
        emp_ids.update(r["emp_id"] for r in rows if r["dept_id"] in matches and r["emp_id"])
    for email in employees:
        matches = {
            r["emp_id"] for r in rows
            if r["email"] and r["email"].lower() == email.strip().lower()
        }
        if not matches:
            raise CleanupAborted(f"--delete-employee {email}: no such employee.")
        emp_ids.update(matches)
    return dept_ids, sorted(emp_ids)


async def _resolve_users_to_delete(
    db: AsyncSession, org_id: uuid.UUID, emails: list[str], deleting_employees: list[uuid.UUID]
) -> list[uuid.UUID]:
    users = {u["email"].lower(): u for u in await _users(db, org_id)}
    ids: list[uuid.UUID] = []
    for email in emails:
        user = users.get(email.strip().lower())
        if user is None:
            raise CleanupAborted(f"--delete-user {email}: no such user in this organization.")
        if ORG_ADMIN in user["roles"]:
            raise CleanupAborted(f"--delete-user {email}: is an ORG_ADMIN — never deleted.")
        created_employees = (
            await db.execute(
                text(
                    "SELECT count(*) FROM employees WHERE created_by = :uid "
                    "AND NOT (id = ANY(:deleting))"
                ),
                {"uid": user["id"], "deleting": deleting_employees},
            )
        ).scalar_one()
        if created_employees:
            raise CleanupAborted(
                f"--delete-user {email}: created {created_employees} kept employee record(s), "
                "so the account can't be deleted without touching real employees. Deactivate "
                "it from Team → Accounts instead."
            )
        ids.append(user["id"])
    return ids


async def _files_for_org(db: AsyncSession, org_id: uuid.UUID) -> list[tuple[str, str]]:
    """Every stored file of the organization: those its resume rows and its
    candidates' 10th marks cards point to, plus (GridFS) any file whose
    metadata names the organization — which also catches files whose rows
    are already gone."""
    rows = await db.execute(
        text(
            "SELECT DISTINCT storage_provider, storage_path FROM resumes "
            "WHERE organization_id = :org "
            "UNION SELECT tenth_marks_card_storage_provider, tenth_marks_card_storage_path "
            "FROM candidates WHERE organization_id = :org "
            "AND tenth_marks_card_storage_path IS NOT NULL"
        ),
        {"org": org_id},
    )
    files = {(r[0], r[1]) for r in rows}
    if get_settings().mongodb_uri:
        from app.db.mongo import get_mongo_database

        collection = get_mongo_database()["resumes.files"]
        async for doc in collection.find(
            {"metadata.organization_id": str(org_id)}, {"_id": 1}
        ):
            files.add(("mongodb_gridfs", str(doc["_id"])))
    return sorted(files)


async def _still_referenced(db: AsyncSession, files: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """Files any remaining resume row or candidate marks card (of any
    organization) still points to."""
    await set_rls_bypass(db, enabled=True)
    referenced: set[tuple[str, str]] = set()
    for provider, path in files:
        hit = (
            await db.execute(
                text(
                    "SELECT 1 FROM resumes WHERE storage_provider = :p AND storage_path = :s "
                    "UNION ALL SELECT 1 FROM candidates "
                    "WHERE tenth_marks_card_storage_provider = :p "
                    "AND tenth_marks_card_storage_path = :s LIMIT 1"
                ),
                {"p": provider, "s": path},
            )
        ).first()
        if hit:
            referenced.add((provider, path))
    await set_rls_bypass(db, enabled=False)
    return referenced


async def _file_present(provider: str, path: str) -> bool:
    """Whether a stored resume file still exists. A file that is gone — the
    GridFS database was dropped, or GridFS isn't configured at all — is
    "missing", never an error. GridFS is asked through its `files`
    collection, so a server that can't be reached raises (and aborts the
    run) instead of being mistaken for "missing"."""
    if provider == "mongodb_gridfs":
        if not get_settings().mongodb_uri:
            return False
        from bson import ObjectId
        from bson.errors import InvalidId

        from app.db.mongo import get_mongo_database
        from app.integrations.storage.mongo_gridfs import BUCKET_NAME

        try:
            object_id = ObjectId(path)
        except (InvalidId, TypeError):
            return False
        found = await get_mongo_database()[f"{BUCKET_NAME}.files"].find_one(
            {"_id": object_id}, {"_id": 1}
        )
        return found is not None
    return await get_resume_storage_for_provider(provider).exists(path)


async def _classify_files(
    files: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(still stored, already missing)."""
    present: list[tuple[str, str]] = []
    missing: list[tuple[str, str]] = []
    for provider, path in files:
        (present if await _file_present(provider, path) else missing).append((provider, path))
    return present, missing


async def _backup_files(
    present: list[tuple[str, str]], missing: list[tuple[str, str]], backup_dir: Path
) -> list[dict[str, Any]]:
    """Downloads every file that still exists; the manifest also lists the
    ones that were already missing (nothing to back up)."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    if any(backup_dir.iterdir()):
        raise CleanupAborted(f"--files-backup-dir {backup_dir} is not empty.")
    manifest: list[dict[str, Any]] = []
    for provider, path in present:
        content = await get_resume_storage_for_provider(provider).read(path)
        name = f"{provider}__{path.replace('/', '_')}"
        (backup_dir / name).write_bytes(content)
        manifest.append(
            {"provider": provider, "storage_path": path, "file": name, "bytes": len(content)}
        )
    for provider, path in missing:
        manifest.append({"provider": provider, "storage_path": path, "status": "already missing"})
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


async def _delete_files(
    present: list[tuple[str, str]], referenced: set[tuple[str, str]]
) -> tuple[int, int]:
    """Removes the files that still exist and nothing references any more.
    Returns (removed, kept because still referenced)."""
    removed = kept = 0
    for provider, path in present:
        if (provider, path) in referenced:
            print(f"  kept (still referenced): {provider}: {path}")
            kept += 1
            continue
        await get_resume_storage_for_provider(provider).delete(path)
        removed += 1
    return removed, kept


def _print_files(present: list[tuple[str, str]], missing: list[tuple[str, str]]) -> None:
    print(
        f"\nStored resume files: {len(present)} to remove, "
        f"{len(missing)} ALREADY MISSING (nothing to remove)"
    )
    if not get_settings().mongodb_uri:
        print("  (MONGODB_URI is not set: GridFS files are treated as already missing)")
    for provider, path in present:
        print(f"  [remove        ] {provider}: {path}")
    for provider, path in missing:
        print(f"  [already missing] {provider}: {path}")


def _print_counts(before: dict[str, int], after: dict[str, int]) -> None:
    print(f"{'table':34} {'before':>8} {'after':>8} {'removed':>8}")
    for table in SCOPED_COUNTS:
        marker = "  (kept)" if table in KEPT_TABLES else ""
        print(
            f"{table:34} {before[table]:>8} {after[table]:>8} "
            f"{before[table] - after[table]:>8}{marker}"
        )


# --- the cleanup transaction ----------------------------------------------------------------


async def _run_deletes(db: AsyncSession, plan: Plan) -> dict[str, int]:
    """All deletes in the caller's transaction (tenant context set). Returns
    the rows each statement deleted directly."""
    deleted: dict[str, int] = {}
    steps = DELETE_STEPS + (MESSAGE_STEPS if plan.include_messages else [])
    for label, sql in steps:
        result = await db.execute(text(sql), {"org": plan.org_id})
        deleted[label] = int(result.rowcount or 0)  # type: ignore[attr-defined]
    if plan.delete_employee_ids:
        # Kept employees who reported to a removed one lose that link (SET NULL).
        result = await db.execute(
            text("DELETE FROM employees WHERE organization_id = :org AND id = ANY(:ids)"),
            {"org": plan.org_id, "ids": plan.delete_employee_ids},
        )
        deleted["employees (named / in named departments)"] = int(result.rowcount or 0)  # type: ignore[attr-defined]
    if plan.delete_department_ids:
        result = await db.execute(
            text("DELETE FROM departments WHERE organization_id = :org AND id = ANY(:ids)"),
            {"org": plan.org_id, "ids": plan.delete_department_ids},
        )
        deleted["departments (named)"] = int(result.rowcount or 0)  # type: ignore[attr-defined]
    if plan.delete_user_ids:
        result = await db.execute(
            text("DELETE FROM users WHERE organization_id = :org AND id = ANY(:ids)"),
            {"org": plan.org_id, "ids": plan.delete_user_ids},
        )
        deleted["users (named test accounts)"] = int(result.rowcount or 0)  # type: ignore[attr-defined]
    return deleted


def _verify(
    plan: Plan,
    before: dict[str, int],
    after: dict[str, int],
    global_before: dict[str, int],
    global_after: dict[str, int],
) -> list[str]:
    problems: list[str] = []
    for table in MUST_BE_EMPTY:
        if after[table] != 0:
            problems.append(f"{table}: {after[table]} row(s) of the organization remain")
    for table in KEPT_TABLES:
        if after[table] != before[table]:
            problems.append(f"{table} changed ({before[table]} -> {after[table]}) but is kept")
    if not plan.include_messages:
        for table in ("admin_conversations", "admin_messages"):
            # Only a named user's own conversations may go (cascade).
            if after[table] > before[table]:
                problems.append(f"{table} grew")
    for table, removing in (
        ("users", plan.delete_user_ids),
        ("employees", plan.delete_employee_ids),
        ("departments", plan.delete_department_ids),
    ):
        expected = before[table] - len(removing)
        if after[table] != expected:
            problems.append(f"{table}: expected {expected}, found {after[table]}")
    # Nothing outside the organization: every table's global drop must equal
    # the organization's own drop.
    for table, total in global_before.items():
        dropped = total - global_after.get(table, 0)
        own = before.get(table, 0) - after.get(table, 0)
        if table in before and dropped != own:
            problems.append(f"{table}: {dropped} row(s) removed globally, {own} in the org")
        if table not in before and dropped:
            problems.append(f"{table}: {dropped} row(s) removed outside the counted tables")
    return problems


async def run(mode: str, args: argparse.Namespace) -> int:
    _target_banner()
    async with AsyncSessionLocal() as db:
        org_id, org_name, contact = await _find_org(db, args.org_slug)
        print(f"Organization    : {org_name} ({args.org_slug}) id={org_id}")
        print(f"Careers contact : {'set' if contact else 'not set'} (kept)\n")
        await db.commit()

        # Everything below runs in ONE transaction scoped to this tenant.
        await set_tenant_context(db, org_id)
        users = await _users(db, org_id)
        if args.delete_all_org_data:
            if not any(ORG_ADMIN in u["roles"] for u in users):
                raise CleanupAborted(
                    "--delete-all-org-data: the organization has no ORG_ADMIN — refusing, "
                    "it would leave nobody able to sign in."
                )
            args.delete_user = sorted(
                {*args.delete_user, *(u["email"] for u in users if ORG_ADMIN not in u["roles"])}
            )
        print("Users:")
        named = {e.strip().lower() for e in args.delete_user}
        for u in users:
            action = "DELETE" if u["email"].lower() in named else "keep"
            print(f"  [{action:6}] {u['email']:40} {','.join(u['roles']) or '-':16} "
                  f"{'active' if u['active'] else 'inactive'}")
        dept_ids, emp_ids = await _resolve_structure_to_delete(
            db,
            org_id,
            args.delete_department,
            args.delete_employee,
            everything=args.delete_all_org_data,
        )
        print("Departments and employees (kept unless named):")
        current: object = object()
        for r in await _structure(db, org_id):
            if r["dept_id"] != current:
                current = r["dept_id"]
                if r["dept_id"]:
                    action = "DELETE" if r["dept_id"] in dept_ids else "keep"
                    gone = " (already removed)" if r["dept_removed"] else ""
                    print(f"  [{action:6}] department {r['dept']}{gone}")
                else:
                    print("           (no department)")
            if r["emp_id"]:
                action = "DELETE" if r["emp_id"] in emp_ids else "keep"
                notes = []
                if r["emp_removed"]:
                    notes.append("already removed")
                if r["login"]:
                    notes.append(f"login {r['login']} is NOT deleted by this")
                extra = f" ({'; '.join(notes)})" if notes else ""
                print(f"      [{action:6}] {r['emp']} <{r['email']}>{extra}")
        jobs = (await db.execute(
            text("SELECT title, status, deleted_at IS NOT NULL FROM jobs "
                 "WHERE organization_id = :org ORDER BY title"), {"org": org_id})).all()
        print(f"Jobs (all removed): {len(jobs)}")
        for title, status, deleted in jobs:
            print(f"  - {title} [{status}{', archived' if deleted else ''}]")

        before = await _scoped_counts(db, org_id)
        files = await _files_for_org(db, org_id)
        present, missing = await _classify_files(files)
        print(
            f"\nStored resume files of the organization: {len(files)} "
            f"({len(present)} present, {len(missing)} already missing)"
        )

        if mode == "inventory":
            print()
            for table, count in before.items():
                print(f"  {table:34} {count:>8}")
            await db.rollback()
            return 0

        plan = Plan(
            org_id=org_id,
            org_slug=args.org_slug,
            delete_user_ids=await _resolve_users_to_delete(
                db, org_id, args.delete_user, emp_ids
            ),
            delete_department_ids=dept_ids,
            delete_employee_ids=emp_ids,
            include_messages=args.include_messages,
        )
        if emp_ids:
            orphaned_reports = int((await db.execute(
                text("SELECT count(*) FROM employees WHERE manager_id = ANY(:ids) "
                     "AND NOT (id = ANY(:ids))"), {"ids": emp_ids})).scalar_one())
            if orphaned_reports:
                print(f"\nKept employees losing a removed manager link: {orphaned_reports}")
        activities_losing_actor = 0
        if plan.delete_user_ids:
            activities_losing_actor = int((await db.execute(
                text("SELECT count(*) FROM activities WHERE actor_user_id = ANY(:ids)"),
                {"ids": plan.delete_user_ids})).scalar_one())

        if mode == "execute" and args.files_backup_dir:
            await _backup_files(present, missing, Path(args.files_backup_dir))
            print(
                f"Backed up {len(present)} file(s) to {args.files_backup_dir} "
                f"({len(missing)} already missing — nothing to back up)\n"
            )

        global_before = await _global_counts(db)
        await set_tenant_context(db, org_id)
        deleted = await _run_deletes(db, plan)
        after = await _scoped_counts(db, org_id)
        global_after = await _global_counts(db)
        await set_tenant_context(db, org_id)

        print("\nRows deleted directly by each statement:")
        for label, count in deleted.items():
            print(f"  {label:58} {count:>6}")
        print("\nPer-table counts for the organization (cascades included):")
        _print_counts(before, after)
        if activities_losing_actor:
            print(f"\nActivity entries kept but losing their actor name: {activities_losing_actor}")
        _print_files(present, missing)

        problems = _verify(plan, before, after, global_before, global_after)
        if problems:
            await db.rollback()
            print("\nABORTED — rolled back, nothing changed:")
            for p in problems:
                print(f"  - {p}")
            return 1

        if mode == "dry-run":
            await db.rollback()
            print("\nDRY RUN — all checks passed; everything ROLLED BACK. Nothing was changed.")
            return 0

        await db.commit()
        print("\nCOMMITTED.")
        referenced = await _still_referenced(db, present)
        removed, kept = await _delete_files(present, referenced)
        print(
            f"Removed {removed} file(s) from storage; {len(missing)} were already missing"
            + (f"; {kept} kept (still referenced)." if kept else ".")
        )
        return 0


async def _with_storage(args: argparse.Namespace) -> int:
    """GridFS needs the process-wide Mongo client the app opens at startup."""
    from app.db.mongo import connect_mongo, disconnect_mongo

    if get_settings().mongodb_uri:
        await connect_mongo()
    try:
        return await run(args.mode, args)
    finally:
        await disconnect_mongo()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("mode", choices=["inventory", "dry-run", "execute"])
    parser.add_argument("--org-slug", required=True)
    parser.add_argument("--delete-user", action="append", default=[], metavar="EMAIL",
                        help="A non-admin test account to delete (repeatable).")
    parser.add_argument("--delete-department", action="append", default=[], metavar="NAME",
                        help="A demo department to delete with every employee in it (repeatable).")
    parser.add_argument("--delete-employee", action="append", default=[], metavar="EMAIL",
                        help="A demo employee record to delete (repeatable).")
    parser.add_argument(
        "--delete-all-org-data",
        action="store_true",
        help="Also delete every department, every employee and every non-ORG_ADMIN user "
        "(the organization and its ORG_ADMINs are kept).",
    )
    parser.add_argument("--include-messages", action="store_true",
                        help="Also delete Talk-to-Admin conversations.")
    parser.add_argument("--confirm-org", help="execute only: must equal --org-slug.")
    parser.add_argument(
        "--files-backup-dir", help="execute only: empty directory for file backups."
    )
    args = parser.parse_args()
    if args.mode == "execute":
        if args.confirm_org != args.org_slug:
            parser.error("execute needs --confirm-org equal to --org-slug.")
    try:
        return asyncio.run(_with_storage(args))
    except CleanupAborted as exc:
        print(f"ABORTED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
