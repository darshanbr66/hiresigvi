"""Configurable program policy: organization defaults, per-level eligibility,
academic thresholds, selection limits, optional bond/interview, internships
and program configuration versions

Revision ID: 6f7081920314
Revises: 5e6f70819203

Additive; existing rows keep their behaviour:

* recruitment_programs: the hardcoded 6/12-month training check becomes a
  1–60 month range (HR configures which durations a program offers); the
  standard company funding value may be 0 (zero funding). New columns:
  study_year_rules (per education level), minimum_cgpa,
  minimum_percentage, requires_interview (default on = unchanged),
  capacity, max_applications, bond_required (default on = unchanged),
  tracks_internship (default off = unchanged) and the internship terms
  (duration, stipend, terms text, whether completion is required for
  employment).
* program_enrollments: the 6/12 training check becomes 1–60; declared
  cgpa / percentage_score; program_version_number (NULL for existing
  applications).
* New tenant tables (RLS): organization_program_settings (one row per
  organization, created on first use), recruitment_program_versions,
  program_internships, program_internship_events.
* Permission program.settings.manage (ORG_ADMIN).

Downgrade drops everything added and restores the 6/12 checks. A program or
participant using another duration, or a 0 standard funding value, would
violate the restored checks, so downgrade refuses (raises) instead of
silently rewriting configured business values.
"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.rls import disable_tenant_rls, enable_tenant_rls

# revision identifiers, used by Alembic.
revision: str = "6f7081920314"
down_revision: str | None = "5e6f70819203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUMS: dict[str, tuple[str, ...]] = {
    "program_internship_status": ("PLANNED", "ACTIVE", "COMPLETED", "TERMINATED", "WITHDRAWN"),
    "program_internship_mode": ("ON_SITE", "REMOTE", "HYBRID"),
    "program_internship_outcome": ("QUALIFIED", "NOT_QUALIFIED"),
}

_PERMISSIONS = (
    (
        "program.settings.manage",
        "Edit the organization's default program settings (durations, fee, funding, internship, bond, eligibility).",
    ),
)
_ROLE_PERMISSIONS = {"ORG_ADMIN": ("program.settings.manage",)}

_PROGRAM_COLUMNS: tuple[tuple[str, sa.types.TypeEngine, str | None], ...] = (
    ("study_year_rules", postgresql.JSONB(), "{}"),
    ("minimum_cgpa", sa.Numeric(4, 2), None),
    ("minimum_percentage", sa.Numeric(5, 2), None),
    ("requires_interview", sa.Boolean(), "true"),
    ("capacity", sa.Integer(), None),
    ("max_applications", sa.Integer(), None),
    ("bond_required", sa.Boolean(), "true"),
    ("tracks_internship", sa.Boolean(), "false"),
    ("internship_duration_months", sa.Integer(), None),
    ("internship_stipend_amount", sa.Numeric(12, 2), None),
    ("internship_stipend_currency", sa.String(length=3), None),
    ("internship_terms", sa.Text(), None),
    ("internship_required_for_employment", sa.Boolean(), "false"),
)
_PROGRAM_CHECKS = (
    (
        "ck_recruitment_programs_training_durations_range",
        "cardinality(training_duration_options) = 0 OR "
        "(1 <= ALL(training_duration_options) AND 60 >= ALL(training_duration_options))",
    ),
    (
        "ck_recruitment_programs_internship_duration_range",
        "internship_duration_months IS NULL OR internship_duration_months BETWEEN 1 AND 60",
    ),
    (
        "ck_recruitment_programs_internship_stipend_valid",
        "internship_stipend_amount IS NULL OR "
        "(internship_stipend_amount >= 0 AND internship_stipend_currency IS NOT NULL)",
    ),
    ("ck_recruitment_programs_capacity_positive", "capacity IS NULL OR capacity > 0"),
    (
        "ck_recruitment_programs_max_applications_positive",
        "max_applications IS NULL OR max_applications > 0",
    ),
    (
        "ck_recruitment_programs_minimum_cgpa_range",
        "minimum_cgpa IS NULL OR (minimum_cgpa >= 0 AND minimum_cgpa <= 10)",
    ),
    (
        "ck_recruitment_programs_minimum_percentage_range",
        "minimum_percentage IS NULL OR (minimum_percentage >= 0 AND minimum_percentage <= 100)",
    ),
)
_OLD_FUNDING_CHECK = (
    "company_funding_value IS NULL OR (company_funding_value > 0 AND "
    "(company_funding_type <> 'PERCENTAGE' OR company_funding_value <= 100))"
)
_NEW_FUNDING_CHECK = (
    "company_funding_value IS NULL OR (company_funding_value >= 0 AND "
    "(company_funding_type <> 'PERCENTAGE' OR company_funding_value <= 100))"
)
_ENROLLMENT_CHECKS = (
    (
        "ck_program_enrollments_training_duration_range",
        "training_duration_months IS NULL OR training_duration_months BETWEEN 1 AND 60",
    ),
    ("ck_program_enrollments_cgpa_range", "cgpa IS NULL OR (cgpa >= 0 AND cgpa <= 10)"),
    (
        "ck_program_enrollments_percentage_range",
        "percentage_score IS NULL OR (percentage_score >= 0 AND percentage_score <= 100)",
    ),
)


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*_ENUMS[name], name=name, create_type=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete=ondelete
    )


def _seed_permissions() -> None:
    bind = op.get_bind()
    permission_ids = {code: str(uuid.uuid4()) for code, _ in _PERMISSIONS}
    op.bulk_insert(
        sa.table("permissions", sa.column("id", sa.UUID()), sa.column("code", sa.String()),
                 sa.column("description", sa.Text())),
        [{"id": permission_ids[c], "code": c, "description": d} for c, d in _PERMISSIONS],
    )
    rows = []
    for role_name, codes in _ROLE_PERMISSIONS.items():
        for (role_id,) in bind.execute(
            sa.text("SELECT id FROM roles WHERE organization_id IS NULL AND name = :name"),
            {"name": role_name},
        ):
            rows.extend({"role_id": role_id, "permission_id": permission_ids[c]} for c in codes)
    if rows:
        op.bulk_insert(
            sa.table("role_permissions", sa.column("role_id", sa.UUID()), sa.column("permission_id", sa.UUID())),
            rows,
        )


def upgrade() -> None:
    bind = op.get_bind()
    for name in _ENUMS:
        _enum(name).create(bind, checkfirst=True)

    # --- recruitment_programs ---
    t = "recruitment_programs"
    # A column with a server default is NOT NULL (switches, JSON); the rest
    # are optional values.
    for column, type_, default in _PROGRAM_COLUMNS:
        op.add_column(
            t, sa.Column(column, type_, server_default=default, nullable=default is None)
        )
    # Names: constraints created with op.f() keep their exact name; the
    # funding check was created (5e6f70819203) with a plain name, which the
    # naming convention rewrites the same way on drop/create.
    op.drop_constraint(op.f("ck_recruitment_programs_training_durations_approved"), t, type_="check")
    op.drop_constraint("ck_recruitment_programs_funding_value_valid", t, type_="check")
    op.create_check_constraint("ck_recruitment_programs_funding_value_valid", t, _NEW_FUNDING_CHECK)
    for name, condition in _PROGRAM_CHECKS:
        op.create_check_constraint(op.f(name), t, condition)

    # --- program_enrollments ---
    t = "program_enrollments"
    op.add_column(t, sa.Column("cgpa", sa.Numeric(4, 2), nullable=True))
    op.add_column(t, sa.Column("percentage_score", sa.Numeric(5, 2), nullable=True))
    op.add_column(t, sa.Column("program_version_number", sa.Integer(), nullable=True))
    op.drop_constraint(op.f("ck_program_enrollments_training_duration_approved"), t, type_="check")
    for name, condition in _ENROLLMENT_CHECKS:
        op.create_check_constraint(op.f(name), t, condition)

    # --- organization_program_settings ---
    t = "organization_program_settings"
    op.create_table(
        t,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("training_duration_options", postgresql.ARRAY(sa.Integer()), server_default="{}", nullable=False),
        sa.Column("training_fee_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column(
            "company_funding_type",
            postgresql.ENUM(name="program_funding_type", create_type=False),
            nullable=True,
        ),
        sa.Column("company_funding_value", sa.Numeric(12, 2), nullable=True),
        sa.Column("funding_requires_independent_approval", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("internship_duration_months", sa.Integer(), nullable=True),
        sa.Column("bond_required", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("bond_duration_months", sa.Integer(), nullable=True),
        sa.Column(
            "eligible_education_levels", postgresql.ARRAY(sa.String(length=30)), server_default="{}", nullable=False
        ),
        sa.Column("study_year_rules", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
        *_timestamps(),
        _fk(t, "organization_id", "organizations", "CASCADE"),
        _fk(t, "updated_by_user_id", "users", "SET NULL"),
        sa.CheckConstraint(
            "cardinality(training_duration_options) = 0 OR "
            "(1 <= ALL(training_duration_options) AND 60 >= ALL(training_duration_options))",
            name=op.f(f"ck_{t}_training_durations_range"),
        ),
        sa.CheckConstraint(
            "training_fee_amount IS NULL OR (training_fee_amount >= 0 AND currency IS NOT NULL)",
            name=op.f(f"ck_{t}_fee_valid"),
        ),
        sa.CheckConstraint(
            "(company_funding_type IS NULL) = (company_funding_value IS NULL)",
            name=op.f(f"ck_{t}_funding_complete"),
        ),
        sa.CheckConstraint(_NEW_FUNDING_CHECK, name=op.f(f"ck_{t}_funding_value_valid")),
        sa.CheckConstraint(
            "internship_duration_months IS NULL OR internship_duration_months BETWEEN 1 AND 60",
            name=op.f(f"ck_{t}_internship_duration_range"),
        ),
        sa.CheckConstraint(
            "bond_duration_months IS NULL OR bond_duration_months BETWEEN 1 AND 120",
            name=op.f(f"ck_{t}_bond_duration_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.UniqueConstraint("organization_id", name=op.f(f"uq_{t}_organization_id")),
    )
    op.create_index(op.f(f"ix_{t}_organization_id"), t, ["organization_id"])
    enable_tenant_rls(op, t)

    # --- recruitment_program_versions ---
    t = "recruitment_program_versions"
    op.create_table(
        t,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("program_id", sa.UUID(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=30), nullable=False),
        sa.Column("changed_fields", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        _fk(t, "organization_id", "organizations", "RESTRICT"),
        _fk(t, "program_id", "recruitment_programs", "CASCADE"),
        _fk(t, "created_by_user_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
    )
    op.create_index(op.f(f"ix_{t}_organization_id"), t, ["organization_id"])
    op.create_index(op.f(f"ix_{t}_program_id"), t, ["program_id"])
    op.create_index(
        "uq_recruitment_program_versions_program_number", t, ["program_id", "version_number"], unique=True
    )
    enable_tenant_rls(op, t)

    # --- program_internships ---
    t = "program_internships"
    op.create_table(
        t,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("enrollment_id", sa.UUID(), nullable=False),
        sa.Column("status", _enum("program_internship_status"), server_default="PLANNED", nullable=False),
        sa.Column("mode", _enum("program_internship_mode"), nullable=True),
        sa.Column("role_title", sa.String(length=255), nullable=True),
        sa.Column("duration_months", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("original_end_date", sa.Date(), nullable=True),
        sa.Column("extension_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stipend_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("stipend_currency", sa.String(length=3), nullable=True),
        sa.Column("supervisor_employee_id", sa.UUID(), nullable=True),
        sa.Column("completed_on", sa.Date(), nullable=True),
        sa.Column("ended_on", sa.Date(), nullable=True),
        sa.Column("outcome", _enum("program_internship_outcome"), nullable=True),
        sa.Column("evaluation_rating", sa.Integer(), nullable=True),
        sa.Column("evaluation_note", sa.Text(), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        *_timestamps(),
        _fk(t, "organization_id", "organizations", "RESTRICT"),
        _fk(t, "enrollment_id", "program_enrollments", "RESTRICT"),
        _fk(t, "supervisor_employee_id", "employees", "SET NULL"),
        _fk(t, "created_by_user_id", "users", "SET NULL"),
        sa.CheckConstraint("duration_months BETWEEN 1 AND 60", name=op.f(f"ck_{t}_duration_range")),
        sa.CheckConstraint(
            "stipend_amount IS NULL OR (stipend_amount >= 0 AND stipend_currency IS NOT NULL)",
            name=op.f(f"ck_{t}_stipend_valid"),
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date > start_date",
            name=op.f(f"ck_{t}_dates_ordered"),
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETED') = (outcome IS NOT NULL)", name=op.f(f"ck_{t}_outcome_on_completion")
        ),
        sa.CheckConstraint(
            "evaluation_rating IS NULL OR evaluation_rating BETWEEN 1 AND 5", name=op.f(f"ck_{t}_rating_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.UniqueConstraint("enrollment_id", name=op.f(f"uq_{t}_enrollment_id")),
    )
    op.create_index(op.f(f"ix_{t}_organization_id"), t, ["organization_id"])
    enable_tenant_rls(op, t)

    # --- program_internship_events ---
    t = "program_internship_events"
    op.create_table(
        t,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("internship_id", sa.UUID(), nullable=False),
        sa.Column("event", sa.String(length=30), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        _fk(t, "organization_id", "organizations", "RESTRICT"),
        _fk(t, "internship_id", "program_internships", "CASCADE"),
        _fk(t, "actor_user_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
    )
    op.create_index(op.f(f"ix_{t}_organization_id"), t, ["organization_id"])
    op.create_index(op.f(f"ix_{t}_internship_id"), t, ["internship_id"])
    enable_tenant_rls(op, t)

    _seed_permissions()


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    conflicts = bind.scalar(
        sa.text(
            "SELECT (SELECT count(*) FROM recruitment_programs WHERE NOT "
            "(training_duration_options <@ ARRAY[6, 12]) OR company_funding_value = 0) + "
            "(SELECT count(*) FROM program_enrollments WHERE training_duration_months IS NOT NULL "
            "AND training_duration_months NOT IN (6, 12))"
        )
    )
    op.execute("SET LOCAL app.bypass_rls = 'off'")
    if conflicts:
        raise RuntimeError(
            "Programs or participants use training durations other than 6/12 months or a 0 "
            "standard funding value; the previous schema can't hold them. Change them first."
        )

    codes = [code for code, _ in _PERMISSIONS]
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE code = ANY(:codes))"
        ),
        {"codes": codes},
    )
    bind.execute(sa.text("DELETE FROM permissions WHERE code = ANY(:codes)"), {"codes": codes})

    for table in (
        "program_internship_events",
        "program_internships",
        "recruitment_program_versions",
        "organization_program_settings",
    ):
        disable_tenant_rls(op, table)
        op.drop_table(table)

    t = "program_enrollments"
    for name, _ in _ENROLLMENT_CHECKS:
        op.drop_constraint(op.f(name), t, type_="check")
    op.create_check_constraint(
        op.f("ck_program_enrollments_training_duration_approved"),
        t,
        "training_duration_months IS NULL OR training_duration_months IN (6, 12)",
    )
    for column in ("program_version_number", "percentage_score", "cgpa"):
        op.drop_column(t, column)

    t = "recruitment_programs"
    for name, _ in _PROGRAM_CHECKS:
        op.drop_constraint(op.f(name), t, type_="check")
    op.drop_constraint("ck_recruitment_programs_funding_value_valid", t, type_="check")
    op.create_check_constraint("ck_recruitment_programs_funding_value_valid", t, _OLD_FUNDING_CHECK)
    op.create_check_constraint(
        op.f("ck_recruitment_programs_training_durations_approved"), t, "training_duration_options <@ ARRAY[6, 12]"
    )
    for column, _, _ in reversed(_PROGRAM_COLUMNS):
        op.drop_column(t, column)

    for name in reversed(list(_ENUMS)):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
