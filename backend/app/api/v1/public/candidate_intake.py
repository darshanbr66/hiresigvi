"""Anonymous candidate-intake endpoints that precede an application: the
organization's public profile (name + published careers contact) and email
one-time-code verification. Tenant scoping comes from resolving the slug and
setting RLS context before any query, exactly like the public jobs router.

Per-IP request budgets here are a first line of defence only; the
authoritative OTP limits (resend cooldown, hourly cap, attempt limit, expiry)
are enforced per email in the database by email_verification_service.
"""

from functools import lru_cache

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import NotFoundError, TooManyRequestsError
from app.core.rate_limit import SlidingWindowRateLimiter, client_key
from app.db.rls import set_tenant_context
from app.db.session import get_db
from app.models.organization import Organization
from app.schemas.public import (
    ApplicantProfilePrefill,
    ApplicantStatus,
    ApplicantStatusQuery,
    EmailVerificationConfirm,
    EmailVerificationConfirmed,
    EmailVerificationRequest,
    EmailVerificationRequested,
    PublicOrganizationSummary,
)
from app.services import organization_service, public_application_service

router = APIRouter(prefix="/organizations/{slug}", tags=["public-candidate-intake"])

#: Window for the per-IP budgets below (settings.public_*_limit_per_window).
RATE_WINDOW_SECONDS = 600.0
RATE_LIMITED_MESSAGE = "Too many requests. Please wait a few minutes and try again."


class CandidateIntakeRateLimiters:
    def __init__(self, *, otp_request: int, otp_verify: int, apply: int) -> None:
        window = RATE_WINDOW_SECONDS
        self.otp_request = SlidingWindowRateLimiter(limit=otp_request, window_seconds=window)
        self.otp_verify = SlidingWindowRateLimiter(limit=otp_verify, window_seconds=window)
        self.apply = SlidingWindowRateLimiter(limit=apply, window_seconds=window)


@lru_cache
def get_candidate_intake_rate_limiters() -> CandidateIntakeRateLimiters:
    settings = get_settings()
    return CandidateIntakeRateLimiters(
        otp_request=settings.public_otp_request_limit_per_window,
        otp_verify=settings.public_otp_verify_limit_per_window,
        apply=settings.public_apply_limit_per_window,
    )


def enforce(limiter: SlidingWindowRateLimiter, request: Request) -> None:
    if not limiter.allow(client_key(request)):
        raise TooManyRequestsError(RATE_LIMITED_MESSAGE, code="rate_limited")


async def resolve_organization(db: AsyncSession, slug: str) -> Organization:
    organization = await organization_service.get_organization_by_slug(db, slug)
    if organization is None:
        raise NotFoundError("Organization not found.")
    await set_tenant_context(db, organization.id)
    return organization


@router.get("", response_model=PublicOrganizationSummary)
async def get_public_organization(
    slug: str, db: AsyncSession = Depends(get_db)
) -> PublicOrganizationSummary:
    organization = await resolve_organization(db, slug)
    return PublicOrganizationSummary.model_validate(organization)


@router.post(
    "/email-verification/request", response_model=EmailVerificationRequested, status_code=202
)
async def request_email_verification(
    slug: str,
    payload: EmailVerificationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
) -> EmailVerificationRequested:
    enforce(limiters.otp_request, request)
    organization = await resolve_organization(db, slug)
    sent = await public_application_service.request_applicant_code(
        db, organization=organization, email=str(payload.email), job_id=payload.job_id
    )
    return EmailVerificationRequested(
        expires_in_seconds=sent.expires_in_seconds,
        resend_available_in_seconds=sent.resend_available_in_seconds,
    )


@router.post("/applicant-status", response_model=ApplicantStatus)
async def get_applicant_status(
    slug: str,
    payload: ApplicantStatusQuery,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
) -> ApplicantStatus:
    """The apply form's early check: has this email / mobile number already
    applied (with no reapply permission)? Only a yes/no — see
    public_application_service.has_already_applied."""
    enforce(limiters.otp_verify, request)
    organization = await resolve_organization(db, slug)
    return ApplicantStatus(
        already_applied=await public_application_service.has_already_applied(
            db,
            organization=organization,
            email=str(payload.email) if payload.email else None,
            phone=payload.phone,
            job_id=payload.job_id,
        )
    )


@router.post("/email-verification/verify", response_model=EmailVerificationConfirmed)
async def verify_email(
    slug: str,
    payload: EmailVerificationConfirm,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limiters: CandidateIntakeRateLimiters = Depends(get_candidate_intake_rate_limiters),
) -> EmailVerificationConfirmed:
    enforce(limiters.otp_verify, request)
    organization = await resolve_organization(db, slug)
    verified = await public_application_service.verify_applicant_email(
        db,
        organization=organization,
        email=str(payload.email),
        code=payload.code,
        job_id=payload.job_id,
    )
    return verified_response(verified)


def verified_response(
    verified: public_application_service.VerifiedApplicant,
) -> EmailVerificationConfirmed:
    """The verify endpoints' response: the token, plus the returning
    candidate's own stored profile (only ever after the code checked out)."""
    existing = verified.existing_candidate
    return EmailVerificationConfirmed(
        verification_token=verified.issued.token,
        expires_at=verified.issued.expires_at,
        existing_profile=(
            ApplicantProfilePrefill.model_validate(existing) if existing is not None else None
        ),
    )
