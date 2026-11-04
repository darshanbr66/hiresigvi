"""Organization-wide default program settings (docs/programs.md § 1a).

`program.read` may view them (they explain what a new program starts with);
only `program.settings.manage` (ORG_ADMIN by default) may change them.
`organization_id` always comes from the caller.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import granted_permission_codes, require_permission
from app.db.session import get_db
from app.models.program_settings import OrganizationProgramSettings
from app.models.user import User
from app.schemas.program_settings import ProgramSettingsResponse, ProgramSettingsUpdateRequest
from app.services import program_settings_service

router = APIRouter(prefix="/program-settings", tags=["recruiter-program-settings"])

_MANAGE = "program.settings.manage"


async def _response(
    db: AsyncSession, settings: OrganizationProgramSettings, user: User
) -> ProgramSettingsResponse:
    split = program_settings_service.preview(settings)
    updated_by_name = (
        await db.scalar(select(User.full_name).where(User.id == settings.updated_by_user_id))
        if settings.updated_by_user_id
        else None
    )
    codes = await granted_permission_codes(db, user)
    response = ProgramSettingsResponse.model_validate(settings)
    response.updated_by_name = updated_by_name
    response.preview_company_contribution = split.company_contribution if split else None
    response.preview_candidate_payable = split.candidate_payable if split else None
    response.can_edit = _MANAGE in codes
    return response


@router.get("", response_model=ProgramSettingsResponse)
async def get_program_settings(
    current_user: User = Depends(require_permission("program.read")),
    db: AsyncSession = Depends(get_db),
) -> ProgramSettingsResponse:
    assert current_user.organization_id is not None
    settings = await program_settings_service.get_settings(db, current_user.organization_id)
    return await _response(db, settings, current_user)


@router.put("", response_model=ProgramSettingsResponse)
async def update_program_settings(
    payload: ProgramSettingsUpdateRequest,
    current_user: User = Depends(require_permission(_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> ProgramSettingsResponse:
    settings = await program_settings_service.update_settings(db, actor=current_user, payload=payload)
    return await _response(db, settings, current_user)
