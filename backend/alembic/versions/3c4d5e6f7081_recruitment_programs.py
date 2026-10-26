"""Recruitment programs (MTech/PhD Scholarship & Paid Training), enrollments

Revision ID: 3c4d5e6f7081
Revises: 2b3c4d5e6f70

* `candidates`: self-declared education fields (education level, current
  study year, specialization, institution, expected completion date) — all
  nullable; existing candidates are unaffected.
* `recruitment_programs` (+ `recruitment_program_institutions`): program
  configuration. Every not-yet-finalized business value (fee, scholarship
  amount, disbursement timing, bond start rule) is nullable; training
  duration options are constrained to the approved 6/12 months.
* `program_enrollments`: one per program application — eligibility review,
  interview gate, scholarship eligibility / decision, training, offer,
  agreement and bond.
* `scholarship_disbursements` and `program_fee_payments`: manual,
  append-only financial tracking (no payment gateway, no card/bank data).
* Permissions: `program.read`, `program.manage`, `program.finance.manage`.

Downgrade drops all of the above (program records go with their tables).
"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.rls import disable_tenant_rls, enable_indirect_tenant_rls, enable_tenant_rls

# revision identifiers, used by Alembic.
revision: str = "3c4d5e6f7081"
down_revision: str | None = "2b3c4d5e6f70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CURRENT_ORG = "NULLIF(current_setting('app.current_org_id', true), '')::uuid"

_PERMISSIONS = (
    ("program.read", "View recruitment programs and their participants."),
    ("program.manage", "Create, edit and publish recruitment programs; review eligibility and track training/employment."),
    ("program.finance.manage", "Record scholarship decisions/disbursements, training-fee payments and agreement/bond details."),
)
_ROLE_PERMISSIONS = {
    "ORG_ADMIN": ("program.read", "program.manage", "program.finance.manage"),
    "RECRUITER": ("program.read", "program.manage"),
    "HIRING_MANAGER": ("program.read",),
}

_ENUMS: dict[str, tuple[str, ...]] = {
    "education_level": ("DIPLOMA", "BACHELORS", "MASTERS", "MTECH", "PHD", "OTHER"),
    "program_status": ("DRAFT", "PUBLISHED", "PAUSED", "CLOSED", "ARCHIVED"),
    "program_eligibility_status": ("PENDING", "ELIGIBLE", "NOT_ELIGIBLE"),
    "scholarship_eligibility": ("NOT_DETERMINED", "ELIGIBLE", "NOT_ELIGIBLE"),
    "scholarship_status": ("NOT_STARTED", "PENDING", "APPROVED", "DECLINED"),
    "training_status": ("NOT_ENROLLED", "ENROLLED", "IN_PROGRESS", "COMPLETED", "DISCONTINUED"),
    "program_offer_status": ("NOT_OFFERED", "OFFERED", "ACCEPTED", "DECLINED", "WITHDRAWN"),
    "program_agreement_status": ("NOT_REQUIRED", "PENDING", "SENT", "SIGNED", "DECLINED", "VOID"),
    "scholarship_disbursement_status": ("SCHEDULED", "PAID", "CANCELLED"),
    "program_payment_method": ("BANK_TRANSFER", "UPI", "CHEQUE", "CASH", "OTHER"),
    "program_payment_record_status": ("RECORDED", "VOIDED"),
}


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
    op.bulk_insert(
        sa.table("role_permissions", sa.column("role_id", sa.UUID()), sa.column("permission_id", sa.UUID())),
        rows,
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name in _ENUMS:
        _enum(name).create(bind, checkfirst=True)

    # --- candidates: declared education ---
    op.add_column("candidates", sa.Column("education_level", _enum("education_level"), nullable=True))
    op.add_column("candidates", sa.Column("current_study_year", sa.Integer(), nullable=True))
    op.add_column("candidates", sa.Column("specialization", sa.String(length=150), nullable=True))
    op.add_column("candidates", sa.Column("institution_id", sa.UUID(), nullable=True))
    op.add_column("candidates", sa.Column("institution_name", sa.String(length=255), nullable=True))
    op.add_column("candidates", sa.Column("expected_completion_date", sa.Date(), nullable=True))
    op.create_foreign_key(
        op.f("fk_candidates_institution_id_institutions"),
        "candidates", "institutions", ["institution_id"], ["id"], ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_candidates_current_study_year_range", "candidates",
        "current_study_year IS NULL OR (current_study_year >= 1 AND current_study_year <= 10)",
    )

    # --- recruitment_programs ---
    t = "recruitment_programs"
    op.create_table(
        t,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", _enum("program_status"), server_default="DRAFT", nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("eligible_education_levels", postgresql.ARRAY(sa.String(length=30)), server_default="{}", nullable=False),
        sa.Column("eligible_study_years", postgresql.ARRAY(sa.Integer()), server_default="{}", nullable=False),
        sa.Column("eligible_specializations", postgresql.ARRAY(sa.String(length=150)), server_default="{}", nullable=False),
        sa.Column("expected_completion_from", sa.Date(), nullable=True),
        sa.Column("expected_completion_to", sa.Date(), nullable=True),
        sa.Column("eligibility_notes", sa.Text(), nullable=True),
        sa.Column("training_duration_options", postgresql.ARRAY(sa.Integer()), server_default="{}", nullable=False),
        sa.Column("training_fee_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("training_fee_currency", sa.String(length=3), nullable=True),
        sa.Column("fee_payment_terms", sa.Text(), nullable=True),
        sa.Column("fee_disclosure", sa.Text(), nullable=True),
        sa.Column("scholarship_amount_min", sa.Numeric(12, 2), nullable=True),
        sa.Column("scholarship_amount_max", sa.Numeric(12, 2), nullable=True),
        sa.Column("scholarship_currency", sa.String(length=3), nullable=True),
        sa.Column("scholarship_eligibility_rules", sa.Text(), nullable=True),
        sa.Column("scholarship_disbursement_timing", sa.Text(), nullable=True),
        sa.Column("scholarship_disclosure", sa.Text(), nullable=True),
        sa.Column("employment_pathway", sa.Text(), nullable=True),
        sa.Column("bond_duration_months", sa.Integer(), nullable=True),
        sa.Column("bond_terms_summary", sa.Text(), nullable=True),
        sa.Column("bond_start_rule", sa.Text(), nullable=True),
        sa.Column("application_opens_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("application_closes_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assessment_id", sa.UUID(), nullable=True),
        sa.Column("interview_stages", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("required_documents", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("candidate_disclosures", sa.Text(), nullable=True),
        sa.Column("terms_version", sa.String(length=50), nullable=True),
        sa.Column("tracks_fee_payments", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("tracks_training", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("tracks_employment", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("requires_agreement", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_user_id", sa.UUID(), nullable=True),
        *_timestamps(),
        _fk(t, "organization_id", "organizations", "RESTRICT"),
        _fk(t, "job_id", "jobs", "RESTRICT"),
        _fk(t, "assessment_id", "assessments", "RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f(f"fk_{t}_created_by_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["published_by_user_id"], ["users.id"], name=op.f(f"fk_{t}_published_by_user_id_users"), ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "training_duration_options <@ ARRAY[6, 12]", name=op.f(f"ck_{t}_training_durations_approved")
        ),
        sa.CheckConstraint(
            "training_fee_amount IS NULL OR training_fee_amount >= 0", name=op.f(f"ck_{t}_fee_non_negative")
        ),
        sa.CheckConstraint(
            "scholarship_amount_min IS NULL OR scholarship_amount_min >= 0",
            name=op.f(f"ck_{t}_scholarship_min_non_negative"),
        ),
        sa.CheckConstraint(
            "scholarship_amount_max IS NULL OR scholarship_amount_min IS NULL "
            "OR scholarship_amount_max >= scholarship_amount_min",
            name=op.f(f"ck_{t}_scholarship_range"),
        ),
        sa.CheckConstraint(
            "bond_duration_months IS NULL OR bond_duration_months > 0",
            name=op.f(f"ck_{t}_bond_duration_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.UniqueConstraint("job_id", name=op.f(f"uq_{t}_job_id")),
    )
    op.create_index(op.f(f"ix_{t}_organization_id"), t, ["organization_id"])
    enable_tenant_rls(op, t)

    # --- recruitment_program_institutions ---
    t = "recruitment_program_institutions"
    op.create_table(
        t,
        sa.Column("program_id", sa.UUID(), nullable=False),
        sa.Column("institution_id", sa.UUID(), nullable=False),
        _fk(t, "program_id", "recruitment_programs", "CASCADE"),
        _fk(t, "institution_id", "institutions", "RESTRICT"),
        sa.PrimaryKeyConstraint("program_id", "institution_id", name=op.f(f"pk_{t}")),
    )
    op.create_index(op.f(f"ix_{t}_institution_id"), t, ["institution_id"])
    enable_indirect_tenant_rls(
        op, t,
        exists_subquery=(
            "EXISTS (SELECT 1 FROM recruitment_programs rp "
            "WHERE rp.id = recruitment_program_institutions.program_id "
            f"AND rp.organization_id = {_CURRENT_ORG})"
        ),
    )

    # --- program_enrollments ---
    t = "program_enrollments"
    op.create_table(
        t,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("program_id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("education_level", _enum("education_level"), nullable=False),
        sa.Column("study_year", sa.Integer(), nullable=True),
        sa.Column("specialization", sa.String(length=150), nullable=False),
        sa.Column("institution_id", sa.UUID(), nullable=True),
        sa.Column("institution_name", sa.String(length=255), nullable=False),
        sa.Column("expected_completion_date", sa.Date(), nullable=True),
        sa.Column("terms_version_accepted", sa.String(length=50), nullable=False),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disclosure_snapshot", sa.Text(), nullable=False),
        sa.Column("eligibility_status", _enum("program_eligibility_status"), server_default="PENDING", nullable=False),
        sa.Column("eligibility_reviewed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("eligibility_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eligibility_note", sa.Text(), nullable=True),
        sa.Column("interview_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interview_completed_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "scholarship_eligibility", _enum("scholarship_eligibility"),
            server_default="NOT_DETERMINED", nullable=False,
        ),
        sa.Column("scholarship_eligibility_decided_by_user_id", sa.UUID(), nullable=True),
        sa.Column("scholarship_eligibility_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scholarship_status", _enum("scholarship_status"), server_default="NOT_STARTED", nullable=False),
        sa.Column("approved_scholarship_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("approved_scholarship_currency", sa.String(length=3), nullable=True),
        sa.Column("scholarship_decided_by_user_id", sa.UUID(), nullable=True),
        sa.Column("scholarship_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scholarship_decision_note", sa.Text(), nullable=True),
        sa.Column("training_status", _enum("training_status"), server_default="NOT_ENROLLED", nullable=False),
        sa.Column("training_duration_months", sa.Integer(), nullable=True),
        sa.Column("training_start_date", sa.Date(), nullable=True),
        sa.Column("training_end_date", sa.Date(), nullable=True),
        sa.Column("offer_status", _enum("program_offer_status"), server_default="NOT_OFFERED", nullable=False),
        sa.Column("offer_made_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("joined_on", sa.Date(), nullable=True),
        sa.Column(
            "agreement_status", _enum("program_agreement_status"), server_default="NOT_REQUIRED", nullable=False
        ),
        sa.Column("agreement_reference", sa.String(length=255), nullable=True),
        sa.Column("agreement_signed_on", sa.Date(), nullable=True),
        sa.Column("agreement_document_storage_provider", sa.String(length=50), nullable=True),
        sa.Column("agreement_document_storage_path", sa.String(length=1000), nullable=True),
        sa.Column("agreement_document_filename", sa.String(length=500), nullable=True),
        sa.Column("agreement_document_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bond_duration_months", sa.Integer(), nullable=True),
        sa.Column("bond_start_date", sa.Date(), nullable=True),
        sa.Column("bond_end_date", sa.Date(), nullable=True),
        *_timestamps(),
        _fk(t, "organization_id", "organizations", "RESTRICT"),
        _fk(t, "program_id", "recruitment_programs", "RESTRICT"),
        _fk(t, "application_id", "applications", "RESTRICT"),
        _fk(t, "candidate_id", "candidates", "RESTRICT"),
        _fk(t, "institution_id", "institutions", "RESTRICT"),
        *[
            sa.ForeignKeyConstraint([c], ["users.id"], name=op.f(f"fk_{t}_{c}_users"), ondelete="SET NULL")
            for c in (
                "eligibility_reviewed_by_user_id",
                "interview_completed_by_user_id",
                "scholarship_eligibility_decided_by_user_id",
                "scholarship_decided_by_user_id",
            )
        ],
        sa.CheckConstraint(
            "training_duration_months IS NULL OR training_duration_months IN (6, 12)",
            name=op.f(f"ck_{t}_training_duration_approved"),
        ),
        sa.CheckConstraint(
            "approved_scholarship_amount IS NULL OR approved_scholarship_amount >= 0",
            name=op.f(f"ck_{t}_scholarship_amount_non_negative"),
        ),
        sa.CheckConstraint(
            "bond_end_date IS NULL OR bond_start_date IS NULL OR bond_end_date > bond_start_date",
            name=op.f(f"ck_{t}_bond_dates_ordered"),
        ),
        sa.CheckConstraint(
            "(agreement_document_storage_path IS NULL) = (agreement_document_storage_provider IS NULL) AND "
            "(agreement_document_storage_path IS NULL) = (agreement_document_filename IS NULL) AND "
            "(agreement_document_storage_path IS NULL) = (agreement_document_uploaded_at IS NULL)",
            name=op.f(f"ck_{t}_agreement_document_complete"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.UniqueConstraint("application_id", name=op.f(f"uq_{t}_application_id")),
    )
    op.create_index(op.f(f"ix_{t}_organization_id"), t, ["organization_id"])
    op.create_index("ix_program_enrollments_program_id", t, ["program_id"])
    op.create_index(op.f(f"ix_{t}_candidate_id"), t, ["candidate_id"])
    enable_tenant_rls(op, t)

    # --- scholarship_disbursements ---
    t = "scholarship_disbursements"
    op.create_table(
        t,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("enrollment_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", _enum("scholarship_disbursement_status"), server_default="SCHEDULED", nullable=False),
        sa.Column("scheduled_on", sa.Date(), nullable=True),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column("reference", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("recorded_by_user_id", sa.UUID(), nullable=True),
        *_timestamps(),
        _fk(t, "organization_id", "organizations", "RESTRICT"),
        _fk(t, "enrollment_id", "program_enrollments", "RESTRICT"),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"], ["users.id"], name=op.f(f"fk_{t}_recorded_by_user_id_users"), ondelete="SET NULL"
        ),
        sa.CheckConstraint("amount > 0", name=op.f(f"ck_{t}_amount_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
    )
    op.create_index(op.f(f"ix_{t}_organization_id"), t, ["organization_id"])
    op.create_index(op.f(f"ix_{t}_enrollment_id"), t, ["enrollment_id"])
    enable_tenant_rls(op, t)

    # --- program_fee_payments ---
    t = "program_fee_payments"
    op.create_table(
        t,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("enrollment_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("method", _enum("program_payment_method"), nullable=False),
        sa.Column("reference", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", _enum("program_payment_record_status"), server_default="RECORDED", nullable=False),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("voided_by_user_id", sa.UUID(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        _fk(t, "organization_id", "organizations", "RESTRICT"),
        _fk(t, "enrollment_id", "program_enrollments", "RESTRICT"),
        sa.ForeignKeyConstraint(
            ["voided_by_user_id"], ["users.id"], name=op.f(f"fk_{t}_voided_by_user_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"], ["users.id"], name=op.f(f"fk_{t}_recorded_by_user_id_users"), ondelete="SET NULL"
        ),
        sa.CheckConstraint("amount > 0", name=op.f(f"ck_{t}_amount_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
    )
    op.create_index(op.f(f"ix_{t}_organization_id"), t, ["organization_id"])
    op.create_index(op.f(f"ix_{t}_enrollment_id"), t, ["enrollment_id"])
    enable_tenant_rls(op, t)

    _seed_permissions()


def downgrade() -> None:
    bind = op.get_bind()
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
        "program_fee_payments",
        "scholarship_disbursements",
        "program_enrollments",
        "recruitment_program_institutions",
        "recruitment_programs",
    ):
        disable_tenant_rls(op, table)
        op.drop_table(table)

    op.drop_constraint("ck_candidates_current_study_year_range", "candidates", type_="check")
    op.drop_constraint(op.f("fk_candidates_institution_id_institutions"), "candidates", type_="foreignkey")
    for column in (
        "expected_completion_date", "institution_name", "institution_id",
        "specialization", "current_study_year", "education_level",
    ):
        op.drop_column("candidates", column)

    for name in reversed(list(_ENUMS)):
        sa.Enum(name=name).drop(bind, checkfirst=True)
