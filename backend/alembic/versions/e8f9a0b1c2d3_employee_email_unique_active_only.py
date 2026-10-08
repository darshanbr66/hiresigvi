"""Employee email uniqueness applies to non-removed employees only.

Removing an employee who left the company is a soft delete (`deleted_at`,
app/services/employee_service.py::delete_employee): the row stays for
history. With `uq_employees_org_email` covering every row, the same person
could never be added again after a rehire — HR would get "already exists"
for someone no longer visible anywhere. The index becomes partial
(`WHERE deleted_at IS NULL`), so uniqueness still holds among the
organization's current employees.

Downgrade restores the full index; it fails if a removed employee and a
current one now share an email, which is the correct outcome rather than
silently dropping either row.

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8f9a0b1c2d3"
down_revision: str | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_employees_org_email", table_name="employees")
    op.create_index(
        "uq_employees_org_email",
        "employees",
        ["organization_id", "email"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_employees_org_email", table_name="employees")
    op.create_index(
        "uq_employees_org_email", "employees", ["organization_id", "email"], unique=True
    )
