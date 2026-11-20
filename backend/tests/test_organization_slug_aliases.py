"""Renamed careers slugs keep working (settings.organization_slug_aliases).

The organization this deployment serves moved from `sigvitas` to
`zodiac-patent` (alembic 7a8b9c0d1e2f). Public lookups by the old slug must
resolve to the renamed organization, the new slug must work even before the
rename has been applied, and an exact slug must always win over an alias.
Uses throwaway slugs so the tests never depend on the real organization row.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.rls import rls_bypass
from app.models.organization import Organization
from app.services import organization_service

OLD = "alias-test-old-slug"
NEW = "alias-test-new-slug"


@pytest.fixture(autouse=True)
def _aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    patched = get_settings().model_copy(update={"organization_slug_aliases": {OLD: NEW}})
    monkeypatch.setattr("app.services.organization_service.get_settings", lambda: patched)


async def _org(db: AsyncSession, *, name: str, slug: str) -> Organization:
    async with rls_bypass(db):
        organization = Organization(name=name, slug=slug)
        db.add(organization)
        await db.flush()
    return organization


async def test_old_slug_resolves_to_the_renamed_organization(db_session: AsyncSession) -> None:
    renamed = await _org(db_session, name="Renamed Co", slug=NEW)

    assert (await organization_service.get_organization_by_slug(db_session, OLD)).id == renamed.id
    assert (await organization_service.get_organization_by_slug(db_session, NEW)).id == renamed.id


async def test_new_slug_works_before_the_rename_is_applied(db_session: AsyncSession) -> None:
    not_yet_renamed = await _org(db_session, name="Old Co", slug=OLD)

    found = await organization_service.get_organization_by_slug(db_session, NEW)
    assert found is not None and found.id == not_yet_renamed.id


async def test_an_exact_slug_always_wins_over_an_alias(db_session: AsyncSession) -> None:
    old = await _org(db_session, name="Old Co", slug=OLD)
    new = await _org(db_session, name="New Co", slug=NEW)

    assert (await organization_service.get_organization_by_slug(db_session, OLD)).id == old.id
    assert (await organization_service.get_organization_by_slug(db_session, NEW)).id == new.id


async def test_unknown_slugs_still_resolve_to_nothing(db_session: AsyncSession) -> None:
    await _org(db_session, name="Renamed Co", slug=NEW)
    assert await organization_service.get_organization_by_slug(db_session, "no-such-org") is None


async def test_public_careers_api_answers_on_the_old_slug(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _org(db_session, name="Renamed Co", slug=NEW)

    old = await client.get(f"/api/v1/public/organizations/{OLD}")
    new = await client.get(f"/api/v1/public/organizations/{NEW}")
    jobs = await client.get(f"/api/v1/public/organizations/{OLD}/jobs")

    assert old.status_code == 200, old.text
    assert old.json() == new.json()
    assert old.json()["name"] == "Renamed Co"
    assert jobs.status_code == 200 and jobs.json() == []


def test_default_alias_maps_the_former_careers_slug() -> None:
    assert get_settings().organization_slug_aliases == {"sigvitas": "zodiac-patent"}
