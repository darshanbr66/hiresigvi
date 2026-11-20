"""The data migration that renames the careers organization (alembic
7a8b9c0d1e2f). Runs the real upgrade()/downgrade() against the test
connection, inside the per-test transaction that is always rolled back —
nothing persists."""

import importlib.util
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "7a8b9c0d1e2f_rename_careers_org_to_zodiac_patent.py"
)
_spec = importlib.util.spec_from_file_location("rename_careers_org", _PATH)
assert _spec and _spec.loader
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


def _run(sync_connection, step) -> None:
    with Operations.context(MigrationContext.configure(sync_connection)):
        step()


async def _rows(connection: AsyncConnection) -> dict[str, tuple[uuid.UUID, str]]:
    await connection.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
    result = await connection.execute(
        text("SELECT slug, id, name FROM organizations WHERE slug IN ('sigvitas', 'zodiac-patent')")
    )
    return {slug: (org_id, name) for slug, org_id, name in result}


async def _ensure_only_old_row(connection: AsyncConnection) -> uuid.UUID:
    """Whatever this database holds, start from: `sigvitas` exists (named as
    originally), `zodiac-patent` does not."""
    rows = await _rows(connection)
    if "zodiac-patent" in rows:
        # An already-migrated database: move that row aside rather than
        # delete it (users/jobs reference it). The test transaction is
        # rolled back, so this never persists.
        await connection.execute(
            text("UPDATE organizations SET slug = :slug WHERE slug = 'zodiac-patent'"),
            {"slug": f"moved-aside-{uuid.uuid4().hex[:8]}"},
        )
    if "sigvitas" in rows:
        await connection.execute(
            text("UPDATE organizations SET name = 'Sigvitas Pvt. Ltd.' WHERE slug = 'sigvitas'")
        )
        return rows["sigvitas"][0]
    org_id = uuid.uuid4()
    await connection.execute(
        text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'Sigvitas Pvt. Ltd.', 'sigvitas')"),
        {"id": org_id},
    )
    return org_id


async def test_upgrade_renames_slug_and_name_and_keeps_the_id(
    db_connection: AsyncConnection,
) -> None:
    org_id = await _ensure_only_old_row(db_connection)

    await db_connection.run_sync(_run, migration.upgrade)
    rows = await _rows(db_connection)
    assert "sigvitas" not in rows
    assert rows["zodiac-patent"] == (org_id, "Zodiac Patent")

    await db_connection.run_sync(_run, migration.downgrade)
    rows = await _rows(db_connection)
    assert "zodiac-patent" not in rows
    assert rows["sigvitas"] == (org_id, "Sigvitas Pvt. Ltd.")


async def test_upgrade_keeps_a_name_that_was_already_changed(
    db_connection: AsyncConnection,
) -> None:
    await _ensure_only_old_row(db_connection)
    await db_connection.execute(
        text("UPDATE organizations SET name = 'Custom Name' WHERE slug = 'sigvitas'")
    )

    await db_connection.run_sync(_run, migration.upgrade)
    assert (await _rows(db_connection))["zodiac-patent"][1] == "Custom Name"


async def test_upgrade_is_a_no_op_when_the_new_slug_is_taken(
    db_connection: AsyncConnection,
) -> None:
    old_id = await _ensure_only_old_row(db_connection)
    other_id = uuid.uuid4()
    await db_connection.execute(
        text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'Other', 'zodiac-patent')"),
        {"id": other_id},
    )

    await db_connection.run_sync(_run, migration.upgrade)
    rows = await _rows(db_connection)
    assert rows["sigvitas"][0] == old_id
    assert rows["zodiac-patent"] == (other_id, "Other")
