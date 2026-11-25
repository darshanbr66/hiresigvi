"""Record what the Education step's eligibility check decided.

Additive only — one nullable JSONB column, no backfill, no change to
existing columns:

- `applications.education_eligibility`: the job's fields of study at
  submission, whether they were mandatory, the field the selected
  specialization matched, and the reasons the application was flagged for
  HR (`eligibility_review_required`). Stored rather than recomputed so HR
  sees what was decided at submission even after the JD changes.

Existing rows read NULL and keep loading (HR then sees the generic
eligibility notice for a flagged application).

Revision ID: 2f3a4b5c6d7e
Revises: 1e2f3a4b5c6d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "2f3a4b5c6d7e"
down_revision: str | None = "1e2f3a4b5c6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("education_eligibility", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("applications", "education_eligibility")
