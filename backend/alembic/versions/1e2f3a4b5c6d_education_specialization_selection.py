"""Record the Education step's specialization category and specialization.

Additive only — five nullable VARCHAR(50) columns, no backfill, no change to
existing columns:

- `applications.education_qualification_key`,
  `applications.education_specialization_category`,
  `applications.education_specialization`: the three Education-step
  selections as submitted (keys into app/services/education/catalog.py and
  specializations.py). Per application, because the Candidate profile is
  shared across applications.
- `education_verification_runs.specialization_category`,
  `education_verification_runs.specialization`: what each academic-document
  check was run against, alongside the existing `qualification_key`.

Existing rows read NULL and keep loading (HR sees the legacy qualification
text for them). Drafts need no change: their education details are JSONB.

Revision ID: 1e2f3a4b5c6d
Revises: 0d1e2f3a4b5c
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1e2f3a4b5c6d"
down_revision: str | None = "0d1e2f3a4b5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APPLICATION_COLUMNS = (
    "education_qualification_key",
    "education_specialization_category",
    "education_specialization",
)
_RUN_COLUMNS = ("specialization_category", "specialization")


def upgrade() -> None:
    for name in _APPLICATION_COLUMNS:
        op.add_column("applications", sa.Column(name, sa.String(length=50), nullable=True))
    for name in _RUN_COLUMNS:
        op.add_column(
            "education_verification_runs", sa.Column(name, sa.String(length=50), nullable=True)
        )


def downgrade() -> None:
    for name in reversed(_RUN_COLUMNS):
        op.drop_column("education_verification_runs", name)
    for name in reversed(_APPLICATION_COLUMNS):
        op.drop_column("applications", name)
