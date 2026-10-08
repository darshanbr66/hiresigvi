"""Candidate registration: unbounded full name, optional 10th marks + card.

* `candidates.full_name` VARCHAR(255) -> TEXT: the self-service form no
  longer limits the length of a name.
* `candidates.tenth_marks_percentage` NUMERIC(5,2), 0–100, optional.
* `candidates.tenth_marks_card_*`: a reference to the optional uploaded
  10th marks card. The file bytes live in the storage abstraction (MongoDB
  GridFS in production), never in Postgres; these columns are metadata
  only, all set or all NULL.

LinkedIn/GitHub becoming optional on the public form needs no schema
change (both columns were always nullable). Candidates are already covered
by the table's RLS policy; new columns need nothing extra.

Downgrade drops the marks columns (the stored files are not touched) and
restores VARCHAR(255); it fails if a longer name has since been stored,
which is the correct outcome rather than silently truncating it.

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9a0b1c2d3e4"
down_revision: str | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "candidates",
        "full_name",
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=False,
    )
    op.add_column(
        "candidates", sa.Column("tenth_marks_percentage", sa.Numeric(5, 2), nullable=True)
    )
    op.add_column(
        "candidates",
        sa.Column("tenth_marks_card_storage_provider", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "candidates",
        sa.Column("tenth_marks_card_storage_path", sa.String(length=1000), nullable=True),
    )
    op.add_column(
        "candidates", sa.Column("tenth_marks_card_filename", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "candidates",
        sa.Column("tenth_marks_card_content_type", sa.String(length=150), nullable=True),
    )
    op.add_column(
        "candidates", sa.Column("tenth_marks_card_size_bytes", sa.Integer(), nullable=True)
    )
    op.add_column(
        "candidates",
        sa.Column("tenth_marks_card_uploaded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_candidates_tenth_marks_percentage_range",
        "candidates",
        "tenth_marks_percentage IS NULL OR "
        "(tenth_marks_percentage >= 0 AND tenth_marks_percentage <= 100)",
    )
    op.create_check_constraint(
        "ck_candidates_tenth_marks_card_complete",
        "candidates",
        "(tenth_marks_card_storage_path IS NULL) = "
        "(tenth_marks_card_storage_provider IS NULL) AND "
        "(tenth_marks_card_storage_path IS NULL) = (tenth_marks_card_filename IS NULL) AND "
        "(tenth_marks_card_storage_path IS NULL) = "
        "(tenth_marks_card_content_type IS NULL) AND "
        "(tenth_marks_card_storage_path IS NULL) = (tenth_marks_card_size_bytes IS NULL) AND "
        "(tenth_marks_card_storage_path IS NULL) = (tenth_marks_card_uploaded_at IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_candidates_tenth_marks_card_complete", "candidates", type_="check")
    op.drop_constraint("ck_candidates_tenth_marks_percentage_range", "candidates", type_="check")
    for column in (
        "tenth_marks_card_uploaded_at",
        "tenth_marks_card_size_bytes",
        "tenth_marks_card_content_type",
        "tenth_marks_card_filename",
        "tenth_marks_card_storage_path",
        "tenth_marks_card_storage_provider",
        "tenth_marks_percentage",
    ):
        op.drop_column("candidates", column)
    op.alter_column(
        "candidates",
        "full_name",
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
