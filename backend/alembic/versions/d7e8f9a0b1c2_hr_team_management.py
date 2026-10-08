"""HR (RECRUITER) team and department management.

HR accounts on this platform hold the RECRUITER role. Until now the Team
page's account management (`user.read`/`user.create`/`user.update`) and the
department hierarchy's write side (`department.manage`/`employee.manage`)
were ORG_ADMIN-only. This grants those *existing* permissions to RECRUITER —
no new role, no new authorization mechanism.

It also adds `user.manage_admins` (ORG_ADMIN only): creating an Org Admin,
changing a role to or from Org Admin, deactivating/reactivating an Org Admin,
and changing one's own role. `user.create`/`user.update` alone no longer
reach any of those (app/services/user_service.py), so HR can manage the
team without being able to make itself — or anyone else — an admin.

Organization settings, Activities deletion, Talk to Admin's inbox and every
other ORG_ADMIN-only permission are untouched. HIRING_MANAGER and
INTERVIEWER are unchanged.

Revision ID: d7e8f9a0b1c2
Revises: c5d6e7f8a9b0
"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e8f9a0b1c2"
down_revision: str | None = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_PERMISSION = (
    "user.manage_admins",
    "Create, promote, demote, deactivate, or reactivate an Org Admin, or change one's own role.",
)
_NEW_PERMISSION_ROLES = ("ORG_ADMIN",)

#: Existing permissions newly granted to RECRUITER (HR).
_RECRUITER_GRANTS = (
    "user.read",
    "user.create",
    "user.update",
    "department.manage",
    "employee.manage",
)

_ROLE_ID_SQL = sa.text("SELECT id FROM roles WHERE organization_id IS NULL AND name = :name")
_PERMISSION_ID_SQL = sa.text("SELECT id FROM permissions WHERE code = :code")


def upgrade() -> None:
    bind = op.get_bind()
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.UUID()),
        sa.column("permission_id", sa.UUID()),
    )

    code, description = _NEW_PERMISSION
    permission_id = str(uuid.uuid4())
    op.bulk_insert(
        sa.table(
            "permissions",
            sa.column("id", sa.UUID()),
            sa.column("code", sa.String()),
            sa.column("description", sa.Text()),
        ),
        [{"id": permission_id, "code": code, "description": description}],
    )
    rows = [
        {
            "role_id": bind.execute(_ROLE_ID_SQL, {"name": name}).scalar_one(),
            "permission_id": permission_id,
        }
        for name in _NEW_PERMISSION_ROLES
    ]

    recruiter_id = bind.execute(_ROLE_ID_SQL, {"name": "RECRUITER"}).scalar_one()
    for grant in _RECRUITER_GRANTS:
        rows.append(
            {
                "role_id": recruiter_id,
                "permission_id": bind.execute(_PERMISSION_ID_SQL, {"code": grant}).scalar_one(),
            }
        )
    op.bulk_insert(role_permissions, rows)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions "
            "WHERE role_id = "
            "(SELECT id FROM roles WHERE organization_id IS NULL AND name = 'RECRUITER') "
            "AND permission_id IN (SELECT id FROM permissions WHERE code = ANY(:codes))"
        ),
        {"codes": list(_RECRUITER_GRANTS)},
    )
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE code = :code)"
        ),
        {"code": _NEW_PERMISSION[0]},
    )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE code = :code"), {"code": _NEW_PERMISSION[0]}
    )
