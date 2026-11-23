"""Candidate-facing campus drive access by opaque link token — no
authentication (docs/campus-hiring.md § 3).

Applying follows the careers site's candidate identity rules exactly: the
email is verified with a one-time code first (the two
`/email-verification/*` endpoints below, same limits as the careers site),
then the same progressive, draft-based flow as a careers-site job
(`/{token}/apply/...`, app/api/v1/public/apply_flow.py)."""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.public.candidate_intake import (
    CandidateIntakeRateLimiters,
    enforce,
    get_candidate_intake_rate_limiters,
    verified_response,
)
from app.db.session import get_db
from app.models.campus_drive import CampusDriveStatus
from app.schemas.public import (
    ApplicantPrecheck,
    ApplicantStatus,
    ApplicantStatusQuery,
    EmailVerificationConfirm,
    EmailVerificationConfirmed,
    EmailVerificationRequest,
    EmailVerificationRequested,
)
from app.schemas.public_campus_drive import (
    PublicCampusDriveUnavailable,
    PublicCampusDriveView,
)
from app.services import public_campus_drive_service

router = APIRouter(prefix="/campus-drive", tags=["public-campus-drive"])


@router.get("/{token}", response_model=PublicCampusDriveView | PublicCampusDriveUnavailable)
async def get_campus_drive(
    token: str, db: AsyncSession = Depends(get_db)
) -> PublicCampusDriveView | PublicCampusDriveUnavailable:
    drive = await public_campus_drive_service.get_drive_by_token(db, token)
    if drive.status == CampusDriveStatus.CLOSED:
        return PublicCampusDriveUnavailable()

    organization = await public_campus_drive_service.get_organization(db, drive)
    return PublicCampusDriveView(
        name=drive.name,
        college_name=drive.college_name,
        description=drive.description,
        job_title=drive.job.title,
        job_description=drive.job.description,
        organization_name=organization.name,
        registration_deadline=drive.registration_deadline,
        status=drive.status,
        has_assessment=drive.default_assessment_id is not None,
        careers_contact_email=organization.careers_contact_email,
    )


@router.post(
    "/{token}/email-verification/request",
    response_model=EmailVerificationRequested,
    status_code=202,
)
async def request_email_verification(
    token: str,
    payload: EmailVerificationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
) -> EmailVerificationRequested:
    enforce(limiters.otp_request, request)
    sent = await public_campus_drive_service.request_email_code(
        db, token=token, email=str(payload.email)
    )
    return EmailVerificationRequested(
        expires_in_seconds=sent.expires_in_seconds,
        resend_available_in_seconds=sent.resend_available_in_seconds,
    )


@router.post("/{token}/applicant-status", response_model=ApplicantStatus)
async def get_applicant_status(
    token: str,
    payload: ApplicantStatusQuery,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
) -> ApplicantStatus:
    """The careers site's early "already applied?" check, for the drive's job
    (any `job_id` in the body is ignored)."""
    enforce(limiters.otp_verify, request)
    return ApplicantStatus(
        already_applied=await public_campus_drive_service.has_already_applied(
            db,
            token=token,
            email=str(payload.email) if payload.email else None,
            phone=payload.phone,
        )
    )


@router.post("/{token}/email-verification/verify", response_model=EmailVerificationConfirmed)
async def verify_email(
    token: str,
    payload: EmailVerificationConfirm,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
) -> EmailVerificationConfirmed:
    enforce(limiters.otp_verify, request)
    verified = await public_campus_drive_service.verify_email_code(
        db, token=token, email=str(payload.email), code=payload.code
    )
    return verified_response(verified)


@router.post("/{token}/apply/precheck", status_code=204)
async def precheck_campus_application(
    token: str,
    payload: ApplicantPrecheck,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
) -> Response:
    """The careers site's pre-resume identity checks, for the drive's job."""
    enforce(limiters.otp_verify, request)
    await public_campus_drive_service.precheck_applicant(
        db,
        token=token,
        email=str(payload.email),
        verification_token=payload.email_verification_token,
        phone=payload.phone,
    )
    return Response(status_code=204)
