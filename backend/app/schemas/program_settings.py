import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.candidate import EducationLevel
from app.models.program import MAX_DURATION_MONTHS, MIN_DURATION_MONTHS, FundingType
from app.schemas.program import _currency, check_durations, check_study_year_rules


class ProgramSettingsUpdateRequest(BaseModel):
    """The whole settings document (PUT semantics): every value is what a
    new program starts with. Empty/None = "no default" for that value."""

    training_duration_options: list[int] = Field(default_factory=list, max_length=12)
    training_fee_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = None
    company_funding_type: FundingType | None = None
    company_funding_value: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    funding_requires_independent_approval: bool = True
    internship_duration_months: int | None = Field(
        default=None, ge=MIN_DURATION_MONTHS, le=MAX_DURATION_MONTHS
    )
    bond_required: bool = True
    bond_duration_months: int | None = Field(default=None, ge=1, le=120)
    eligible_education_levels: list[EducationLevel] = Field(default_factory=list, max_length=10)
    study_year_rules: dict[str, list[int]] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, value: str | None) -> str | None:
        return _currency(value)

    @field_validator("training_duration_options")
    @classmethod
    def _check_durations(cls, value: list[int]) -> list[int]:
        return check_durations(value) or []

    @field_validator("study_year_rules")
    @classmethod
    def _check_rules(cls, value: dict[str, list[int]]) -> dict[str, list[int]]:
        return check_study_year_rules(value) or {}

    @model_validator(mode="after")
    def _consistent(self) -> "ProgramSettingsUpdateRequest":
        if self.training_fee_amount is not None and not self.currency:
            raise ValueError("Enter the currency of the default training fee.")
        if (self.company_funding_type is None) != (self.company_funding_value is None):
            raise ValueError("Enter both the company funding basis and its value, or neither.")
        if self.company_funding_type == FundingType.PERCENTAGE and (
            self.company_funding_value is not None and self.company_funding_value > 100
        ):
            raise ValueError("A funding percentage must be between 0% and 100%.")
        if (
            self.company_funding_type == FundingType.FIXED_AMOUNT
            and self.company_funding_value is not None
            and self.training_fee_amount is not None
            and self.company_funding_value > self.training_fee_amount
        ):
            raise ValueError("Fixed company funding can't be more than the default training fee.")
        if self.bond_required and self.bond_duration_months is None:
            raise ValueError("Enter the default bond duration, or turn the bond off.")
        return self


class ProgramSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    training_duration_options: list[int]
    training_fee_amount: Decimal | None
    currency: str | None
    company_funding_type: FundingType | None
    company_funding_value: Decimal | None
    funding_requires_independent_approval: bool
    internship_duration_months: int | None
    bond_required: bool
    bond_duration_months: int | None
    eligible_education_levels: list[EducationLevel]
    study_year_rules: dict[str, list[int]]
    updated_by_user_id: uuid.UUID | None
    updated_by_name: str | None = None
    updated_at: datetime
    #: The authoritative split of the default fee under the default funding
    #: (None when either isn't set).
    preview_company_contribution: Decimal | None = None
    preview_candidate_payable: Decimal | None = None
    #: Whether the viewer may edit (program.settings.manage).
    can_edit: bool = False
