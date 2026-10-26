"""Candidate-facing recruitment programs — no authentication
(docs/programs.md § 3). The listing is per organization (by slug); a
program's page and apply flow are addressed by the program's id, and the
backend resolves the organization and the program's job from it — nothing
the candidate sends can redirect an application elsewhere.

Applying takes exactly the careers site's form (`applicant_submission`) plus
the program's education declaration and terms acceptance."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.public.applications import ApplicantSubmission, applicant_submission
from app.api.v1.public.candidate_intake import (
    CandidateIntakeRateLimiters,
    enforce,
    get_candidate_intake_rate_limiters,
    resolve_organization,
    verified_response,
)
from app.db.session import get_db
from app.integrations.storage import ResumeStorage, get_resume_storage
from app.models.candidate import EducationLevel
from app.models.organization import Organization
from app.models.program import ProgramStatus, RecruitmentProgram
from app.schemas.institution import InstitutionSummary
from app.schemas.program import (
    ProgramEducationDeclaration,
    PublicProgramApplicationResult,
    PublicProgramSummary,
    PublicProgramUnavailable,
    PublicProgramView,
)
from app.schemas.public import (
    ApplicantPrecheck,
    ApplicantStatus,
    ApplicantStatusQuery,
    EmailVerificationConfirm,
    EmailVerificationConfirmed,
    EmailVerificationRequest,
    EmailVerificationRequested,
)
from app.services import program_service, public_program_service

listing_router = APIRouter(prefix="/organizations/{slug}/programs", tags=["public-programs"])
router = APIRouter(prefix="/programs", tags=["public-programs"])


def _public_view(program: RecruitmentProgram, organization: Organization) -> PublicProgramView:
    accepting, reason = program_service.accepting_applications(program)
    return PublicProgramView(
        id=program.id,
        title=program.title,
        description=program.description,
        organization_name=organization.name,
        careers_contact_email=organization.careers_contact_email,
        eligible_education_levels=[EducationLevel(v) for v in program.eligible_education_levels],
        eligible_study_years=program.eligible_study_years,
        eligible_specializations=program.eligible_specializations,
        eligible_institutions=[
            InstitutionSummary.model_validate(i) for i in program.eligible_institutions if i.is_active
        ],
        expected_completion_from=program.expected_completion_from,
        expected_completion_to=program.expected_completion_to,
        eligibility_notes=program.eligibility_notes,
        training_duration_options=program.training_duration_options,
        training_fee_amount=program.training_fee_amount,
        training_fee_currency=program.training_fee_currency,
        fee_payment_terms=program.fee_payment_terms,
        fee_disclosure=program.fee_disclosure,
        scholarship_amount_min=program.scholarship_amount_min,
        scholarship_amount_max=program.scholarship_amount_max,
        scholarship_currency=program.scholarship_currency,
        scholarship_eligibility_rules=program.scholarship_eligibility_rules,
        scholarship_disbursement_timing=program.scholarship_disbursement_timing,
        scholarship_disclosure=program.scholarship_disclosure,
        employment_pathway=program.employment_pathway,
        bond_duration_months=program.bond_duration_months,
        bond_terms_summary=program.bond_terms_summary,
        has_assessment=program.assessment_id is not None,
        interview_stages=program.interview_stages,
        required_documents=program.required_documents,
        candidate_disclosures=program.candidate_disclosures or "",
        terms_version=program.terms_version or "",
        disclosure_text=program_service.disclosure_text(program),
        application_opens_at=program.application_opens_at,
        application_closes_at=program.application_closes_at,
        accepting_applications=accepting,
        not_accepting_reason=reason,
    )


@listing_router.get("", response_model=list[PublicProgramSummary])
async def list_programs(slug: str, db: AsyncSession = Depends(get_db)) -> list[PublicProgramSummary]:
    organization = await resolve_organization(db, slug)
    programs = await public_program_service.list_published(db, organization.id)
    return [
        PublicProgramSummary(
            id=p.id,
            title=p.title,
            training_duration_options=p.training_duration_options,
            eligible_education_levels=[EducationLevel(v) for v in p.eligible_education_levels],
            application_closes_at=p.application_closes_at,
            accepting_applications=program_service.accepting_applications(p)[0],
        )
        for p in programs
    ]


@router.get("/{program_id}", response_model=PublicProgramView | PublicProgramUnavailable)
async def get_program(
    program_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> PublicProgramView | PublicProgramUnavailable:
    program = await public_program_service.get_program(db, program_id)
    if program.status == ProgramStatus.CLOSED:
        return PublicProgramUnavailable()
    organization = await public_program_service.get_organization(db, program)
    return _public_view(program, organization)


@router.post(
    "/{program_id}/email-verification/request",
    response_model=EmailVerificationRequested,
    status_code=202,
)
async def request_email_verification(
    program_id: uuid.UUID,
    payload: EmailVerificationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
) -> EmailVerificationRequested:
    enforce(limiters.otp_request, request)
    sent = await public_program_service.request_email_code(
        db, program_id=program_id, email=str(payload.email)
    )
    return EmailVerificationRequested(
        expires_in_seconds=sent.expires_in_seconds,
        resend_available_in_seconds=sent.resend_available_in_seconds,
    )


@router.post("/{program_id}/applicant-status", response_model=ApplicantStatus)
async def get_applicant_status(
    program_id: uuid.UUID,
    payload: ApplicantStatusQuery,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
) -> ApplicantStatus:
    enforce(limiters.otp_verify, request)
    return ApplicantStatus(
        already_applied=await public_program_service.has_already_applied(
            db,
            program_id=program_id,
            email=str(payload.email) if payload.email else None,
            phone=payload.phone,
        )
    )


@router.post("/{program_id}/email-verification/verify", response_model=EmailVerificationConfirmed)
async def verify_email(
    program_id: uuid.UUID,
    payload: EmailVerificationConfirm,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
) -> EmailVerificationConfirmed:
    enforce(limiters.otp_verify, request)
    verified = await public_program_service.verify_email_code(
        db, program_id=program_id, email=str(payload.email), code=payload.code
    )
    return verified_response(verified)


@router.post("/{program_id}/apply/precheck", status_code=204)
async def precheck_program_application(
    program_id: uuid.UUID,
    payload: ApplicantPrecheck,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
) -> Response:
    enforce(limiters.otp_verify, request)
    await public_program_service.precheck_applicant(
        db,
        program_id=program_id,
        email=str(payload.email),
        verification_token=payload.email_verification_token,
        phone=payload.phone,
    )
    return Response(status_code=204)


async def program_declaration(
    education_level: EducationLevel = Form(...),
    study_year: int | None = Form(default=None),
    specialization: str = Form(..., min_length=1, max_length=150),
    institution_id: uuid.UUID | None = Form(default=None),
    institution_name: str | None = Form(default=None, max_length=255),
    expected_completion_date: date | None = Form(default=None),
    terms_version: str = Form(..., min_length=1, max_length=50),
    terms_accepted: bool = Form(...),
) -> ProgramEducationDeclaration:
    try:
        return ProgramEducationDeclaration(
            education_level=education_level,
            study_year=study_year,
            specialization=specialization,
            institution_id=institution_id,
            institution_name=institution_name,
            expected_completion_date=expected_completion_date,
            terms_version=terms_version,
            terms_accepted=terms_accepted,
        )
    except ValidationError as exc:
        raise RequestValidationError(
            [
                {**e, "loc": ("body", *e["loc"])}
                for e in exc.errors(include_url=False, include_context=False, include_input=False)
            ]
        ) from exc


@router.post("/{program_id}/apply", response_model=PublicProgramApplicationResult, status_code=201)
async def apply_to_program(
    program_id: uuid.UUID,
    declaration: ProgramEducationDeclaration = Depends(program_declaration),
    submission: ApplicantSubmission = Depends(applicant_submission),
    db: AsyncSession = Depends(get_db),
    storage: ResumeStorage = Depends(get_resume_storage),
) -> PublicProgramApplicationResult:
    result = await public_program_service.apply_to_program(
        db,
        storage,
        program_id=program_id,
        email=submission.email,
        verification_token=submission.verification_token,
        profile=submission.profile,
        declaration=declaration,
        resume_filename=submission.resume_filename,
        resume_bytes=submission.resume_bytes,
        marks_card=submission.marks_card,
    )
    application = result.submission.application
    return PublicProgramApplicationResult(
        id=application.id,
        program_title=result.program.title,
        candidate_email=application.candidate.email,
        confirmation_email_sent=result.submission.confirmation_email_sent,
        careers_contact_email=result.organization.careers_contact_email,
        submitted_at=application.applied_at,
    )
