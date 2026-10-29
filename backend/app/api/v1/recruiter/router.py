from fastapi import APIRouter

from app.api.v1.recruiter.activities import router as activities_router
from app.api.v1.recruiter.admin_messages import router as admin_messages_router
from app.api.v1.recruiter.applications import router as applications_router
from app.api.v1.recruiter.assessments import router as assessments_router
from app.api.v1.recruiter.auth import router as auth_router
from app.api.v1.recruiter.calendar import router as calendar_router
from app.api.v1.recruiter.campus_drives import router as campus_drives_router
from app.api.v1.recruiter.candidates import router as candidates_router
from app.api.v1.recruiter.departments import router as departments_router
from app.api.v1.recruiter.email_templates import router as email_templates_router
from app.api.v1.recruiter.employees import router as employees_router
from app.api.v1.recruiter.general_email import router as general_email_router
from app.api.v1.recruiter.institutions import router as institutions_router
from app.api.v1.recruiter.internal_ai import router as internal_ai_router
from app.api.v1.recruiter.interviews import router as interviews_router
from app.api.v1.recruiter.jobs import router as jobs_router
from app.api.v1.recruiter.notes import router as notes_router
from app.api.v1.recruiter.notes import workspace_router as notes_workspace_router
from app.api.v1.recruiter.notifications import router as notifications_router
from app.api.v1.recruiter.programs import router as programs_router
from app.api.v1.recruiter.question_bank import router as question_bank_router
from app.api.v1.recruiter.reports import router as reports_router
from app.api.v1.recruiter.screening import router as screening_router
from app.api.v1.recruiter.users import router as users_router

router = APIRouter(prefix="/recruiter")
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(jobs_router)
router.include_router(candidates_router)
router.include_router(applications_router)
router.include_router(interviews_router)
router.include_router(reports_router)
router.include_router(screening_router)
router.include_router(assessments_router)
router.include_router(campus_drives_router)
router.include_router(institutions_router)
router.include_router(question_bank_router)
router.include_router(programs_router)
router.include_router(notes_router)
router.include_router(notes_workspace_router)
router.include_router(calendar_router)
router.include_router(activities_router)
router.include_router(departments_router)
router.include_router(employees_router)
router.include_router(email_templates_router)
router.include_router(general_email_router)
router.include_router(notifications_router)
router.include_router(internal_ai_router)
router.include_router(admin_messages_router)
