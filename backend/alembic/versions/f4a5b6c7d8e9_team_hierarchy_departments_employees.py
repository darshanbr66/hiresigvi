"""team hierarchy: departments and employees

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-09-18 14:00:00.000000

New, additive domain (ZPHIREX platform overhaul § 9-13) — an HR/org-chart
directory, deliberately separate from the existing `users`/RBAC (portal
login) domain, which is untouched by this migration. `department.manage`
and `employee.manage` are ORG_ADMIN-only; `RECRUITER`/`HIRING_MANAGER` get
read-only visibility into the hierarchy (view-only in v1 — a narrower
"edit basic fields" grant for recruiters is a documented follow-up, not
blocking).
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.rls import enable_tenant_rls

# revision identifiers, used by Alembic.
revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSIONS = (
    ("department.manage", "Create, edit, delete, and reassign employees for departments."),
    ("department.read", "View the department hierarchy."),
    ("employee.manage", "Create, edit, move, deactivate, and reactivate employees."),
    ("employee.read", "View employee directory records."),
)
_ROLE_PERMISSIONS = {
    "ORG_ADMIN": ("department.manage", "department.read", "employee.manage", "employee.read"),
    "RECRUITER": ("department.read", "employee.read"),
    "HIRING_MANAGER": ("department.read", "employee.read"),
}


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "departments",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("deletion_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name=op.f("fk_departments_organization_id_organizations"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by_user_id"], ["users.id"],
            name=op.f("fk_departments_deleted_by_user_id_users"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
    )
    op.create_index(op.f("ix_departments_organization_id"), "departments", ["organization_id"], unique=False)

    op.create_table(
        "employees",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("employee_code", sa.String(length=50), nullable=True),
        sa.Column("designation", sa.String(length=255), nullable=True),
        sa.Column("department_id", sa.UUID(), nullable=True),
        sa.Column("manager_id", sa.UUID(), nullable=True),
        sa.Column("joining_date", sa.Date(), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column(
            "employment_status",
            postgresql.ENUM("ACTIVE", "INACTIVE", name="employment_status"),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("deletion_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name=op.f("fk_employees_organization_id_organizations"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"],
            name=op.f("fk_employees_department_id_departments"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["manager_id"], ["employees.id"],
            name=op.f("fk_employees_manager_id_employees"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_employees_user_id_users"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name=op.f("fk_employees_created_by_users"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by_user_id"], ["users.id"],
            name=op.f("fk_employees_deleted_by_user_id_users"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employees")),
    )
    op.create_index(op.f("ix_employees_organization_id"), "employees", ["organization_id"], unique=False)
    op.create_index(op.f("ix_employees_department_id"), "employees", ["department_id"], unique=False)
    op.create_index(op.f("ix_employees_manager_id"), "employees", ["manager_id"], unique=False)
    op.create_index(
        "uq_employees_org_email", "employees", ["organization_id", "email"], unique=True,
    )

    # --- Row-Level Security ---
    enable_tenant_rls(op, "departments")
    enable_tenant_rls(op, "employees")

    # --- Permissions ---
    permissions_table = sa.table(
        "permissions",
        sa.column("id", sa.UUID()),
        sa.column("code", sa.String()),
        sa.column("description", sa.Text()),
    )
    permission_ids = {code: str(uuid.uuid4()) for code, _ in _PERMISSIONS}
    op.bulk_insert(
        permissions_table,
        [
            {"id": permission_ids[code], "code": code, "description": description}
            for code, description in _PERMISSIONS
        ],
    )

    role_ids = {
        name: row[0]
        for name in _ROLE_PERMISSIONS
        for row in bind.execute(
            sa.text("SELECT id FROM roles WHERE organization_id IS NULL AND name = :name"),
            {"name": name},
        )
    }

    role_permissions_table = sa.table(
        "role_permissions",
        sa.column("role_id", sa.UUID()),
        sa.column("permission_id", sa.UUID()),
    )
    op.bulk_insert(
        role_permissions_table,
        [
            {"role_id": role_ids[role_name], "permission_id": permission_ids[code]}
            for role_name, codes in _ROLE_PERMISSIONS.items()
            for code in codes
        ],
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE code = ANY(:codes))"
        ),
        {"codes": [code for code, _ in _PERMISSIONS]},
    )
    connection.execute(
        sa.text("DELETE FROM permissions WHERE code = ANY(:codes)"),
        {"codes": [code for code, _ in _PERMISSIONS]},
    )

    from app.db.rls import disable_tenant_rls

    disable_tenant_rls(op, "employees")
    disable_tenant_rls(op, "departments")

    op.drop_index("uq_employees_org_email", table_name="employees")
    op.drop_index(op.f("ix_employees_manager_id"), table_name="employees")
    op.drop_index(op.f("ix_employees_department_id"), table_name="employees")
    op.drop_index(op.f("ix_employees_organization_id"), table_name="employees")
    op.drop_table("employees")
    sa.Enum(name="employment_status").drop(connection, checkfirst=True)

    op.drop_index(op.f("ix_departments_organization_id"), table_name="departments")
    op.drop_table("departments")
