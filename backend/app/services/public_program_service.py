"""Candidate-facing recruitment-program access (docs/programs.md § 3) — no
login required, same RLS-bootstrap pattern as
app/services/public_campus_drive_service.py: the program's tenant is
resolved via `rls_bypass` (its id is looked up before any org is known),
then tenant context is set for the rest of the request.

Applying to a program *is* the careers-site self-service flow
(public_application_service), applied to the program's own job: the same
email one-time code, unique email and mobile, returning-candidate rules,
one-application-per-job, reapply window, AI screening and welcome email.
On top of it, the program checks the candidate's declared education against
its eligibility criteria and records exactly which terms they accepted —
both before anything is stored, and the enrollment is created atomically
with the application.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.db.rls import rls_bypass, set_tenant_context
from app.integrations.storage import ResumeStorage
from app.models.application import Application
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.program import ProgramStatus, RecruitmentProgram
from app.schemas.candidate import PublicApplicantProfile
from app.schemas.program import ProgramEducationDeclaration
from app.services import program_service, public_application_service
from app.services.candidate_document_service import MarksCardUpload
from app.services.email_verification_service import CodeSent

_INVALID_MESSAGE = "This program could not be found."
_VISIBLE = (ProgramStatus.PUBLISHED, ProgramStatus.PAUSED, ProgramStatus.CLOSED)


async def get_program(db: AsyncSession, program_id: uuid.UUID) -> RecruitmentProgram:
    """A DRAFT or ARCHIVED program behaves exactly like an unknown id."""
    async with rls_bypass(db):
        result = await db.execute(
            select(RecruitmentProgram)
            .where(RecruitmentProgram.id == program_id)
            .options(
                selectinload(RecruitmentProgram.eligible_institutions),
                selectinload(RecruitmentProgram.assessment),
            )
        )
        program = result.scalar_one_or_none()
    if program is None or program.status not in _VISIBLE:
        raise NotFoundError(_INVALID_MESSAGE)
    await set_tenant_context(db, program.organization_id)
    return program


async def get_organization(db: AsyncSession, program: RecruitmentProgram) -> Organization:
    organization = await db.get(Organization, program.organization_id)
    if organization is None:  # FK RESTRICT makes this unreachable in practice
        raise NotFoundError(_INVALID_MESSAGE)
    return organization


async def list_published(db: AsyncSession, organization_id: uuid.UUID) -> list[RecruitmentProgram]:
    return await program_service.list_programs(db, organization_id, status=ProgramStatus.PUBLISHED)


def _require_accepting(program: RecruitmentProgram) -> None:
    accepting, reason = program_service.accepting_applications(program)
    if not accepting:
        raise ConflictError(reason or "Not accepting applications.", code="program_not_accepting")


async def has_already_applied(
    db: AsyncSession, *, program_id: uuid.UUID, email: str | None, phone: str | None
) -> bool:
    program = await get_program(db, program_id)
    _require_accepting(program)
    organization = await get_organization(db, program)
    return await public_application_service.has_already_applied(
        db, organization=organization, email=email, phone=phone, job_id=program.job_id, program=program
    )


async def request_email_code(db: AsyncSession, *, program_id: uuid.UUID, email: str) -> CodeSent:
    program = await get_program(db, program_id)
    _require_accepting(program)
    organization = await get_organization(db, program)
    return await public_application_service.request_applicant_code(
        db, organization=organization, email=email, job_id=program.job_id, program=program
    )


async def verify_email_code(
    db: AsyncSession, *, program_id: uuid.UUID, email: str, code: str
) -> public_application_service.VerifiedApplicant:
    program = await get_program(db, program_id)
    _require_accepting(program)
    organization = await get_organization(db, program)
    return await public_application_service.verify_applicant_email(
        db, organization=organization, email=email, code=code, job_id=program.job_id, program=program
    )


async def precheck_applicant(
    db: AsyncSession,
    *,
    program_id: uuid.UUID,
    email: str,
    verification_token: str,
    phone: str,
) -> None:
    program = await get_program(db, program_id)
    _require_accepting(program)
    organization = await get_organization(db, program)
    await public_application_service.precheck_applicant(
        db,
        organization=organization,
        job_id=program.job_id,
        email=email,
        verification_token=verification_token,
        phone=phone,
        program=program,
    )


@dataclass(frozen=True)
class ProgramSubmissionResult:
    submission: public_application_service.SubmissionResult
    organization: Organization
    program: RecruitmentProgram


async def apply_to_program(
    db: AsyncSession,
    storage: ResumeStorage,
    *,
    program_id: uuid.UUID,
    email: str,
    verification_token: str | None,
    profile: PublicApplicantProfile,
    declaration: ProgramEducationDeclaration,
    resume_filename: str,
    resume_bytes: bytes,
    marks_card: MarksCardUpload | None = None,
) -> ProgramSubmissionResult:
    program = await get_program(db, program_id)
    # Eligibility, terms version and the application window are checked
    # first, so an ineligible submission never spends the verified token.
    program_service.check_application_allowed(program, declaration)
    organization = await get_organization(db, program)

    async def enroll(session: AsyncSession, application: Application, candidate: Candidate) -> None:
        await program_service.record_enrollment(
            session,
            program=program,
            application=application,
            candidate=candidate,
            declaration=declaration,
        )

    submission = await public_application_service.submit_application(
        db,
        storage,
        organization=organization,
        job_id=program.job_id,
        email=email,
        verification_token=verification_token,
        profile=profile,
        resume_filename=resume_filename,
        resume_content_type="application/pdf",
        resume_bytes=resume_bytes,
        marks_card=marks_card,
        program=program,
        on_created=enroll,
    )
    return ProgramSubmissionResult(submission=submission, organization=organization, program=program)
