"""job description show/hide flag

Revision ID: d2b3c4e5f6a7
Revises: c1a2f3b4d5e6
Create Date: 2026-09-18 12:30:00.000000

Adds `jobs.description_visible` (ZPHIREX platform overhaul § 2): hiding a
job description is a UI-visibility toggle only — the JD text itself is
never deleted or truncated, this column just gates whether the public
job-detail endpoint includes it (app/api/v1/public/jobs.py). Defaults to
true so every existing job keeps showing its JD exactly as before.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd2b3c4e5f6a7'
down_revision: Union[str, None] = 'c1a2f3b4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("description_visible", sa.Boolean(), server_default="true", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("jobs", "description_visible")
