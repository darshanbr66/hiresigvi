"""Organization-wide starting values for new recruitment programs
(docs/programs.md § 1a).

One row per organization, edited by authorized Admin/HR in the portal. A
new program copies these values when it is created; after that the
program's own configuration is what counts, so changing a default never
rewrites an existing program, a candidate's accepted terms, a funding
decision or a payment record.

The first row is created on first use from the initial values in
app/services/program_settings_service.py (the team's proposed starting
points, not confirmed policy).
"""

import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.program import FundingType


class OrganizationProgramSettings(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "organization_program_settings"
    __table_args__ = (
        CheckConstraint(
            "cardinality(training_duration_options) = 0 OR "
            "(1 <= ALL(training_duration_options) AND 60 >= ALL(training_duration_options))",
            name="ck_organization_program_settings_training_durations_range",
        ),
        CheckConstraint(
            "training_fee_amount IS NULL OR (training_fee_amount >= 0 AND currency IS NOT NULL)",
            name="ck_organization_program_settings_fee_valid",
        ),
        CheckConstraint(
            "(company_funding_type IS NULL) = (company_funding_value IS NULL)",
            name="ck_organization_program_settings_funding_complete",
        ),
        CheckConstraint(
            "company_funding_value IS NULL OR (company_funding_value >= 0 AND "
            "(company_funding_type <> 'PERCENTAGE' OR company_funding_value <= 100))",
            name="ck_organization_program_settings_funding_value_valid",
        ),
        CheckConstraint(
            "internship_duration_months IS NULL OR internship_duration_months BETWEEN 1 AND 60",
            name="ck_organization_program_settings_internship_duration_range",
        ),
        CheckConstraint(
            "bond_duration_months IS NULL OR bond_duration_months BETWEEN 1 AND 120",
            name="ck_organization_program_settings_bond_duration_range",
        ),
        # One settings row per organization. A named constraint (plus the
        # plain index below), matching migration 6f7081920314 exactly.
        UniqueConstraint("organization_id", name="uq_organization_program_settings_organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    training_duration_options: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, default=list, server_default="{}"
    )
    training_fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    company_funding_type: Mapped[FundingType | None] = mapped_column(
        Enum(FundingType, name="program_funding_type", native_enum=True, create_type=False),
        nullable=True,
    )
    company_funding_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    funding_requires_independent_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    internship_duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bond_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    bond_duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eligible_education_levels: Mapped[list[str]] = mapped_column(
        ARRAY(String(30)), nullable=False, default=list, server_default="{}"
    )
    study_year_rules: Mapped[dict[str, list[int]]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
