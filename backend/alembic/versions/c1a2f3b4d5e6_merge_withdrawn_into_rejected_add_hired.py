"""merge WITHDRAWN into REJECTED, add HIRED application status

Revision ID: c1a2f3b4d5e6
Revises: 7b8857298c92
Create Date: 2026-09-18 12:00:00.000000

Product decision (see CLAUDE.md / ZPHIREX platform overhaul): the
recruiter-facing workflow no longer distinguishes "Rejected" from
"Withdrawn" — every application previously in WITHDRAWN is data-migrated to
REJECTED, and the value is removed from the native Postgres
`application_status` enum entirely (not just hidden in application code),
so no trace of it remains in the DB schema either. `HIRED` is added as the
new terminal state reachable only from SELECTED (docs/recruitment-workflow.md
§ 2), matching the platform's "Selected -> Hired" hiring-outcome flow.

Postgres has no `ALTER TYPE ... DROP VALUE`, so this uses the standard
rename-old-type / create-new-type / cast-columns / drop-old-type pattern,
applied to both `applications.status` and
`application_status_history.from_status`/`to_status` (all three columns
share the same enum type).
"""
from typing import Sequence, Union

from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1a2f3b4d5e6'
down_revision: Union[str, None] = '7b8857298c92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_VALUES = (
    "APPLIED", "UNDER_REVIEW", "SCREENING", "ASSESSMENT_INVITED",
    "ASSESSMENT_STARTED", "ASSESSMENT_COMPLETED", "SHORTLISTED", "INTERVIEW",
    "SELECTED", "REJECTED", "WITHDRAWN",
)
_NEW_VALUES = (
    "APPLIED", "UNDER_REVIEW", "SCREENING", "ASSESSMENT_INVITED",
    "ASSESSMENT_STARTED", "ASSESSMENT_COMPLETED", "SHORTLISTED", "INTERVIEW",
    "SELECTED", "REJECTED", "HIRED",
)

_ENUM_COLUMNS = (
    ("applications", "status"),
    ("application_status_history", "from_status"),
    ("application_status_history", "to_status"),
)


def upgrade() -> None:
    bind = op.get_bind()

    # Every tenant-owned table FORCEs RLS (app/db/rls.py), which applies to
    # DML from the migration role too, not just application queries — bypass
    # it for this migration's data updates, exactly like the narrow,
    # audited cases in app/db/rls.py::rls_bypass, so the UPDATE below isn't
    # silently scoped to zero rows by a missing tenant context.
    op.execute("SET app.bypass_rls = 'on'")

    # 1. Data migration — existing WITHDRAWN rows become REJECTED, on the
    # *old* enum type (REJECTED already a valid value there).
    op.execute("UPDATE applications SET status = 'REJECTED' WHERE status = 'WITHDRAWN'")
    op.execute(
        "UPDATE application_status_history SET from_status = 'REJECTED' "
        "WHERE from_status = 'WITHDRAWN'"
    )
    op.execute(
        "UPDATE application_status_history SET to_status = 'REJECTED' "
        "WHERE to_status = 'WITHDRAWN'"
    )

    # 2. Drop the default that references the old enum type, so the type
    # swap below isn't blocked by it.
    op.alter_column("applications", "status", server_default=None)

    # 3. Rename old type out of the way, create the new one.
    op.execute("ALTER TYPE application_status RENAME TO application_status_old")
    new_enum = postgresql.ENUM(*_NEW_VALUES, name="application_status")
    new_enum.create(bind, checkfirst=False)

    # 4. Cast every column using the enum over to the new type.
    for table, column in _ENUM_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE application_status "
            f"USING {column}::text::application_status"
        )

    # 5. Restore the default and drop the old type.
    op.alter_column("applications", "status", server_default="APPLIED")
    op.execute("DROP TYPE application_status_old")


def downgrade() -> None:
    bind = op.get_bind()

    op.execute("SET app.bypass_rls = 'on'")

    # HIRED doesn't exist in the old schema — fold it back into SELECTED,
    # the state it was reached from, before the type swap.
    op.execute("UPDATE applications SET status = 'SELECTED' WHERE status = 'HIRED'")
    op.execute(
        "UPDATE application_status_history SET from_status = 'SELECTED' "
        "WHERE from_status = 'HIRED'"
    )
    op.execute(
        "UPDATE application_status_history SET to_status = 'SELECTED' "
        "WHERE to_status = 'HIRED'"
    )

    op.alter_column("applications", "status", server_default=None)
    op.execute("ALTER TYPE application_status RENAME TO application_status_new")
    old_enum = postgresql.ENUM(*_OLD_VALUES, name="application_status")
    old_enum.create(bind, checkfirst=False)

    for table, column in _ENUM_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE application_status "
            f"USING {column}::text::application_status"
        )

    op.alter_column("applications", "status", server_default="APPLIED")
    op.execute("DROP TYPE application_status_new")
