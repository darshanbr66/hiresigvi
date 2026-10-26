"""Institution registry, campus-drive institution link, affiliation verification

Revision ID: 1a2b3c4d5e6f
Revises: f9a0b1c2d3e4

* `institutions`: the organization's registry of colleges/universities
  (docs/campus-hiring.md § 1a), unique per organization by normalized name.
* `campus_drives.institution_id` (NOT NULL after backfill) and
  `campus_drives.affiliation_verification_method` (default NONE).
  Backfill is additive: one institution is registered per distinct
  (organization, normalized `college_name`) of the existing drives, and each
  drive is linked to it. `college_name` itself is kept, unchanged, as the
  display snapshot.
* `campus_affiliation_verifications`: one verification record per
  campus-drive application that requires one (pending/verified/rejected,
  method, reviewer, timestamp).
* `campus_drive_roster_entries`: a college-provided student list per drive
  (email, optional roll number/name) for STUDENT_LIST verification.

All new tenant-owned tables get the standard RLS policy.

Downgrade drops the new tables/columns; the backfilled institutions go with
the table. `campus_drives.college_name` was never touched, so drives keep
their original college text.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.rls import disable_tenant_rls, enable_tenant_rls

# revision identifiers, used by Alembic.
revision: str = "1a2b3c4d5e6f"
down_revision: str | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NORMALIZE_SQL = "lower(regexp_replace(btrim({col}), '\\s+', ' ', 'g'))"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    institution_type = postgresql.ENUM(
        "UNIVERSITY", "COLLEGE", "INSTITUTE", "OTHER", name="institution_type", create_type=False
    )
    verification_method = postgresql.ENUM(
        "NONE", "EMAIL_DOMAIN", "STUDENT_LIST", "MANUAL",
        name="affiliation_verification_method", create_type=False,
    )
    affiliation_status = postgresql.ENUM(
        "PENDING", "VERIFIED", "REJECTED", name="affiliation_status", create_type=False
    )
    institution_type.create(bind, checkfirst=True)
    verification_method.create(bind, checkfirst=True)
    affiliation_status.create(bind, checkfirst=True)

    # --- institutions ---
    op.create_table(
        "institutions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("institution_type", institution_type, nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column(
            "email_domains", postgresql.ARRAY(sa.String(length=255)), server_default="{}", nullable=False
        ),
        sa.Column("placement_contact_name", sa.String(length=255), nullable=True),
        sa.Column("placement_contact_email", sa.String(length=320), nullable=True),
        sa.Column("placement_contact_phone", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name=op.f("fk_institutions_organization_id_organizations"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_institutions_created_by_users"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_institutions")),
    )
    op.create_index(op.f("ix_institutions_organization_id"), "institutions", ["organization_id"])
    op.create_index(
        "uq_institutions_org_normalized_name", "institutions",
        ["organization_id", "normalized_name"], unique=True,
    )
    enable_tenant_rls(op, "institutions")

    # --- campus_drives: institution link + verification method ---
    op.add_column("campus_drives", sa.Column("institution_id", sa.UUID(), nullable=True))
    op.add_column(
        "campus_drives",
        sa.Column(
            "affiliation_verification_method", verification_method,
            server_default="NONE", nullable=False,
        ),
    )

    # Both tables FORCE RLS even for the owner (app/db/rls.py): the backfill
    # needs an explicit, transaction-scoped bypass or it matches zero rows.
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    normalized = _NORMALIZE_SQL.format(col="cd.college_name")
    op.execute(
        "INSERT INTO institutions (organization_id, name, normalized_name, created_by) "
        f"SELECT DISTINCT ON (cd.organization_id, {normalized}) "
        f"cd.organization_id, btrim(cd.college_name), {normalized}, cd.created_by "
        "FROM campus_drives cd "
        f"ORDER BY cd.organization_id, {normalized}, cd.created_at"
    )
    op.execute(
        "UPDATE campus_drives cd SET institution_id = i.id FROM institutions i "
        "WHERE i.organization_id = cd.organization_id "
        f"AND i.normalized_name = {normalized}"
    )
    op.execute("SET LOCAL app.bypass_rls = 'off'")

    op.alter_column("campus_drives", "institution_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_campus_drives_institution_id_institutions"),
        "campus_drives", "institutions", ["institution_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index(op.f("ix_campus_drives_institution_id"), "campus_drives", ["institution_id"])

    # --- campus_affiliation_verifications ---
    op.create_table(
        "campus_affiliation_verifications",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("campus_drive_id", sa.UUID(), nullable=False),
        sa.Column("institution_id", sa.UUID(), nullable=False),
        sa.Column("status", affiliation_status, server_default="PENDING", nullable=False),
        sa.Column("method", verification_method, nullable=True),
        sa.Column("reviewed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name=op.f("fk_campus_affiliation_verifications_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["applications.id"],
            name=op.f("fk_campus_affiliation_verifications_application_id_applications"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["campus_drive_id"], ["campus_drives.id"],
            name=op.f("fk_campus_affiliation_verifications_campus_drive_id_campus_drives"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["institution_id"], ["institutions.id"],
            name=op.f("fk_campus_affiliation_verifications_institution_id_institutions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"], ["users.id"],
            name=op.f("fk_campus_affiliation_verifications_reviewed_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campus_affiliation_verifications")),
        sa.UniqueConstraint(
            "application_id", name=op.f("uq_campus_affiliation_verifications_application_id")
        ),
    )
    op.create_index(
        op.f("ix_campus_affiliation_verifications_organization_id"),
        "campus_affiliation_verifications", ["organization_id"],
    )
    op.create_index(
        op.f("ix_campus_affiliation_verifications_campus_drive_id"),
        "campus_affiliation_verifications", ["campus_drive_id"],
    )
    enable_tenant_rls(op, "campus_affiliation_verifications")

    # --- campus_drive_roster_entries ---
    op.create_table(
        "campus_drive_roster_entries",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("campus_drive_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("student_identifier", sa.String(length=100), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("added_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name=op.f("fk_campus_drive_roster_entries_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["campus_drive_id"], ["campus_drives.id"],
            name=op.f("fk_campus_drive_roster_entries_campus_drive_id_campus_drives"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["added_by_user_id"], ["users.id"],
            name=op.f("fk_campus_drive_roster_entries_added_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campus_drive_roster_entries")),
    )
    op.create_index(
        op.f("ix_campus_drive_roster_entries_organization_id"),
        "campus_drive_roster_entries", ["organization_id"],
    )
    op.create_index(
        "uq_campus_drive_roster_drive_email", "campus_drive_roster_entries",
        ["campus_drive_id", "email"], unique=True,
    )
    enable_tenant_rls(op, "campus_drive_roster_entries")


def downgrade() -> None:
    bind = op.get_bind()
    disable_tenant_rls(op, "campus_drive_roster_entries")
    op.drop_table("campus_drive_roster_entries")
    disable_tenant_rls(op, "campus_affiliation_verifications")
    op.drop_table("campus_affiliation_verifications")

    op.drop_index(op.f("ix_campus_drives_institution_id"), table_name="campus_drives")
    op.drop_constraint(
        op.f("fk_campus_drives_institution_id_institutions"), "campus_drives", type_="foreignkey"
    )
    op.drop_column("campus_drives", "affiliation_verification_method")
    op.drop_column("campus_drives", "institution_id")

    disable_tenant_rls(op, "institutions")
    op.drop_table("institutions")

    sa.Enum(name="affiliation_status").drop(bind, checkfirst=True)
    sa.Enum(name="affiliation_verification_method").drop(bind, checkfirst=True)
    sa.Enum(name="institution_type").drop(bind, checkfirst=True)
