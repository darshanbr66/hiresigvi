"""Program funding approvals, fee obligations, training progress, agreement
terms, employment review, interviews and program affiliation verification

Revision ID: 5e6f70819203
Revises: 4d5e6f708192

Additive only — every existing row keeps working unchanged:

* Enum values added: scholarship_status PROPOSED; training_status
  WITHDRAWN; program_agreement_status EXPIRED, TERMINATED;
  affiliation_verification_method STUDENT_ID_DOCUMENT,
  COORDINATOR_CONFIRMATION.
* recruitment_programs: company funding basis (type + value, both NULL until
  the business decides), independent-approval switch (default on) and the
  applicant affiliation-verification method (existing programs: NONE, i.e.
  unchanged behaviour).
* program_enrollments: the candidate's fee obligation snapshot (fee,
  currency, due date, company contribution, candidate payable), accepted
  funding revision, training enrollment/completion details, employment
  review, agreement version/terms snapshot/effective+expiry dates, bond
  reason, employee link. All NULL / default for existing rows.
* New tenant tables (RLS): program_funding_revisions, training_milestones,
  application_interviews.
* campus_affiliation_verifications can belong to a program application:
  campus_drive_id and institution_id become nullable, program_id added, and
  a check keeps exactly one context (and an institution for drive rows).
* assessment_results.pass_score_applied, backfilled from each attempt's
  assessment so existing results keep their meaning.
* Permissions interview.read / interview.manage / program.employment.approve.
* notification_type INTERVIEW_ASSIGNED (in-app notice to an assigned
  interviewer).

Downgrade drops everything added. Postgres can't remove enum values, so the
added values stay in their types; rows using them are first mapped back to
the closest pre-existing value (PROPOSED -> PENDING, WITHDRAWN ->
DISCONTINUED, EXPIRED -> SIGNED, TERMINATED -> VOID, the two evidence
methods -> MANUAL), and program affiliation verifications are deleted (the
pre-upgrade schema has no place for them).
"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.rls import disable_tenant_rls, enable_tenant_rls

# revision identifiers, used by Alembic.
revision: str = "5e6f70819203"
down_revision: str | None = "4d5e6f708192"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ADDED_VALUES: tuple[tuple[str, str], ...] = (
    ("scholarship_status", "PROPOSED"),
    ("training_status", "WITHDRAWN"),
    ("program_agreement_status", "EXPIRED"),
    ("program_agreement_status", "TERMINATED"),
    ("affiliation_verification_method", "STUDENT_ID_DOCUMENT"),
    ("affiliation_verification_method", "COORDINATOR_CONFIRMATION"),
    ("notification_type", "INTERVIEW_ASSIGNED"),
)

_ENUMS: dict[str, tuple[str, ...]] = {
    "program_funding_type": ("FIXED_AMOUNT", "PERCENTAGE"),
    "program_funding_revision_kind": ("INITIAL", "REVISION", "AMENDMENT"),
    "program_funding_revision_status": ("PROPOSED", "APPROVED", "REJECTED", "WITHDRAWN", "SUPERSEDED"),
    "training_milestone_status": ("PENDING", "COMPLETED", "MISSED"),
    "program_employment_review_status": ("NOT_STARTED", "IN_REVIEW", "APPROVED", "NOT_PROCEEDING"),
    "interview_status": ("SCHEDULED", "COMPLETED", "CANCELLED", "NO_SHOW"),
    "interview_outcome": ("PASS", "FAIL", "ON_HOLD"),
}

_PERMISSIONS = (
    ("interview.read", "View interviews (interviewers see feedback only on their own interviews)."),
    ("interview.manage", "Schedule interviews and record or view any interviewer's outcome and feedback."),
    ("program.employment.approve", "Approve or decline a program participant's progression to full-time employment."),
)
_ROLE_PERMISSIONS = {
    "ORG_ADMIN": ("interview.read", "interview.manage", "program.employment.approve"),
    "RECRUITER": ("interview.read", "interview.manage"),
    "HIRING_MANAGER": ("interview.read", "interview.manage", "program.employment.approve"),
    "INTERVIEWER": ("interview.read",),
}

_PROGRAM_COLUMNS = (
    "company_funding_type",
    "company_funding_value",
    "funding_requires_independent_approval",
    "affiliation_verification_method",
)
_ENROLLMENT_COLUMNS = (
    "fee_amount",
    "fee_currency",
    "fee_due_date",
    "company_contribution_amount",
    "candidate_payable_amount",
    "funding_terms_accepted_revision",
    "funding_terms_accepted_at",
    "training_enrolled_at",
    "training_completed_on",
    "training_outcome_note",
    "employment_review_status",
    "employment_role_title",
    "employment_department_id",
    "employment_proposed_start_date",
    "employment_decided_by_user_id",
    "employment_decided_at",
    "employment_decision_note",
    "employee_id",
    "agreement_version",
    "agreement_terms_snapshot",
    "agreement_sent_at",
    "agreement_effective_date",
    "agreement_expiry_date",
    "agreement_status_note",
    "bond_applies_reason",
)
_ENROLLMENT_CHECKS = (
    (
        "ck_program_enrollments_fee_obligation_valid",
        "fee_amount IS NULL OR (fee_amount >= 0 AND fee_currency IS NOT NULL)",
    ),
    (
        "ck_program_enrollments_contribution_within_fee",
        "company_contribution_amount IS NULL OR (company_contribution_amount >= 0 "
        "AND fee_amount IS NOT NULL AND company_contribution_amount <= fee_amount)",
    ),
    (
        "ck_program_enrollments_payable_consistent",
        "candidate_payable_amount IS NULL OR (fee_amount IS NOT NULL AND "
        "candidate_payable_amount = fee_amount - COALESCE(company_contribution_amount, 0))",
    ),
    (
        "ck_program_enrollments_training_completion_after_start",
        "training_completed_on IS NULL OR training_start_date IS NULL "
        "OR training_completed_on >= training_start_date",
    ),
    (
        "ck_program_enrollments_agreement_dates_ordered",
        "agreement_expiry_date IS NULL OR agreement_effective_date IS NULL "
        "OR agreement_expiry_date > agreement_effective_date",
    ),
)
_PROGRAM_CHECKS = (
    (
        "ck_recruitment_programs_funding_complete",
        "(company_funding_type IS NULL) = (company_funding_value IS NULL)",
    ),
    (
        "ck_recruitment_programs_funding_value_valid",
        "company_funding_value IS NULL OR (company_funding_value > 0 AND "
        "(company_funding_type <> 'PERCENTAGE' OR company_funding_value <= 100))",
    ),
)


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*_ENUMS[name], name=name, create_type=False)


def _existing_enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(name=name, create_type=False)


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
    # New enum values must be committed before any statement can use them.
    with op.get_context().autocommit_block():
        for type_name, value in _ADDED_VALUES:
            op.execute(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{value}'")

    bind = op.get_bind()
    for name in _ENUMS:
        _enum(name).create(bind, checkfirst=True)

    # --- recruitment_programs ---
    op.add_column("recruitment_programs", sa.Column("company_funding_type", _enum("program_funding_type"), nullable=True))
    op.add_column("recruitment_programs", sa.Column("company_funding_value", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "recruitment_programs",
        sa.Column("funding_requires_independent_approval", sa.Boolean(), server_default="true", nullable=False),
    )
    op.add_column(
        "recruitment_programs",
        sa.Column(
            "affiliation_verification_method",
            _existing_enum("affiliation_verification_method"),
            server_default="NONE",
            nullable=False,
        ),
    )
    for name, condition in _PROGRAM_CHECKS:
        op.create_check_constraint(name, "recruitment_programs", condition)

    # --- program_enrollments ---
    t = "program_enrollments"
    op.add_column(t, sa.Column("fee_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column(t, sa.Column("fee_currency", sa.String(length=3), nullable=True))
    op.add_column(t, sa.Column("fee_due_date", sa.Date(), nullable=True))
    op.add_column(t, sa.Column("company_contribution_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column(t, sa.Column("candidate_payable_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column(t, sa.Column("funding_terms_accepted_revision", sa.Integer(), nullable=True))
    op.add_column(t, sa.Column("funding_terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(t, sa.Column("training_enrolled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(t, sa.Column("training_completed_on", sa.Date(), nullable=True))
    op.add_column(t, sa.Column("training_outcome_note", sa.Text(), nullable=True))
    op.add_column(
        t,
        sa.Column(
            "employment_review_status",
            _enum("program_employment_review_status"),
            server_default="NOT_STARTED",
            nullable=False,
        ),
    )
    op.add_column(t, sa.Column("employment_role_title", sa.String(length=255), nullable=True))
    op.add_column(t, sa.Column("employment_department_id", sa.UUID(), nullable=True))
    op.add_column(t, sa.Column("employment_proposed_start_date", sa.Date(), nullable=True))
    op.add_column(t, sa.Column("employment_decided_by_user_id", sa.UUID(), nullable=True))
    op.add_column(t, sa.Column("employment_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(t, sa.Column("employment_decision_note", sa.Text(), nullable=True))
    op.add_column(t, sa.Column("employee_id", sa.UUID(), nullable=True))
    op.add_column(t, sa.Column("agreement_version", sa.String(length=100), nullable=True))
    op.add_column(t, sa.Column("agreement_terms_snapshot", postgresql.JSONB(), nullable=True))
    op.add_column(t, sa.Column("agreement_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(t, sa.Column("agreement_effective_date", sa.Date(), nullable=True))
    op.add_column(t, sa.Column("agreement_expiry_date", sa.Date(), nullable=True))
    op.add_column(t, sa.Column("agreement_status_note", sa.Text(), nullable=True))
    op.add_column(t, sa.Column("bond_applies_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f(f"fk_{t}_employment_department_id_departments"), t, "departments",
        ["employment_department_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f(f"fk_{t}_employment_decided_by_user_id_users"), t, "users",
        ["employment_decided_by_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f(f"fk_{t}_employee_id_employees"), t, "employees", ["employee_id"], ["id"], ondelete="SET NULL",
    )
    for name, condition in _ENROLLMENT_CHECKS:
        op.create_check_constraint(name, t, condition)

    # --- campus_affiliation_verifications: program context ---
    t = "campus_affiliation_verifications"
    op.add_column(t, sa.Column("program_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f(f"fk_{t}_program_id_recruitment_programs"), t, "recruitment_programs",
        ["program_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index(op.f(f"ix_{t}_program_id"), t, ["program_id"])
    op.alter_column(t, "campus_drive_id", existing_type=sa.UUID(), nullable=True)
    op.alter_column(t, "institution_id", existing_type=sa.UUID(), nullable=True)
    op.create_check_constraint(
        "ck_campus_affiliation_verifications_one_context", t,
        "(campus_drive_id IS NULL) <> (program_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_campus_affiliation_verifications_drive_institution", t,
        "campus_drive_id IS NULL OR institution_id IS NOT NULL",
    )

    # --- assessment_results.pass_score_applied ---
    op.add_column("assessment_results", sa.Column("pass_score_applied", sa.Integer(), nullable=True))
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute(
        """
        UPDATE assessment_results r SET pass_score_applied = a.pass_score
        FROM assessment_invitations i JOIN assessments a ON a.id = i.assessment_id
        WHERE i.id = r.invitation_id
        """
    )
    op.execute("SET LOCAL app.bypass_rls = 'off'")

    # --- program_funding_revisions ---
    t = "program_funding_revisions"
    op.create_table(
        t,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("enrollment_id", sa.UUID(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("kind", _enum("program_funding_revision_kind"), nullable=False),
        sa.Column("status", _enum("program_funding_revision_status"), server_default="PROPOSED", nullable=False),
        sa.Column("fee_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("funding_type", _enum("program_funding_type"), nullable=False),
        sa.Column("funding_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("company_contribution_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("candidate_payable_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("proposed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("decided_by_user_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        *_timestamps(),
        _fk(t, "organization_id", "organizations", "RESTRICT"),
        _fk(t, "enrollment_id", "program_enrollments", "RESTRICT"),
        sa.ForeignKeyConstraint(
            ["proposed_by_user_id"], ["users.id"], name=op.f(f"fk_{t}_proposed_by_user_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"], ["users.id"], name=op.f(f"fk_{t}_decided_by_user_id_users"), ondelete="SET NULL"
        ),
        sa.CheckConstraint("fee_amount >= 0", name=op.f(f"ck_{t}_fee_non_negative")),
        sa.CheckConstraint(
            "funding_value > 0 AND (funding_type <> 'PERCENTAGE' OR funding_value <= 100)",
            name=op.f(f"ck_{t}_value_valid"),
        ),
        sa.CheckConstraint(
            "company_contribution_amount >= 0 AND company_contribution_amount <= fee_amount",
            name=op.f(f"ck_{t}_contribution_within_fee"),
        ),
        sa.CheckConstraint(
            "candidate_payable_amount = fee_amount - company_contribution_amount",
            name=op.f(f"ck_{t}_payable_consistent"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
    )
    op.create_index(op.f(f"ix_{t}_organization_id"), t, ["organization_id"])
    op.create_index(op.f(f"ix_{t}_enrollment_id"), t, ["enrollment_id"])
    op.create_index("uq_program_funding_revisions_enrollment_number", t, ["enrollment_id", "revision_number"], unique=True)
    op.create_index(
        "uq_program_funding_revisions_one_proposed", t, ["enrollment_id"], unique=True,
        postgresql_where=sa.text("status = 'PROPOSED'"),
    )
    enable_tenant_rls(op, t)

    # --- training_milestones ---
    t = "training_milestones"
    op.create_table(
        t,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("enrollment_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("status", _enum("training_milestone_status"), server_default="PENDING", nullable=False),
        sa.Column("completed_on", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
        *_timestamps(),
        _fk(t, "organization_id", "organizations", "RESTRICT"),
        _fk(t, "enrollment_id", "program_enrollments", "RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name=op.f(f"fk_{t}_created_by_user_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], name=op.f(f"fk_{t}_updated_by_user_id_users"), ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETED') = (completed_on IS NOT NULL)", name=op.f(f"ck_{t}_completion_date")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
    )
    op.create_index(op.f(f"ix_{t}_organization_id"), t, ["organization_id"])
    op.create_index(op.f(f"ix_{t}_enrollment_id"), t, ["enrollment_id"])
    enable_tenant_rls(op, t)

    # --- application_interviews ---
    t = "application_interviews"
    op.create_table(
        t,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("stage", sa.String(length=150), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("interviewer_user_id", sa.UUID(), nullable=True),
        sa.Column("status", _enum("interview_status"), server_default="SCHEDULED", nullable=False),
        sa.Column("outcome", _enum("interview_outcome"), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("scheduled_by_user_id", sa.UUID(), nullable=True),
        sa.Column("recorded_by_user_id", sa.UUID(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        _fk(t, "organization_id", "organizations", "RESTRICT"),
        _fk(t, "application_id", "applications", "RESTRICT"),
        sa.ForeignKeyConstraint(
            ["interviewer_user_id"], ["users.id"], name=op.f(f"fk_{t}_interviewer_user_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["scheduled_by_user_id"], ["users.id"], name=op.f(f"fk_{t}_scheduled_by_user_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"], ["users.id"], name=op.f(f"fk_{t}_recorded_by_user_id_users"), ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETED') = (outcome IS NOT NULL)", name=op.f(f"ck_{t}_outcome_when_completed")
        ),
        sa.CheckConstraint("rating IS NULL OR (rating >= 1 AND rating <= 5)", name=op.f(f"ck_{t}_rating_range")),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
    )
    op.create_index(op.f(f"ix_{t}_organization_id"), t, ["organization_id"])
    op.create_index("ix_application_interviews_application_id", t, ["application_id"])
    op.create_index(op.f(f"ix_{t}_interviewer_user_id"), t, ["interviewer_user_id"])
    op.create_index(
        "uq_application_interviews_one_scheduled_per_stage", t, ["application_id", "stage"], unique=True,
        postgresql_where=sa.text("status = 'SCHEDULED'"),
    )
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

    for table in ("application_interviews", "training_milestones", "program_funding_revisions"):
        disable_tenant_rls(op, table)
        op.drop_table(table)

    op.drop_column("assessment_results", "pass_score_applied")

    # Map rows using values the old schema doesn't know back to the closest
    # pre-existing value, and drop program verifications (no place for them).
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute("UPDATE program_enrollments SET scholarship_status = 'PENDING' WHERE scholarship_status = 'PROPOSED'")
    op.execute("UPDATE program_enrollments SET training_status = 'DISCONTINUED' WHERE training_status = 'WITHDRAWN'")
    op.execute("UPDATE program_enrollments SET agreement_status = 'SIGNED' WHERE agreement_status = 'EXPIRED'")
    op.execute("UPDATE program_enrollments SET agreement_status = 'VOID' WHERE agreement_status = 'TERMINATED'")
    op.execute("DELETE FROM campus_affiliation_verifications WHERE program_id IS NOT NULL")
    # The pre-upgrade code can't read this notification type.
    op.execute("DELETE FROM notifications WHERE type::text = 'INTERVIEW_ASSIGNED'")
    op.execute(
        "UPDATE campus_affiliation_verifications SET method = 'MANUAL' "
        "WHERE method IN ('STUDENT_ID_DOCUMENT', 'COORDINATOR_CONFIRMATION')"
    )
    op.execute(
        "UPDATE campus_drives SET affiliation_verification_method = 'MANUAL' "
        "WHERE affiliation_verification_method IN ('STUDENT_ID_DOCUMENT', 'COORDINATOR_CONFIRMATION')"
    )
    op.execute("SET LOCAL app.bypass_rls = 'off'")

    t = "campus_affiliation_verifications"
    op.drop_constraint("ck_campus_affiliation_verifications_drive_institution", t, type_="check")
    op.drop_constraint("ck_campus_affiliation_verifications_one_context", t, type_="check")
    op.alter_column(t, "institution_id", existing_type=sa.UUID(), nullable=False)
    op.alter_column(t, "campus_drive_id", existing_type=sa.UUID(), nullable=False)
    op.drop_index(op.f(f"ix_{t}_program_id"), table_name=t)
    op.drop_constraint(op.f(f"fk_{t}_program_id_recruitment_programs"), t, type_="foreignkey")
    op.drop_column(t, "program_id")

    t = "program_enrollments"
    for name, _ in _ENROLLMENT_CHECKS:
        op.drop_constraint(name, t, type_="check")
    op.drop_constraint(op.f(f"fk_{t}_employee_id_employees"), t, type_="foreignkey")
    op.drop_constraint(op.f(f"fk_{t}_employment_decided_by_user_id_users"), t, type_="foreignkey")
    op.drop_constraint(op.f(f"fk_{t}_employment_department_id_departments"), t, type_="foreignkey")
    for column in reversed(_ENROLLMENT_COLUMNS):
        op.drop_column(t, column)

    for name, _ in _PROGRAM_CHECKS:
        op.drop_constraint(name, "recruitment_programs", type_="check")
    for column in reversed(_PROGRAM_COLUMNS):
        op.drop_column("recruitment_programs", column)

    for name in reversed(list(_ENUMS)):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
