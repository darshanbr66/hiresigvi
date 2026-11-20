"""The data migration that sets Zodiac Patent's public recruitment contact
(alembic 8b9c0d1e2f3a). Runs the real upgrade()/downgrade() against the test
connection, inside the per-test transaction that is always rolled back —
nothing persists."""

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "8b9c0d1e2f3a_set_zodiac_patent_careers_contact.py"
)
_spec = importlib.util.spec_from_file_location("set_careers_contact", _PATH)
assert _spec and _spec.loader
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

NEW = "hr@zodiacpatent.com"


def _run(sync_connection, step) -> None:
    with Operations.context(MigrationContext.configure(sync_connection)):
        step()


async def _bypass(connection: AsyncConnection) -> None:
    await connection.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))


async def _contact(connection: AsyncConnection, slug: str) -> str | None:
    await _bypass(connection)
    result = await connection.execute(
        text("SELECT careers_contact_email FROM organizations WHERE slug = :slug"), {"slug": slug}
    )
    return result.scalar_one()


async def _careers_org_with(connection: AsyncConnection, contact: str | None) -> None:
    """Whatever this database holds, start from a `zodiac-patent` row whose
    contact is `contact`."""
    await _bypass(connection)
    updated = await connection.execute(
        text("UPDATE organizations SET careers_contact_email = :c WHERE slug = 'zodiac-patent'"),
        {"c": contact},
    )
    if updated.rowcount == 0:
        await connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, careers_contact_email) "
                "VALUES (:id, 'Zodiac Patent', 'zodiac-patent', :c)"
            ),
            {"id": uuid.uuid4(), "c": contact},
        )


@pytest.mark.parametrize("old", ["dbr@sigvitas.com", "HR@Sigvitas.com", None, "  "])
async def test_upgrade_replaces_a_sigvitas_or_missing_contact(
    db_connection: AsyncConnection, old: str | None
) -> None:
    await _careers_org_with(db_connection, old)

    await db_connection.run_sync(_run, migration.upgrade)
    assert await _contact(db_connection, "zodiac-patent") == NEW

    # downgrade never re-publishes the Sigvitas address
    await db_connection.run_sync(_run, migration.downgrade)
    assert await _contact(db_connection, "zodiac-patent") == NEW


async def test_upgrade_keeps_a_contact_that_was_set_deliberately(
    db_connection: AsyncConnection,
) -> None:
    await _careers_org_with(db_connection, "careers@zodiacpatent.com")

    await db_connection.run_sync(_run, migration.upgrade)
    assert await _contact(db_connection, "zodiac-patent") == "careers@zodiacpatent.com"


async def test_upgrade_leaves_other_organizations_alone(db_connection: AsyncConnection) -> None:
    await _careers_org_with(db_connection, "dbr@sigvitas.com")
    other_slug = f"other-{uuid.uuid4().hex[:8]}"
    await db_connection.execute(
        text(
            "INSERT INTO organizations (id, name, slug, careers_contact_email) "
            "VALUES (:id, 'Other', :slug, 'dbr@sigvitas.com')"
        ),
        {"id": uuid.uuid4(), "slug": other_slug},
    )

    await db_connection.run_sync(_run, migration.upgrade)
    assert await _contact(db_connection, other_slug) == "dbr@sigvitas.com"
    assert await _contact(db_connection, "zodiac-patent") == NEW
