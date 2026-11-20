"""Set Zodiac Patent's public recruitment contact to hr@zodiacpatent.com

Revision ID: 8b9c0d1e2f3a
Revises: 7a8b9c0d1e2f

Data only — no schema change. `organizations.careers_contact_email` is the
one candidate-facing recruitment contact: the careers page shows it, the
public "already applied / already registered" messages and the candidate
emails (contact line and Reply-To) use it, and the Zodi assistant may share
only it. The careers organization still carried the former SIGVITAS address,
so candidates were told to write to a Sigvitas mailbox. Zodiac Patent's
recruitment contact is hr@zodiacpatent.com.

Guarded so it is safe on any database:
* only the careers organization (slug `zodiac-patent`, after 7a8b9c0d1e2f)
  is touched — no other tenant;
* the address changes only when it is unset or still a `@sigvitas.com`
  address, so a contact someone has since set deliberately (through the
  admin Organizations page) is kept.

Downgrade is a no-op: it would re-publish a Sigvitas address as Zodiac
Patent's recruitment contact, which must not happen. Change the contact
through the admin Organizations page if needed.
"""

from collections.abc import Sequence

from alembic import op
from app.db.rls import BYPASS_SETTING

# revision identifiers, used by Alembic.
revision: str = "8b9c0d1e2f3a"
down_revision: str | None = "7a8b9c0d1e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_SLUG = "zodiac-patent"
CONTACT_EMAIL = "hr@zodiacpatent.com"


def upgrade() -> None:
    # `organizations` has FORCE ROW LEVEL SECURITY keyed on its own id; a
    # migration acts on the tenant root row with no tenant context, so it
    # uses the documented bypass for this transaction only.
    op.execute(f"SELECT set_config('{BYPASS_SETTING}', 'on', true)")
    op.execute(
        f"""
        UPDATE organizations
        SET careers_contact_email = '{CONTACT_EMAIL}'
        WHERE slug = '{ORG_SLUG}'
          AND (
            careers_contact_email IS NULL
            OR btrim(careers_contact_email) = ''
            OR lower(careers_contact_email) LIKE '%@sigvitas.com'
          )
        """
    )


def downgrade() -> None:
    # Deliberately nothing — see the module docstring.
    pass
