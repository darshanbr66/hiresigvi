"""Anonymous job application submission — the candidate-facing entry point
into the recruiting pipeline (CLAUDE.md § 1: "public career site... entry
point into candidate apply flow").

Every field is mandatory — except the LinkedIn/GitHub profile URLs and the
10th marks percentage and marks card, which are optional — and validated
here, server-side, regardless of what the React form enforces; the email
must have been verified with a one-time code (the `email_verification_token` from
`POST .../email-verification/verify`); the resume must be a real PDF. The
orchestration (identity check, AI screening, welcome email) lives in
app/services/public_application_service.py.

`applicant_submission` is the one definition of that form: the campus drive
link (app/api/v1/public/campus_drives.py) takes exactly the same fields.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import EmailStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.public.candidate_intake import (
    CandidateIntakeRateLimiters,
    enforce,
    get_candidate_intake_rate_limiters,
    resolve_organization,
)
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db.session import get_db
from app.integrations.storage import ResumeStorage, get_resume_storage
from app.models.candidate import CandidateType
from app.schemas.candidate import PublicApplicantProfile
from app.schemas.public import ApplicantPrecheck, PublicApplicationResult
from app.services import candidate_document_service, public_application_service
from app.services.candidate_document_service import MarksCardUpload

# The schema's `location` is the form's `current_location`.
_FIELD_ALIASES = {"location": "current_location"}


def _as_request_errors(exc: ValidationError) -> list[Any]:
    """Re-shapes a schema ValidationError as body-field errors, renaming the
    schema's `location` back to the form's `current_location`."""
    return [
        {**error, "loc": ("body", *(_FIELD_ALIASES.get(str(part), part) for part in error["loc"]))}
        for error in exc.errors(include_url=False, include_context=False, include_input=False)
    ]


@dataclass(frozen=True)
class ApplicantSubmission:
    email: str
    verification_token: str
    profile: PublicApplicantProfile
    resume_filename: str
    resume_bytes: bytes
    #: The optional 10th marks card, already validated (type, signature,
    #: size); None = not uploaded.
    marks_card: MarksCardUpload | None = None


async def applicant_submission(
    request: Request,
    # Required, deliberately without a length limit.
    full_name: str = Form(..., min_length=1),
    email: EmailStr = Form(...),
    email_verification_token: str = Form(..., min_length=1, max_length=200),
    phone: str = Form(..., min_length=1, max_length=32),
    date_of_birth: date = Form(...),
    place_of_birth: str = Form(..., min_length=1, max_length=255),
    # Repeat the field once per language (multipart list semantics).
    languages: list[str] = Form(...),
    candidate_type: CandidateType = Form(...),
    years_experience: int | None = Form(default=None, ge=0, le=80),
    notice_period_days: int | None = Form(default=None, ge=0, le=365),
    immediate_joiner: bool | None = Form(default=None),
    current_title: str | None = Form(default=None, max_length=255),
    current_company: str | None = Form(default=None, max_length=255),
    current_location: str = Form(..., min_length=1, max_length=255),
    preferred_location: str = Form(..., min_length=1, max_length=255),
    qualification: str = Form(..., min_length=1, max_length=255),
    linkedin_url: str | None = Form(default=None, max_length=500),
    github_url: str | None = Form(default=None, max_length=500),
    tenth_marks_percentage: Decimal | None = Form(default=None),
    resume: UploadFile = File(...),
    tenth_marks_card: UploadFile | None = File(default=None),
    limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
) -> ApplicantSubmission:
    """The complete self-service application form, validated. The per-IP
    apply budget is spent first, before any validation work."""
    enforce(limiters.apply, request)

    # Cross-field rules (EXPERIENCED needs experience/role/notice period,
    # mobile normalization, URL host checks, DOB bounds) live on the schema;
    # surface failures through the same 422 envelope as FastAPI's own form
    # validation.
    try:
        profile = PublicApplicantProfile(
            full_name=full_name,
            phone=phone,
            date_of_birth=date_of_birth,
            place_of_birth=place_of_birth,
            languages=languages,
            candidate_type=candidate_type,
            years_experience=years_experience,
            notice_period_days=notice_period_days,
            immediate_joiner=immediate_joiner,
            current_title=current_title,
            current_company=current_company,
            location=current_location,
            preferred_location=preferred_location,
            qualification=qualification,
            linkedin_url=linkedin_url,
            github_url=github_url,
            tenth_marks_percentage=tenth_marks_percentage,
        )
    except ValidationError as exc:
        raise RequestValidationError(_as_request_errors(exc)) from exc

    # Read at most one byte past the limit: an oversized upload is rejected
    # without ever being held in memory in full.
    max_bytes = get_settings().max_resume_size_mb * 1024 * 1024
    resume_bytes = await resume.read(max_bytes + 1)
    if len(resume_bytes) > max_bytes:
        raise AppError(
            f"Resume must be smaller than {get_settings().max_resume_size_mb}MB.",
            code="file_too_large",
        )

    return ApplicantSubmission(
        email=str(email),
        verification_token=email_verification_token,
        profile=profile,
        resume_filename=resume.filename or "resume.pdf",
        resume_bytes=resume_bytes,
        marks_card=await _read_marks_card(tenth_marks_card),
    )


async def _read_marks_card(upload: UploadFile | None) -> MarksCardUpload | None:
    """The optional marks card: absent (or an empty file part with no name,
    as a browser sends for an untouched file input) = not uploaded. Read at
    most one byte past the limit, like the resume."""
    if upload is None or (not upload.filename and upload.size in (None, 0)):
        return None
    max_bytes = get_settings().max_marks_card_size_mb * 1024 * 1024
    content = await upload.read(max_bytes + 1)
    return candidate_document_service.validate_marks_card(
        filename=upload.filename or "", content=content
    )


router = APIRouter(prefix="/organizations/{slug}/jobs", tags=["public-applications"])


@router.post("/{job_id}/apply/precheck", status_code=204)
async def precheck_application(
    slug: str,
    job_id: uuid.UUID,
    payload: ApplicantPrecheck,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
) -> Response:
    """The submission's identity checks (mobile number, one application per
    job, reapply window) for a verified email, before the resume step.
    204 = nothing stands in the way; conflicts are the same 409s a
    submission would return."""
    enforce(limiters.otp_verify, request)
    organization = await resolve_organization(db, slug)
    await public_application_service.precheck_applicant(
        db,
        organization=organization,
        job_id=job_id,
        email=str(payload.email),
        verification_token=payload.email_verification_token,
        phone=payload.phone,
    )
    return Response(status_code=204)


@router.post("/{job_id}/apply", response_model=PublicApplicationResult, status_code=201)
async def apply_to_job(
    slug: str,
    job_id: uuid.UUID,
    submission: ApplicantSubmission = Depends(applicant_submission),
    db: AsyncSession = Depends(get_db),
    storage: ResumeStorage = Depends(get_resume_storage),
) -> PublicApplicationResult:
    organization = await resolve_organization(db, slug)
    result = await public_application_service.submit_application(
        db,
        storage,
        organization=organization,
        job_id=job_id,
        email=submission.email,
        verification_token=submission.verification_token,
        profile=submission.profile,
        resume_filename=submission.resume_filename,
        resume_content_type="application/pdf",
        resume_bytes=submission.resume_bytes,
        marks_card=submission.marks_card,
    )

    return PublicApplicationResult(
        id=result.application.id,
        job_title=result.application.job.title,
        candidate_email=result.application.candidate.email,
        confirmation_email_sent=result.confirmation_email_sent,
        careers_contact_email=organization.careers_contact_email,
        submitted_at=result.application.applied_at,
    )
