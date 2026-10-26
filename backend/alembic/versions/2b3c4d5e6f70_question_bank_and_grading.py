"""Question bank, assessment draft/publish, new question types, manual grading

Revision ID: 2b3c4d5e6f70
Revises: 1a2b3c4d5e6f

* `question_type` gains TRUE_FALSE, SHORT_ANSWER, DESCRIPTIVE, CODING.
* `assessments.status` DRAFT/PUBLISHED — every existing assessment becomes
  PUBLISHED, i.e. exactly as usable as before.
* Question bank (docs/assessment.md § 10): `question_categories`
  (hierarchical subject/topic), `question_tags`, `question_bank_items`,
  `question_bank_item_tags`, immutable `question_bank_item_versions` and
  their private `question_bank_answer_keys`.
* `questions.negative_points` (default 0) and
  `questions.source_bank_version_id` (the bank version a question was
  snapshotted from; NULL for questions written directly into a test).
* `question_answer_keys`: private grading data for text-answer questions.
* `candidate_answers`: free-text answer + per-answer grading (marks,
  method, grader, time, private comment).
* `assessment_results.passed` becomes nullable and `grading_status`
  (FINAL / PENDING_MANUAL_REVIEW) is added — existing results are FINAL.
* Permissions: `question_bank.read`, `question_bank.manage`,
  `assessment.grade`.

Downgrade removes everything added here. PostgreSQL cannot drop enum
values, so the four new `question_type` values remain on the type
(unused); downgrade refuses to run while any question still uses one, and
while any result is still awaiting manual grading (its `passed` is NULL).
"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.rls import disable_tenant_rls, enable_indirect_tenant_rls, enable_tenant_rls

# revision identifiers, used by Alembic.
revision: str = "2b3c4d5e6f70"
down_revision: str | None = "1a2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_QUESTION_TYPES = ("TRUE_FALSE", "SHORT_ANSWER", "DESCRIPTIVE", "CODING")
_CURRENT_ORG = "NULLIF(current_setting('app.current_org_id', true), '')::uuid"

_PERMISSIONS = (
    ("question_bank.read", "View the organization's question bank."),
    ("question_bank.manage", "Create, edit, publish and archive question-bank questions, categories and tags."),
    ("assessment.grade", "Manually grade candidates' descriptive/coding/short answers."),
)
_ROLE_PERMISSIONS = {
    "ORG_ADMIN": ("question_bank.read", "question_bank.manage", "assessment.grade"),
    "RECRUITER": ("question_bank.read", "question_bank.manage", "assessment.grade"),
    "HIRING_MANAGER": ("question_bank.read", "assessment.grade"),
}


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id"], ["organizations.id"],
        name=op.f(f"fk_{table}_organization_id_organizations"), ondelete="RESTRICT",
    )


def _user_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["users.id"], name=op.f(f"fk_{table}_{column}_users"), ondelete="SET NULL",
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
    for value in _NEW_QUESTION_TYPES:
        op.execute(f"ALTER TYPE question_type ADD VALUE IF NOT EXISTS '{value}'")

    assessment_status = postgresql.ENUM("DRAFT", "PUBLISHED", name="assessment_status", create_type=False)
    grading_method = postgresql.ENUM("AUTO", "MANUAL", name="grading_method", create_type=False)
    result_grading_status = postgresql.ENUM(
        "FINAL", "PENDING_MANUAL_REVIEW", name="result_grading_status", create_type=False
    )
    difficulty = postgresql.ENUM("EASY", "MEDIUM", "HARD", name="question_difficulty", create_type=False)
    bank_status = postgresql.ENUM(
        "DRAFT", "PUBLISHED", "ARCHIVED", name="question_bank_status", create_type=False
    )
    question_type = postgresql.ENUM(name="question_type", create_type=False)
    for enum in (assessment_status, grading_method, result_grading_status, difficulty, bank_status):
        enum.create(bind, checkfirst=True)

    # --- assessments.status ---
    op.add_column(
        "assessments",
        sa.Column("status", assessment_status, server_default="PUBLISHED", nullable=False),
    )

    # --- question_categories ---
    op.create_table(
        "question_categories",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("normalized_name", sa.String(length=150), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        *_timestamps(),
        _org_fk("question_categories"),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["question_categories.id"],
            name=op.f("fk_question_categories_parent_id_question_categories"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_question_categories")),
    )
    op.create_index(op.f("ix_question_categories_organization_id"), "question_categories", ["organization_id"])
    op.create_index(op.f("ix_question_categories_parent_id"), "question_categories", ["parent_id"])
    op.create_index(
        "uq_question_categories_root_name", "question_categories",
        ["organization_id", "normalized_name"], unique=True, postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "uq_question_categories_child_name", "question_categories",
        ["organization_id", "parent_id", "normalized_name"], unique=True,
        postgresql_where=sa.text("parent_id IS NOT NULL"),
    )

    # --- question_tags ---
    op.create_table(
        "question_tags",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("normalized_name", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        _org_fk("question_tags"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_question_tags")),
    )
    op.create_index(op.f("ix_question_tags_organization_id"), "question_tags", ["organization_id"])
    op.create_index(
        "uq_question_tags_org_normalized_name", "question_tags",
        ["organization_id", "normalized_name"], unique=True,
    )

    # --- question_bank_items ---
    op.create_table(
        "question_bank_items",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column("difficulty", difficulty, server_default="MEDIUM", nullable=False),
        sa.Column("status", bank_status, server_default="DRAFT", nullable=False),
        sa.Column("current_version_number", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        *_timestamps(),
        _org_fk("question_bank_items"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["question_categories.id"],
            name=op.f("fk_question_bank_items_category_id_question_categories"), ondelete="RESTRICT",
        ),
        _user_fk("question_bank_items", "created_by"),
        _user_fk("question_bank_items", "updated_by"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_question_bank_items")),
    )
    op.create_index(op.f("ix_question_bank_items_organization_id"), "question_bank_items", ["organization_id"])
    op.create_index(op.f("ix_question_bank_items_category_id"), "question_bank_items", ["category_id"])
    op.create_index(
        "ix_question_bank_items_org_status", "question_bank_items", ["organization_id", "status"]
    )

    # --- question_bank_item_tags ---
    op.create_table(
        "question_bank_item_tags",
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("tag_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"], ["question_bank_items.id"],
            name=op.f("fk_question_bank_item_tags_item_id_question_bank_items"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["question_tags.id"],
            name=op.f("fk_question_bank_item_tags_tag_id_question_tags"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", "tag_id", name=op.f("pk_question_bank_item_tags")),
    )
    op.create_index(op.f("ix_question_bank_item_tags_tag_id"), "question_bank_item_tags", ["tag_id"])

    # --- question_bank_item_versions ---
    op.create_table(
        "question_bank_item_versions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("type", question_type, nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("negative_points", sa.Integer(), server_default="0", nullable=False),
        sa.Column("options", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        _org_fk("question_bank_item_versions"),
        sa.ForeignKeyConstraint(
            ["item_id"], ["question_bank_items.id"],
            name=op.f("fk_question_bank_item_versions_item_id_question_bank_items"), ondelete="RESTRICT",
        ),
        _user_fk("question_bank_item_versions", "created_by"),
        sa.CheckConstraint("points >= 1", name=op.f("ck_question_bank_item_versions_points_positive")),
        sa.CheckConstraint(
            "negative_points >= 0 AND negative_points <= points",
            name=op.f("ck_question_bank_item_versions_negative_points_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_question_bank_item_versions")),
    )
    op.create_index(
        op.f("ix_question_bank_item_versions_organization_id"), "question_bank_item_versions", ["organization_id"]
    )
    op.create_index(
        "uq_question_bank_item_versions_item_version", "question_bank_item_versions",
        ["item_id", "version_number"], unique=True,
    )

    # --- question_bank_answer_keys ---
    op.create_table(
        "question_bank_answer_keys",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("correct_option_keys", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("accepted_answers", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("case_sensitive", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("grading_notes", sa.Text(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        _org_fk("question_bank_answer_keys"),
        sa.ForeignKeyConstraint(
            ["version_id"], ["question_bank_item_versions.id"],
            name=op.f("fk_question_bank_answer_keys_version_id_question_bank_item_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_question_bank_answer_keys")),
        sa.UniqueConstraint("version_id", name=op.f("uq_question_bank_answer_keys_version_id")),
    )
    op.create_index(
        op.f("ix_question_bank_answer_keys_organization_id"), "question_bank_answer_keys", ["organization_id"]
    )

    for table in (
        "question_categories",
        "question_tags",
        "question_bank_items",
        "question_bank_item_versions",
        "question_bank_answer_keys",
    ):
        enable_tenant_rls(op, table)
    enable_indirect_tenant_rls(
        op, "question_bank_item_tags",
        exists_subquery=(
            "EXISTS (SELECT 1 FROM question_bank_items qbi "
            "WHERE qbi.id = question_bank_item_tags.item_id "
            f"AND qbi.organization_id = {_CURRENT_ORG})"
        ),
    )

    # --- questions: negative marking + bank provenance ---
    op.add_column(
        "questions", sa.Column("negative_points", sa.Integer(), server_default="0", nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_questions_negative_points_range"), "questions",
        "negative_points >= 0 AND negative_points <= points",
    )
    op.add_column("questions", sa.Column("source_bank_version_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_questions_source_bank_version_id_question_bank_item_versions"),
        "questions", "question_bank_item_versions", ["source_bank_version_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(op.f("ix_questions_source_bank_version_id"), "questions", ["source_bank_version_id"])

    # --- question_answer_keys ---
    op.create_table(
        "question_answer_keys",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("question_id", sa.UUID(), nullable=False),
        sa.Column("accepted_answers", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("case_sensitive", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("grading_notes", sa.Text(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["question_id"], ["questions.id"],
            name=op.f("fk_question_answer_keys_question_id_questions"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_question_answer_keys")),
        sa.UniqueConstraint("question_id", name=op.f("uq_question_answer_keys_question_id")),
    )
    enable_indirect_tenant_rls(
        op, "question_answer_keys",
        exists_subquery=(
            "EXISTS (SELECT 1 FROM questions q JOIN assessments a ON a.id = q.assessment_id "
            "WHERE q.id = question_answer_keys.question_id "
            f"AND a.organization_id = {_CURRENT_ORG})"
        ),
    )

    # --- candidate_answers: text answers + per-answer grading ---
    op.add_column("candidate_answers", sa.Column("text_answer", sa.Text(), nullable=True))
    op.add_column("candidate_answers", sa.Column("is_correct", sa.Boolean(), nullable=True))
    op.add_column("candidate_answers", sa.Column("awarded_points", sa.Integer(), nullable=True))
    op.add_column("candidate_answers", sa.Column("grading_method", grading_method, nullable=True))
    op.add_column("candidate_answers", sa.Column("graded_by_user_id", sa.UUID(), nullable=True))
    op.add_column("candidate_answers", sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("candidate_answers", sa.Column("grader_comment", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_candidate_answers_graded_by_user_id_users"),
        "candidate_answers", "users", ["graded_by_user_id"], ["id"], ondelete="SET NULL",
    )

    # --- assessment_results: manual-review state ---
    op.alter_column("assessment_results", "passed", existing_type=sa.Boolean(), nullable=True)
    op.add_column(
        "assessment_results",
        sa.Column("grading_status", result_grading_status, server_default="FINAL", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_assessment_results_passed_when_final"), "assessment_results",
        "grading_status <> 'FINAL' OR passed IS NOT NULL",
    )

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

    op.execute("SET LOCAL app.bypass_rls = 'on'")
    in_use = bind.execute(
        sa.text("SELECT count(*) FROM questions WHERE type::text = ANY(:types)"),
        {"types": list(_NEW_QUESTION_TYPES)},
    ).scalar_one()
    pending = bind.execute(
        sa.text("SELECT count(*) FROM assessment_results WHERE passed IS NULL")
    ).scalar_one()
    op.execute("SET LOCAL app.bypass_rls = 'off'")
    if in_use or pending:
        raise RuntimeError(
            "Cannot downgrade: assessments still use the new question types or results are "
            "awaiting manual grading. Resolve those first; no data is removed automatically."
        )

    op.drop_constraint(op.f("ck_assessment_results_passed_when_final"), "assessment_results", type_="check")
    op.drop_column("assessment_results", "grading_status")
    op.alter_column("assessment_results", "passed", existing_type=sa.Boolean(), nullable=False)

    op.drop_constraint(op.f("fk_candidate_answers_graded_by_user_id_users"), "candidate_answers", type_="foreignkey")
    for column in (
        "grader_comment", "graded_at", "graded_by_user_id", "grading_method",
        "awarded_points", "is_correct", "text_answer",
    ):
        op.drop_column("candidate_answers", column)

    disable_tenant_rls(op, "question_answer_keys")
    op.drop_table("question_answer_keys")

    op.drop_index(op.f("ix_questions_source_bank_version_id"), table_name="questions")
    op.drop_constraint(
        op.f("fk_questions_source_bank_version_id_question_bank_item_versions"), "questions", type_="foreignkey"
    )
    op.drop_column("questions", "source_bank_version_id")
    op.drop_constraint(op.f("ck_questions_negative_points_range"), "questions", type_="check")
    op.drop_column("questions", "negative_points")

    for table in (
        "question_bank_item_tags",
        "question_bank_answer_keys",
        "question_bank_item_versions",
        "question_bank_items",
        "question_tags",
        "question_categories",
    ):
        disable_tenant_rls(op, table)
        op.drop_table(table)

    op.drop_column("assessments", "status")
    for name in (
        "question_bank_status", "question_difficulty", "result_grading_status",
        "grading_method", "assessment_status",
    ):
        sa.Enum(name=name).drop(bind, checkfirst=True)
