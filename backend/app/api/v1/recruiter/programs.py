"""Recruiter-facing recruitment programs (docs/programs.md).

Permissions: `program.read` to view, `program.manage` to configure/publish
programs and move participants through eligibility, test, interview,
training and employment; `program.finance.manage` (ORG_ADMIN by default)
for the scholarship decision, disbursements, training-fee payments, the
agreement and the bond. `organization_id` always comes from the caller.
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import granted_permission_codes, require_permission
from app.core.config import get_settings
from app.core.exceptions import AppError, ForbiddenError, NotFoundError
from app.db.session import get_db
from app.integrations.storage import (
    ResumeStorage,
    StorageError,
    get_resume_storage,
    get_resume_storage_for_provider,
)
from app.models.program import (
    DisbursementStatus,
    EligibilityStatus,
    PaymentRecordStatus,
    ProgramStatus,
    RecruitmentProgram,
    ScholarshipStatus,
    TrainingStatus,
)
from app.models.user import User
from app.schemas.assessment import AssessmentInvitationResponse
from app.schemas.institution import InstitutionSummary
from app.schemas.program import (
    AgreementUpdateRequest,
    BondUpdateRequest,
    DisbursementCreateRequest,
    DisbursementResponse,
    DisbursementUpdateRequest,
    EligibilityReviewRequest,
    EnrollmentResponse,
    EnrollmentResultSummary,
    FeePaymentCreateRequest,
    FeePaymentResponse,
    FeePaymentVoidRequest,
    JoiningRequest,
    OfferUpdateRequest,
    ProgramCreateRequest,
    ProgramReport,
    ProgramResponse,
    ProgramStatusRequest,
    ProgramSummary,
    ProgramUpdateRequest,
    ScholarshipDecisionRequest,
    ScholarshipEligibilityRequest,
    TrainingUpdateRequest,
)
from app.services import program_service, resume_service
from app.workflows import program_workflow

router = APIRouter(prefix="/programs", tags=["recruiter-programs"])

_FINANCE = "program.finance.manage"


async def _program_response(db: AsyncSession, program: RecruitmentProgram) -> ProgramResponse:
    counts = await program_service.enrollment_counts(db, program.organization_id)
    return ProgramResponse(
        **{
            field: getattr(program, field)
            for field in ProgramResponse.model_fields
            if hasattr(program, field)
            and field not in ("eligible_institutions", "publication_problems", "enrollment_count")
        },
        eligible_institutions=[InstitutionSummary.model_validate(i) for i in program.eligible_institutions],
        assessment_title=program.assessment.title if program.assessment else None,
        employment_job_title=program.employment_job.title if program.employment_job else None,
        publication_problems=(
            program_service.publication_problems(program)
            if program.status == ProgramStatus.DRAFT
            else []
        ),
        enrollment_count=counts.get(program.id, 0),
    )


def _enrollment_response(
    view: program_service.EnrollmentView, *, include_finance: bool
) -> EnrollmentResponse:
    enrollment = view.enrollment
    invitation = view.latest_invitation
    result = invitation.result if invitation is not None else None
    payments = view.fee_payments if include_finance else []
    disbursements = view.disbursements if include_finance else []
    return EnrollmentResponse(
        **{
            field: getattr(enrollment, field)
            for field in EnrollmentResponse.model_fields
            if hasattr(enrollment, field) and field not in ("fee_payments", "disbursements")
        },
        candidate_full_name=view.candidate.full_name,
        candidate_email=view.candidate.email,
        application_status=view.application.status,
        stage=program_workflow.derive_stage(enrollment, view.application),
        applied_at=view.application.applied_at,
        latest_result=(
            EnrollmentResultSummary(
                percentage=result.percentage,
                passed=result.passed,
                grading_status=result.grading_status,
                attempt_number=invitation.attempt_number,
            )
            if result is not None and invitation is not None
            else None
        ),
        fee_paid_total=sum(
            (p.amount for p in payments if p.status == PaymentRecordStatus.RECORDED), Decimal("0")
        ),
        disbursed_total=sum(
            (d.amount for d in disbursements if d.status == DisbursementStatus.PAID), Decimal("0")
        ),
        fee_payments=[FeePaymentResponse.model_validate(p) for p in payments],
        disbursements=[DisbursementResponse.model_validate(d) for d in disbursements],
    )


async def _program(db: AsyncSession, user: User, program_id: uuid.UUID) -> RecruitmentProgram:
    assert user.organization_id is not None
    return await program_service.require_program(db, user.organization_id, program_id)


async def _enrollment_out(
    db: AsyncSession, user: User, enrollment: object
) -> EnrollmentResponse:
    view = await program_service.enrollment_view(db, enrollment)  # type: ignore[arg-type]
    include_finance = _FINANCE in await granted_permission_codes(db, user)
    return _enrollment_response(view, include_finance=include_finance)


# --- Programs ---


@router.get("", response_model=list[ProgramSummary])
async def list_programs(
    program_status: ProgramStatus | None = Query(default=None, alias="status"),
    current_user: User = Depends(require_permission("program.read")),
    db: AsyncSession = Depends(get_db),
) -> list[ProgramSummary]:
    assert current_user.organization_id is not None
    programs = await program_service.list_programs(
        db, current_user.organization_id, status=program_status
    )
    counts = await program_service.enrollment_counts(db, current_user.organization_id)
    return [
        ProgramSummary(
            id=p.id,
            title=p.title,
            status=p.status,
            training_duration_options=p.training_duration_options,
            application_opens_at=p.application_opens_at,
            application_closes_at=p.application_closes_at,
            enrollment_count=counts.get(p.id, 0),
            created_at=p.created_at,
        )
        for p in programs
    ]


@router.post("", response_model=ProgramResponse, status_code=status.HTTP_201_CREATED)
async def create_program(
    payload: ProgramCreateRequest,
    current_user: User = Depends(require_permission("program.manage")),
    db: AsyncSession = Depends(get_db),
) -> ProgramResponse:
    program = await program_service.create_program(db, actor=current_user, payload=payload)
    return await _program_response(db, program)


@router.get("/{program_id}", response_model=ProgramResponse)
async def get_program(
    program_id: uuid.UUID,
    current_user: User = Depends(require_permission("program.read")),
    db: AsyncSession = Depends(get_db),
) -> ProgramResponse:
    return await _program_response(db, await _program(db, current_user, program_id))


@router.patch("/{program_id}", response_model=ProgramResponse)
async def update_program(
    program_id: uuid.UUID,
    payload: ProgramUpdateRequest,
    current_user: User = Depends(require_permission("program.manage")),
    db: AsyncSession = Depends(get_db),
) -> ProgramResponse:
    program = await _program(db, current_user, program_id)
    updated = await program_service.update_program(db, program, payload, actor=current_user)
    return await _program_response(db, updated)


@router.post("/{program_id}/status", response_model=ProgramResponse)
async def change_program_status(
    program_id: uuid.UUID,
    payload: ProgramStatusRequest,
    current_user: User = Depends(require_permission("program.manage")),
    db: AsyncSession = Depends(get_db),
) -> ProgramResponse:
    program = await _program(db, current_user, program_id)
    updated = await program_service.change_status(db, program, payload.status, actor=current_user)
    return await _program_response(db, updated)


@router.get("/{program_id}/report", response_model=ProgramReport)
async def program_report(
    program_id: uuid.UUID,
    current_user: User = Depends(require_permission("program.read")),
    db: AsyncSession = Depends(get_db),
) -> ProgramReport:
    return await program_service.program_report(db, await _program(db, current_user, program_id))


# --- Participants ---


@router.get("/{program_id}/enrollments", response_model=list[EnrollmentResponse])
async def list_enrollments(
    program_id: uuid.UUID,
    eligibility: EligibilityStatus | None = Query(default=None),
    scholarship: ScholarshipStatus | None = Query(default=None),
    training: TrainingStatus | None = Query(default=None),
    current_user: User = Depends(require_permission("program.read")),
    db: AsyncSession = Depends(get_db),
) -> list[EnrollmentResponse]:
    program = await _program(db, current_user, program_id)
    views = await program_service.list_enrollments(
        db, program, eligibility=eligibility, scholarship=scholarship, training=training
    )
    return [_enrollment_response(v, include_finance=False) for v in views]


@router.get("/{program_id}/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def get_enrollment(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    current_user: User = Depends(require_permission("program.read")),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    return await _enrollment_out(db, current_user, enrollment)


@router.post("/{program_id}/enrollments/{enrollment_id}/eligibility", response_model=EnrollmentResponse)
async def review_eligibility(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    payload: EligibilityReviewRequest,
    current_user: User = Depends(require_permission("program.manage")),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    await program_service.review_eligibility(
        db, enrollment=enrollment, status=payload.status, note=payload.note, actor=current_user
    )
    return await _enrollment_out(db, current_user, enrollment)


@router.post(
    "/{program_id}/enrollments/{enrollment_id}/invite-assessment",
    response_model=AssessmentInvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_to_program_assessment(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    current_user: User = Depends(require_permission("program.manage")),
    db: AsyncSession = Depends(get_db),
) -> AssessmentInvitationResponse:
    """Prepares the program-test invitation (not emailed — send it from the
    application like any assessment invitation). Also needs
    `assessment.manage`, the permission that governs every invitation."""
    if "assessment.manage" not in await granted_permission_codes(db, current_user):
        raise ForbiddenError("You do not have permission to perform this action.")
    from app.api.v1.recruiter.assessments import _invitation_response

    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    invitation, raw_token = await program_service.invite_to_assessment(
        db, program=program, enrollment=enrollment, actor=current_user
    )
    link = f"{get_settings().frontend_base_url}/assessment/{raw_token}"
    return await _invitation_response(db, invitation, invitation_link=link)


@router.post(
    "/{program_id}/enrollments/{enrollment_id}/interview-completed", response_model=EnrollmentResponse
)
async def mark_interview_completed(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    current_user: User = Depends(require_permission("program.manage")),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    await program_service.mark_interview_completed(db, enrollment=enrollment, actor=current_user)
    return await _enrollment_out(db, current_user, enrollment)


@router.post(
    "/{program_id}/enrollments/{enrollment_id}/scholarship-eligibility",
    response_model=EnrollmentResponse,
)
async def decide_scholarship_eligibility(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    payload: ScholarshipEligibilityRequest,
    current_user: User = Depends(require_permission("program.manage")),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    await program_service.decide_scholarship_eligibility(
        db, enrollment=enrollment, decision=payload.decision, note=payload.note, actor=current_user
    )
    return await _enrollment_out(db, current_user, enrollment)


@router.post(
    "/{program_id}/enrollments/{enrollment_id}/scholarship-decision",
    response_model=EnrollmentResponse,
)
async def decide_scholarship(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    payload: ScholarshipDecisionRequest,
    current_user: User = Depends(require_permission(_FINANCE)),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    await program_service.decide_scholarship(
        db,
        program=program,
        enrollment=enrollment,
        decision=payload.decision,
        amount=payload.approved_amount,
        currency=payload.currency,
        note=payload.note,
        actor=current_user,
    )
    return await _enrollment_out(db, current_user, enrollment)


@router.post("/{program_id}/enrollments/{enrollment_id}/disbursements", response_model=EnrollmentResponse)
async def record_disbursement(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    payload: DisbursementCreateRequest,
    current_user: User = Depends(require_permission(_FINANCE)),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    await program_service.record_disbursement(
        db,
        enrollment=enrollment,
        amount=payload.amount,
        status=payload.status,
        scheduled_on=payload.scheduled_on,
        paid_on=payload.paid_on,
        reference=payload.reference,
        note=payload.note,
        actor=current_user,
    )
    return await _enrollment_out(db, current_user, enrollment)


@router.post(
    "/{program_id}/enrollments/{enrollment_id}/disbursements/{disbursement_id}",
    response_model=EnrollmentResponse,
)
async def update_disbursement(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    disbursement_id: uuid.UUID,
    payload: DisbursementUpdateRequest,
    current_user: User = Depends(require_permission(_FINANCE)),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    await program_service.update_disbursement(
        db,
        enrollment=enrollment,
        disbursement_id=disbursement_id,
        status=payload.status,
        paid_on=payload.paid_on,
        note=payload.note,
        actor=current_user,
    )
    return await _enrollment_out(db, current_user, enrollment)


@router.post("/{program_id}/enrollments/{enrollment_id}/fee-payments", response_model=EnrollmentResponse)
async def record_fee_payment(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    payload: FeePaymentCreateRequest,
    current_user: User = Depends(require_permission(_FINANCE)),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    await program_service.record_fee_payment(
        db,
        program=program,
        enrollment=enrollment,
        amount=payload.amount,
        currency=payload.currency,
        paid_on=payload.paid_on,
        method=payload.method,
        reference=payload.reference,
        note=payload.note,
        actor=current_user,
    )
    return await _enrollment_out(db, current_user, enrollment)


@router.post(
    "/{program_id}/enrollments/{enrollment_id}/fee-payments/{payment_id}/void",
    response_model=EnrollmentResponse,
)
async def void_fee_payment(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    payment_id: uuid.UUID,
    payload: FeePaymentVoidRequest,
    current_user: User = Depends(require_permission(_FINANCE)),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    await program_service.void_fee_payment(
        db, enrollment=enrollment, payment_id=payment_id, reason=payload.reason, actor=current_user
    )
    return await _enrollment_out(db, current_user, enrollment)


@router.post("/{program_id}/enrollments/{enrollment_id}/training", response_model=EnrollmentResponse)
async def update_training(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    payload: TrainingUpdateRequest,
    current_user: User = Depends(require_permission("program.manage")),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    await program_service.update_training(
        db,
        program=program,
        enrollment=enrollment,
        status=payload.status,
        duration_months=payload.duration_months,
        start_date=payload.start_date,
        end_date=payload.end_date,
        actor=current_user,
    )
    return await _enrollment_out(db, current_user, enrollment)


@router.post("/{program_id}/enrollments/{enrollment_id}/offer", response_model=EnrollmentResponse)
async def update_offer(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    payload: OfferUpdateRequest,
    current_user: User = Depends(require_permission("program.manage")),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    await program_service.update_offer(
        db, program=program, enrollment=enrollment, status=payload.status, note=payload.note,
        actor=current_user,
    )
    return await _enrollment_out(db, current_user, enrollment)


@router.post("/{program_id}/enrollments/{enrollment_id}/joining", response_model=EnrollmentResponse)
async def record_joining(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    payload: JoiningRequest,
    current_user: User = Depends(require_permission("program.manage")),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    await program_service.record_joining(
        db, enrollment=enrollment, joined_on=payload.joined_on, actor=current_user
    )
    return await _enrollment_out(db, current_user, enrollment)


@router.post("/{program_id}/enrollments/{enrollment_id}/agreement", response_model=EnrollmentResponse)
async def update_agreement(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    payload: AgreementUpdateRequest,
    current_user: User = Depends(require_permission(_FINANCE)),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    await program_service.update_agreement(
        db,
        enrollment=enrollment,
        status=payload.status,
        reference=payload.reference,
        signed_on=payload.signed_on,
        actor=current_user,
    )
    return await _enrollment_out(db, current_user, enrollment)


@router.post(
    "/{program_id}/enrollments/{enrollment_id}/agreement/document", response_model=EnrollmentResponse
)
async def upload_agreement_document(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission(_FINANCE)),
    db: AsyncSession = Depends(get_db),
    storage: ResumeStorage = Depends(get_resume_storage),
) -> EnrollmentResponse:
    """The signed copy of the company-approved agreement, as a PDF. Bytes go
    to the storage abstraction; Postgres keeps only the reference, and the
    storage path is never returned to any client."""
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    max_bytes = get_settings().max_resume_size_mb * 1024 * 1024
    content = await file.read(max_bytes + 1)
    filename = file.filename or "agreement.pdf"
    try:
        resume_service.validate_resume_upload(filename=filename, content=content, pdf_only=True)
    except AppError as exc:
        raise AppError(
            exc.message.replace("resume", "agreement").replace("Resume", "Agreement"), code=exc.code
        ) from exc
    saved = await storage.save(
        organization_id=enrollment.organization_id,
        candidate_id=enrollment.candidate_id,
        application_id=enrollment.application_id,
        original_filename=filename,
        content_type="application/pdf",
        content=content,
        uploaded_by_user_id=current_user.id,
        document_kind="program_agreement",
    )
    try:
        previous_path = await program_service.attach_agreement_document(
            db,
            enrollment=enrollment,
            storage_provider=saved.storage_provider,
            storage_path=saved.storage_path,
            filename=filename,
            actor=current_user,
        )
    except BaseException:
        await storage.delete(saved.storage_path)
        raise
    response = await _enrollment_out(db, current_user, enrollment)
    await db.commit()
    if previous_path and previous_path != saved.storage_path:
        try:
            await storage.delete(previous_path)
        except StorageError:
            pass  # an orphaned old copy is harmless; the reference moved on
    return response


@router.get("/{program_id}/enrollments/{enrollment_id}/agreement/document")
async def download_agreement_document(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    current_user: User = Depends(require_permission(_FINANCE)),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    if not enrollment.agreement_document_storage_path or not enrollment.agreement_document_storage_provider:
        raise NotFoundError("No signed agreement has been attached.")
    try:
        storage = get_resume_storage_for_provider(enrollment.agreement_document_storage_provider)
        chunks = await storage.open_stream(enrollment.agreement_document_storage_path)
    except StorageError as exc:
        raise NotFoundError("The agreement file could not be found in storage.") from exc
    safe_name = (enrollment.agreement_document_filename or "agreement.pdf").replace('"', "")
    return StreamingResponse(
        chunks,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.post("/{program_id}/enrollments/{enrollment_id}/bond", response_model=EnrollmentResponse)
async def set_bond(
    program_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    payload: BondUpdateRequest,
    current_user: User = Depends(require_permission(_FINANCE)),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentResponse:
    program = await _program(db, current_user, program_id)
    enrollment = await program_service.require_enrollment(db, program, enrollment_id)
    await program_service.set_bond(
        db,
        enrollment=enrollment,
        start_date=payload.start_date,
        duration_months=payload.duration_months,
        actor=current_user,
    )
    return await _enrollment_out(db, current_user, enrollment)
