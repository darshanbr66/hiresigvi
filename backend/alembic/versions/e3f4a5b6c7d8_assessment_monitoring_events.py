"""assessment monitoring events (transparent proctoring)

Revision ID: e3f4a5b6c7d8
Revises: d2b3c4e5f6a7
Create Date: 2026-09-18 13:00:00.000000

Adds `assessment_monitoring_events` (ZPHIREX platform overhaul § 5-7):
one row per observed browser-monitoring event during a candidate's
attempt (tab switch, window blur/focus, fullscreen exit, camera/mic
permission or device change, connection interruption, consent given).
No raw audio/video, no new PII beyond what's listed in the model
docstring. Tenancy is indirect via `invitation_id ->
assessment_invitations.organization_id`, same pattern as
`candidate_answers`/`assessment_results` (89d2c5e04700).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.rls import enable_indirect_tenant_rls

# revision identifiers, used by Alembic.
revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, None] = 'd2b3c4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EVENT_TYPES = (
    "MONITORING_CONSENT_GIVEN",
    "TAB_SWITCH",
    "WINDOW_BLUR",
    "WINDOW_FOCUS",
    "FULLSCREEN_EXIT",
    "CAMERA_PERMISSION_CHANGED",
    "MICROPHONE_PERMISSION_CHANGED",
    "CAMERA_DEVICE_CHANGED",
    "MICROPHONE_DEVICE_CHANGED",
    "CAMERA_UNAVAILABLE",
    "MICROPHONE_UNAVAILABLE",
    "CONNECTION_INTERRUPTED",
    "CONNECTION_RESTORED",
)


def upgrade() -> None:
    op.create_table(
        "assessment_monitoring_events",
        sa.Column("invitation_id", sa.UUID(), nullable=False),
        sa.Column(
            "event_type",
            postgresql.ENUM(*_EVENT_TYPES, name="monitoring_event_type"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("event_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["invitation_id"], ["assessment_invitations.id"],
            name=op.f("fk_assessment_monitoring_events_invitation_id_assessment_invitations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_monitoring_events")),
    )
    op.create_index(
        op.f("ix_assessment_monitoring_events_invitation_id"),
        "assessment_monitoring_events", ["invitation_id"], unique=False,
    )

    enable_indirect_tenant_rls(
        op, "assessment_monitoring_events",
        exists_subquery=(
            "EXISTS (SELECT 1 FROM assessment_invitations ai "
            "WHERE ai.id = assessment_monitoring_events.invitation_id "
            "AND ai.organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON assessment_monitoring_events")
    op.execute("ALTER TABLE assessment_monitoring_events DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        op.f("ix_assessment_monitoring_events_invitation_id"),
        table_name="assessment_monitoring_events",
    )
    op.drop_table("assessment_monitoring_events")
    sa.Enum(name="monitoring_event_type").drop(connection, checkfirst=True)
