"""Rename the careers organization from SIGVITAS to Zodiac Patent

Revision ID: 7a8b9c0d1e2f
Revises: 6f7081920314

Data only — no schema change. The public career site is branded for Zodiac
Patent, so the organization this deployment serves moves from the careers
slug `sigvitas` to `zodiac-patent`, and its display name (shown on the public
careers pages, candidate emails and the staff app) becomes "Zodiac Patent".

What is NOT touched: the organization's id (every user, role, job, candidate,
application and permission references the id, never the slug), so accounts,
logins, permissions and tenant isolation are unaffected.

Guarded so it is safe on any database:
* it only acts when an organization with slug `sigvitas` exists and none
  already has `zodiac-patent` (a fresh or already-migrated database is a
  no-op);
* the name changes only if it still reads as a SIGVITAS name
  (case-insensitive prefix), so a name someone already changed is kept.

Old links keep working without this row: public lookups by `sigvitas`
resolve through `settings.organization_slug_aliases`, and the frontend
redirects `/org/sigvitas/...` (features/public/careersOrg.ts).

Downgrade restores the slug, and the name to "Sigvitas Pvt. Ltd." when it is
still "Zodiac Patent" (the original name isn't stored anywhere; that is the
name the organization was created with).
"""

from collections.abc import Sequence

from alembic import op
from app.db.rls import BYPASS_SETTING

# revision identifiers, used by Alembic.
revision: str = "7a8b9c0d1e2f"
down_revision: str | None = "6f7081920314"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_SLUG = "sigvitas"
NEW_SLUG = "zodiac-patent"
NEW_NAME = "Zodiac Patent"
OLD_NAME = "Sigvitas Pvt. Ltd."


def _bypass_rls() -> None:
    # `organizations` has FORCE ROW LEVEL SECURITY keyed on its own id; a
    # migration acts on the tenant root row with no tenant context, so it
    # uses the documented bypass for this transaction only.
    op.execute(f"SELECT set_config('{BYPASS_SETTING}', 'on', true)")


def upgrade() -> None:
    _bypass_rls()
    op.execute(
        f"""
        UPDATE organizations
        SET slug = '{NEW_SLUG}',
            name = CASE WHEN lower(name) LIKE 'sigvitas%' THEN '{NEW_NAME}' ELSE name END
        WHERE slug = '{OLD_SLUG}'
          AND NOT EXISTS (SELECT 1 FROM organizations WHERE slug = '{NEW_SLUG}')
        """
    )


def downgrade() -> None:
    _bypass_rls()
    op.execute(
        f"""
        UPDATE organizations
        SET slug = '{OLD_SLUG}',
            name = CASE WHEN name = '{NEW_NAME}' THEN '{OLD_NAME}' ELSE name END
        WHERE slug = '{NEW_SLUG}'
          AND NOT EXISTS (SELECT 1 FROM organizations WHERE slug = '{OLD_SLUG}')
        """
    )
