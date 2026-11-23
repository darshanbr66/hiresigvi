"""Progressive apply flow: drafts, job qualification cache, academic documents.

Additive only — no existing row is changed or removed:

* `job_qualification_profiles` — a job's qualification requirements derived
  by AI from its own JD text, cached per JD version (`source_hash`).
* `application_drafts` — a self-service application in progress (never an
  Application), holding each step's validated data server-side.
* `academic_documents` — metadata of the uploaded marks card/certificate
  (bytes live in the storage abstraction, never in Postgres).
* `education_verification_runs` — every AI consistency check, kept as
  history.
* `applications.education_verification_status` (nullable; NULL for every
  existing application) and `candidates.qualification_year` (nullable).

All four tables are tenant-owned and get the standard RLS policy.

Revision ID: 9c0d1e2f3a4b
Revises: 8b9c0d1e2f3a
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.rls import disable_tenant_rls, enable_tenant_rls

# revision identifiers, used by Alembic.
revision: str = "9c0d1e2f3a4b"
down_revision: str | None = "8b9c0d1e2f3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "job_qualification_profiles",
    "application_drafts",
    "academic_documents",
    "education_verification_runs",
)
_ENUMS = (
    "application_education_status",
    "education_verification_outcome",
    "education_check_status",
    "application_draft_status",
    "job_qualification_status",
)


def upgrade() -> None:
    op.create_table('job_qualification_profiles',
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('source_hash', sa.String(length=64), nullable=False),
    sa.Column('status', sa.Enum('READY', 'UNAVAILABLE', name='job_qualification_status'), nullable=False),
    sa.Column('requirements', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('provider', sa.String(length=50), nullable=True),
    sa.Column('model', sa.String(length=100), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('analyzed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_job_qualification_profiles_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_job_qualification_profiles_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_job_qualification_profiles')),
    sa.UniqueConstraint('job_id', name=op.f('uq_job_qualification_profiles_job_id'))
    )
    op.create_index(op.f('ix_job_qualification_profiles_organization_id'), 'job_qualification_profiles', ['organization_id'], unique=False)
    op.create_table('application_drafts',
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('campus_drive_id', sa.UUID(), nullable=True),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'SUBMITTED', name='application_draft_status'), server_default='OPEN', nullable=False),
    sa.Column('full_name', sa.Text(), nullable=False),
    sa.Column('phone', sa.String(length=32), nullable=False),
    sa.Column('background', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('education', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('education_status', sa.Enum('NOT_STARTED', 'VERIFIED', 'MISMATCH', 'UNCLEAR', 'FAILED', 'MANUAL_REVIEW', name='education_check_status'), server_default='NOT_STARTED', nullable=False),
    sa.Column('qualification_source_hash', sa.String(length=64), nullable=True),
    sa.Column('academic_document_id', sa.UUID(), nullable=True),
    sa.Column('verification_attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('submitted_application_id', sa.UUID(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['campus_drive_id'], ['campus_drives.id'], name=op.f('fk_application_drafts_campus_drive_id_campus_drives'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_application_drafts_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_application_drafts_organization_id_organizations'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['submitted_application_id'], ['applications.id'], name=op.f('fk_application_drafts_submitted_application_id_applications'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_application_drafts'))
    )
    op.create_index(op.f('ix_application_drafts_job_id'), 'application_drafts', ['job_id'], unique=False)
    op.create_index(op.f('ix_application_drafts_organization_id'), 'application_drafts', ['organization_id'], unique=False)
    op.create_index('uq_application_drafts_open_per_job', 'application_drafts', ['organization_id', 'email', 'job_id'], unique=True, postgresql_where=sa.text("status = 'OPEN'"))
    op.create_table('academic_documents',
    sa.Column('draft_id', sa.UUID(), nullable=True),
    sa.Column('candidate_id', sa.UUID(), nullable=True),
    sa.Column('application_id', sa.UUID(), nullable=True),
    sa.Column('qualification_key', sa.String(length=50), nullable=False),
    sa.Column('storage_provider', sa.String(length=50), nullable=False),
    sa.Column('storage_path', sa.String(length=1000), nullable=False),
    sa.Column('original_filename', sa.String(length=500), nullable=False),
    sa.Column('content_type', sa.String(length=150), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_academic_documents_application_id_applications'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_academic_documents_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['draft_id'], ['application_drafts.id'], name=op.f('fk_academic_documents_draft_id_application_drafts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_academic_documents_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_academic_documents'))
    )
    op.create_index(op.f('ix_academic_documents_application_id'), 'academic_documents', ['application_id'], unique=False)
    op.create_index(op.f('ix_academic_documents_draft_id'), 'academic_documents', ['draft_id'], unique=False)
    op.create_index(op.f('ix_academic_documents_organization_id'), 'academic_documents', ['organization_id'], unique=False)
    op.create_table('education_verification_runs',
    sa.Column('draft_id', sa.UUID(), nullable=True),
    sa.Column('application_id', sa.UUID(), nullable=True),
    sa.Column('academic_document_id', sa.UUID(), nullable=True),
    sa.Column('qualification_key', sa.String(length=50), nullable=False),
    sa.Column('outcome', sa.Enum('MATCH', 'MISMATCH', 'UNCLEAR', 'FAILED', name='education_verification_outcome'), nullable=False),
    sa.Column('reason_code', sa.String(length=50), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('candidate_message', sa.Text(), nullable=False),
    sa.Column('analysis', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('provider', sa.String(length=50), nullable=True),
    sa.Column('model', sa.String(length=100), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('qualification_source_hash', sa.String(length=64), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['academic_document_id'], ['academic_documents.id'], name=op.f('fk_education_verification_runs_academic_document_id_academic_documents'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_education_verification_runs_application_id_applications'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['draft_id'], ['application_drafts.id'], name=op.f('fk_education_verification_runs_draft_id_application_drafts'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_education_verification_runs_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_education_verification_runs'))
    )
    op.create_index(op.f('ix_education_verification_runs_application_id'), 'education_verification_runs', ['application_id'], unique=False)
    op.create_index(op.f('ix_education_verification_runs_draft_id'), 'education_verification_runs', ['draft_id'], unique=False)
    op.create_index(op.f('ix_education_verification_runs_organization_id'), 'education_verification_runs', ['organization_id'], unique=False)
    education_status = postgresql.ENUM('VERIFIED', 'MANUAL_REVIEW', name='application_education_status')
    education_status.create(op.get_bind(), checkfirst=True)
    op.add_column('applications', sa.Column('education_verification_status', postgresql.ENUM('VERIFIED', 'MANUAL_REVIEW', name='application_education_status', create_type=False), nullable=True))
    op.add_column('candidates', sa.Column('qualification_year', sa.Integer(), nullable=True))
    # Drafts <-> documents reference each other; this side is added once
    # both tables exist.
    op.create_foreign_key(
        op.f('fk_application_drafts_academic_document_id_academic_documents'),
        'application_drafts', 'academic_documents', ['academic_document_id'], ['id'],
        ondelete='SET NULL',
    )
    for table in _TABLES:
        enable_tenant_rls(op, table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        disable_tenant_rls(op, table)
    op.drop_constraint(
        op.f('fk_application_drafts_academic_document_id_academic_documents'),
        'application_drafts', type_='foreignkey',
    )
    op.drop_column('candidates', 'qualification_year')
    op.drop_column('applications', 'education_verification_status')
    op.drop_index(op.f('ix_education_verification_runs_organization_id'), table_name='education_verification_runs')
    op.drop_index(op.f('ix_education_verification_runs_draft_id'), table_name='education_verification_runs')
    op.drop_index(op.f('ix_education_verification_runs_application_id'), table_name='education_verification_runs')
    op.drop_table('education_verification_runs')
    op.drop_index(op.f('ix_academic_documents_organization_id'), table_name='academic_documents')
    op.drop_index(op.f('ix_academic_documents_draft_id'), table_name='academic_documents')
    op.drop_index(op.f('ix_academic_documents_application_id'), table_name='academic_documents')
    op.drop_table('academic_documents')
    op.drop_index('uq_application_drafts_open_per_job', table_name='application_drafts', postgresql_where=sa.text("status = 'OPEN'"))
    op.drop_index(op.f('ix_application_drafts_organization_id'), table_name='application_drafts')
    op.drop_index(op.f('ix_application_drafts_job_id'), table_name='application_drafts')
    op.drop_table('application_drafts')
    op.drop_index(op.f('ix_job_qualification_profiles_organization_id'), table_name='job_qualification_profiles')
    op.drop_table('job_qualification_profiles')
    for enum in _ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {enum}")
