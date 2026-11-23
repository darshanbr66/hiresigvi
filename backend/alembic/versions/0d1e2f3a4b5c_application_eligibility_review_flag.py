"""Flag applications whose job's qualification requirements were ambiguous.

Additive: `applications.eligibility_review_required` BOOLEAN NOT NULL
DEFAULT false — every existing row reads false. Set by the progressive
apply flow when the JD's requirements couldn't be determined with
confidence (the general qualification list was offered instead).

Revision ID: 0d1e2f3a4b5c
Revises: 9c0d1e2f3a4b
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0d1e2f3a4b5c"
down_revision: str | None = "9c0d1e2f3a4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column(
            "eligibility_review_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("applications", "eligibility_review_required")
