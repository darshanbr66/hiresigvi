from fastapi import APIRouter

from app.api.v1.public.ai import router as ai_router
from app.api.v1.public.applications import router as applications_router
from app.api.v1.public.assessments import router as assessments_router
from app.api.v1.public.campus_drives import router as campus_drives_router
from app.api.v1.public.candidate_intake import router as candidate_intake_router
from app.api.v1.public.jobs import router as jobs_router
from app.api.v1.public.programs import listing_router as program_listing_router
from app.api.v1.public.programs import router as programs_router

router = APIRouter(prefix="/public")
router.include_router(jobs_router)
router.include_router(candidate_intake_router)
router.include_router(ai_router)
router.include_router(applications_router)
router.include_router(assessments_router)
router.include_router(campus_drives_router)
router.include_router(program_listing_router)
router.include_router(programs_router)
