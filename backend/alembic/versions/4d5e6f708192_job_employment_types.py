"""Job employment types (internship / part-time / full-time) and type details

Revision ID: 4d5e6f708192
Revises: 3c4d5e6f7081

* `jobs.employment_type` was free text (String(100), nullable). It becomes
  the native enum `employment_type` (INTERNSHIP, PART_TIME, FULL_TIME) —
  the same column, not a second one. Existing rows are converted in place:
  - a recruitment program's carrier job -> NULL (the program is its own
    workflow, never an ordinary job type; its old "Training program" text
    is derivable from the program row);
  - text that reads as full-time / part-time / internship (case, spaces and
    hyphens ignored) -> that type;
  - anything else, including NULL -> FULL_TIME, the platform's existing
    default (the job form pre-filled "Full-time"). Unrecognized text such
    as "Contract" is preserved verbatim in the new `employment_terms`
    column so nothing a recruiter typed is lost and HR can re-classify.
* New nullable, type-specific detail columns (app/models/job.py::
  EMPLOYMENT_TYPE_FIELDS): work mode, duration, paid/unpaid, a single
  compensation range (stipend / pay / salary) with currency and period,
  hours per week, schedule, experience range, eligibility, full-time
  conversion, expected joining date, employment terms. All NULL for
  existing jobs, so every existing job and application keeps working.
* `recruitment_programs.employment_job_id`: optional reference to the
  FULL_TIME job a program's graduates are intended to join.

Downgrade restores the free-text column (labels "Internship", "Part-time",
"Full-time", carrier jobs "Training program") and drops the new columns.
Legacy text that was moved to `employment_terms` is restored to
`employment_type` for FULL_TIME jobs when it still fits.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d5e6f708192"
down_revision: str | None = "3c4d5e6f7081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUMS: dict[str, tuple[str, ...]] = {
    "employment_type": ("INTERNSHIP", "PART_TIME", "FULL_TIME"),
    "work_mode": ("ON_SITE", "HYBRID", "REMOTE"),
    "compensation_period": ("HOURLY", "MONTHLY", "ANNUAL"),
}

_NORMALIZED = "regexp_replace(lower(employment_type), '[^a-z]', '', 'g')"


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*_ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in _ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.add_column("jobs", sa.Column("employment_type_new", _enum("employment_type"), nullable=True))
    op.add_column("jobs", sa.Column("work_mode", _enum("work_mode"), nullable=True))
    op.add_column("jobs", sa.Column("duration_months", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("is_paid", sa.Boolean(), nullable=True))
    op.add_column("jobs", sa.Column("compensation_min", sa.Numeric(12, 2), nullable=True))
    op.add_column("jobs", sa.Column("compensation_max", sa.Numeric(12, 2), nullable=True))
    op.add_column("jobs", sa.Column("compensation_currency", sa.String(length=3), nullable=True))
    op.add_column(
        "jobs", sa.Column("compensation_period", _enum("compensation_period"), nullable=True)
    )
    op.add_column("jobs", sa.Column("hours_per_week", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("work_schedule", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("min_experience_years", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("max_experience_years", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("eligibility_criteria", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("conversion_to_full_time", sa.Boolean(), nullable=True))
    op.add_column("jobs", sa.Column("expected_joining_date", sa.Date(), nullable=True))
    op.add_column("jobs", sa.Column("employment_terms", sa.Text(), nullable=True))

    # `jobs` has FORCE ROW LEVEL SECURITY, so the data conversion needs an
    # explicit, transaction-scoped bypass or it matches zero rows.
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute(
        f"""
        UPDATE jobs SET employment_type_new = CASE
            WHEN id IN (SELECT job_id FROM recruitment_programs) THEN NULL
            WHEN {_NORMALIZED} IN ('parttime') THEN 'PART_TIME'::employment_type
            WHEN {_NORMALIZED} IN ('intern', 'internship') THEN 'INTERNSHIP'::employment_type
            ELSE 'FULL_TIME'::employment_type
        END
        """
    )
    op.execute(
        f"""
        UPDATE jobs SET employment_terms = btrim(employment_type)
        WHERE employment_type IS NOT NULL
          AND btrim(employment_type) <> ''
          AND id NOT IN (SELECT job_id FROM recruitment_programs)
          AND {_NORMALIZED} NOT IN ('fulltime', 'parttime', 'intern', 'internship')
        """
    )
    op.execute("SET LOCAL app.bypass_rls = 'off'")

    op.drop_column("jobs", "employment_type")
    op.alter_column("jobs", "employment_type_new", new_column_name="employment_type")

    op.create_check_constraint(
        op.f("ck_jobs_compensation_range"),
        "jobs",
        "compensation_min IS NULL OR compensation_max IS NULL OR compensation_min <= compensation_max",
    )
    op.create_check_constraint(
        op.f("ck_jobs_experience_range"),
        "jobs",
        "min_experience_years IS NULL OR max_experience_years IS NULL "
        "OR min_experience_years <= max_experience_years",
    )
    op.create_check_constraint(
        op.f("ck_jobs_duration_months_range"), "jobs", "duration_months IS NULL OR duration_months BETWEEN 1 AND 60"
    )
    op.create_check_constraint(
        op.f("ck_jobs_hours_per_week_range"), "jobs", "hours_per_week IS NULL OR hours_per_week BETWEEN 1 AND 168"
    )

    op.add_column(
        "recruitment_programs",
        sa.Column("employment_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_recruitment_programs_employment_job_id_jobs"),
        "recruitment_programs",
        "jobs",
        ["employment_job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_recruitment_programs_employment_job_id_jobs"), "recruitment_programs", type_="foreignkey"
    )
    op.drop_column("recruitment_programs", "employment_job_id")

    for name in (
        op.f("ck_jobs_hours_per_week_range"),
        op.f("ck_jobs_duration_months_range"),
        op.f("ck_jobs_experience_range"),
        op.f("ck_jobs_compensation_range"),
    ):
        op.drop_constraint(name, "jobs", type_="check")

    op.add_column("jobs", sa.Column("employment_type_text", sa.String(length=100), nullable=True))
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute(
        """
        UPDATE jobs SET employment_type_text = CASE
            WHEN id IN (SELECT job_id FROM recruitment_programs) THEN 'Training program'
            WHEN employment_type = 'INTERNSHIP' THEN 'Internship'
            WHEN employment_type = 'PART_TIME' THEN 'Part-time'
            WHEN employment_type = 'FULL_TIME' AND employment_terms IS NOT NULL
                 AND length(employment_terms) <= 100 THEN employment_terms
            WHEN employment_type = 'FULL_TIME' THEN 'Full-time'
            ELSE NULL
        END
        """
    )
    op.execute("SET LOCAL app.bypass_rls = 'off'")

    for column in (
        "employment_terms",
        "expected_joining_date",
        "conversion_to_full_time",
        "eligibility_criteria",
        "max_experience_years",
        "min_experience_years",
        "work_schedule",
        "hours_per_week",
        "compensation_period",
        "compensation_currency",
        "compensation_max",
        "compensation_min",
        "is_paid",
        "duration_months",
        "work_mode",
        "employment_type",
    ):
        op.drop_column("jobs", column)
    op.alter_column("jobs", "employment_type_text", new_column_name="employment_type")

    bind = op.get_bind()
    for name in reversed(list(_ENUMS)):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
