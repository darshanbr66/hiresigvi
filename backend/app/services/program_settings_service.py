"""Organization-wide default program settings (docs/programs.md § 1a).

`INITIAL_PROGRAM_DEFAULTS` are the team's *proposed* starting values. They
seed an organization's settings row the first time it is read, and from
then on authorized Admin/HR change them in the portal. A program copies the
defaults when it is created; editing a default afterwards never changes an
existing program, a candidate's accepted terms or any money record.
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import EducationLevel
from app.models.program import FundingType
from app.models.program_settings import OrganizationProgramSettings
from app.models.user import User
from app.schemas.program_settings import ProgramSettingsUpdateRequest
from app.services import activity_service, program_finance

#: Proposed initial values (not confirmed policy): 6- or 12-month training,
#: a ₹50,000 fee with 50% company funding, a 6-month internship and a
#: 3-year bond for funded participants, open to second-year M.Tech and PhD
#: candidates.
INITIAL_PROGRAM_DEFAULTS: dict[str, Any] = {
    "training_duration_options": [6, 12],
    "training_fee_amount": Decimal("50000.00"),
    "currency": "INR",
    "company_funding_type": FundingType.PERCENTAGE,
    "company_funding_value": Decimal("50.00"),
    "funding_requires_independent_approval": True,
    "internship_duration_months": 6,
    "bond_required": True,
    "bond_duration_months": 36,
    "eligible_education_levels": [EducationLevel.MTECH.value, EducationLevel.PHD.value],
    "study_year_rules": {EducationLevel.MTECH.value: [2]},
}

_FIELDS = tuple(INITIAL_PROGRAM_DEFAULTS)


async def get_settings(db: AsyncSession, organization_id: uuid.UUID) -> OrganizationProgramSettings:
    """The organization's settings, created from the initial values on
    first use."""
    settings = await db.scalar(
        select(OrganizationProgramSettings).where(
            OrganizationProgramSettings.organization_id == organization_id
        )
    )
    if settings is not None:
        return settings
    settings = OrganizationProgramSettings(organization_id=organization_id, **INITIAL_PROGRAM_DEFAULTS)
    db.add(settings)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        # Created concurrently by another request.
        existing = await db.scalar(
            select(OrganizationProgramSettings).where(
                OrganizationProgramSettings.organization_id == organization_id
            )
        )
        assert existing is not None
        return existing
    return settings


def preview(settings: OrganizationProgramSettings) -> program_finance.FundingSplit | None:
    if settings.training_fee_amount is None or settings.company_funding_type is None:
        return None
    return program_finance.split_fee(
        settings.training_fee_amount, settings.company_funding_type, settings.company_funding_value
    )


def _describe(value: Any) -> str:
    if value is None or value == [] or value == {}:
        return "none"
    if isinstance(value, Decimal):
        return f"{value:f}"
    if isinstance(value, FundingType):
        return value.value
    return str(value)


async def update_settings(
    db: AsyncSession, *, actor: User, payload: ProgramSettingsUpdateRequest
) -> OrganizationProgramSettings:
    assert actor.organization_id is not None
    settings = await get_settings(db, actor.organization_id)
    values = payload.model_dump()
    values["eligible_education_levels"] = [level.value for level in payload.eligible_education_levels]
    if values["company_funding_value"] is not None:
        values["company_funding_value"] = program_finance.money(values["company_funding_value"])
    if values["training_fee_amount"] is not None:
        values["training_fee_amount"] = program_finance.money(values["training_fee_amount"])
    if not values["bond_required"]:
        values["bond_duration_months"] = None

    changes = []
    for field in _FIELDS:
        before = getattr(settings, field)
        if before != values[field]:
            changes.append(f"{field}: {_describe(before)} -> {_describe(values[field])}")
            setattr(settings, field, values[field])
    if not changes:
        return settings
    settings.updated_by_user_id = actor.id
    await db.flush()
    await db.refresh(settings)
    await activity_service.record_activity(
        db,
        organization_id=actor.organization_id,
        actor=actor,
        action="PROGRAM_SETTINGS_UPDATED",
        entity_type="program_settings",
        entity_id=settings.id,
        entity_label="Default program settings",
        description=(
            "Default program settings changed (new programs only; existing programs and "
            "candidate terms are unchanged): " + "; ".join(changes) + "."
        ),
    )
    return settings
