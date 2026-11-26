"""Widen the Education step's specialization columns for free-text values.

The Education step's specialization category and specialization are now
typed by the candidate instead of picked from the catalogue, so the
columns that held catalogue keys (VARCHAR(50)) must fit what candidates
actually type ("Electronics and Communication Engineering (VLSI Design)"
is already 55 characters):

- `applications.education_specialization_category`        50 -> 100
- `applications.education_specialization`                 50 -> 150
- `education_verification_runs.specialization_category`   50 -> 100
- `education_verification_runs.specialization`            50 -> 150

150 matches `candidates.specialization` and the program application's
specialization field. Widening a VARCHAR is a metadata-only change in
PostgreSQL (no table rewrite, no data change); existing rows — catalogue
keys such as `CS_IT` / `AI_ML` — are untouched and keep resolving to
their labels in the application layer. Drafts need no change: their
education details are JSONB.

Downgrade narrows the columns back to 50. Any free-text value longer than
50 characters stored after the upgrade is truncated to 50 by the downgrade
(`USING left(col, 50)`), so a downgrade is lossy for those rows — take a
backup first if it must preserve them.

Revision ID: 3a4b5c6d7e8f
Revises: 2f3a4b5c6d7e
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3a4b5c6d7e8f"
down_revision: str | None = "2f3a4b5c6d7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (table, column, new length)
_COLUMNS = (
    ("applications", "education_specialization_category", 100),
    ("applications", "education_specialization", 150),
    ("education_verification_runs", "specialization_category", 100),
    ("education_verification_runs", "specialization", 150),
)


def upgrade() -> None:
    for table, column, length in _COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.String(length=50),
            type_=sa.String(length=length),
            existing_nullable=True,
        )


def downgrade() -> None:
    for table, column, length in reversed(_COLUMNS):
        op.alter_column(
            table,
            column,
            existing_type=sa.String(length=length),
            type_=sa.String(length=50),
            existing_nullable=True,
            postgresql_using=f"left({column}, 50)",
        )
