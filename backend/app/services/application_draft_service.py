"""The progressive self-service apply flow (careers site and campus drive
links): a server-side draft that each step saves into, an AI-assisted
check of the academic document, then submission through the existing
`public_application_service.submit_application`.

    Step 1-2  details + email one-time code (email_verification_service)
    start     -> `start_draft`: identity checks (mobile number, one
                 application per job, reapply window — the same rules and
                 consequences as a submission), then an OPEN draft
    Step 3    -> `save_background`
    Step 4    -> `save_education` (qualification must be one the JD allows;
                 specialization must belong to the chosen category)
              -> `upload_document` / `recheck_document` (AI consistency
                 check, every attempt kept as an EducationVerificationRun)
              -> `accept_manual_review` (only when the check could not
                 reach a confident answer)
    Step 5-6  -> `submit`: everything re-validated from the draft, then the
                 standard submission (Candidate + Application + resume,
                 screening, welcome email)

Security model: every function takes the email + verification token and
starts with `require_verified_token` — the token, not anything the browser
claims, is what proves the email was verified, and the draft is looked up
by that verified email. The education outcome is stored here and never
accepted from the client. A draft is never an Application: nothing
incomplete can be treated as submitted.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    TooManyRequestsError,
    UnprocessableError,
)
from app.core.logging import get_logger
from app.db.rls import set_tenant_context
from app.integrations.ai import (
    AIProviderError,
    AIProviderNotConfiguredError,
    get_education_ai_provider,
)
from app.integrations.ai.education import VocabularyEntry
from app.integrations.ai.extraction import extract_resume_text
from app.integrations.storage import (
    ResumeStorage,
    StorageError,
    get_resume_storage_for_provider,
)
from app.models.application import Application, ApplicationEducationStatus
from app.models.application_draft import (
    AcademicDocument,
    ApplicationDraft,
    ApplicationDraftStatus,
    EducationCheckStatus,
    EducationVerificationOutcome,
    EducationVerificationRun,
)
from app.models.campus_drive import CampusDrive
from app.models.candidate import Candidate
from app.models.email_verification import EmailVerification
from app.models.job import Job
from app.models.organization import Organization
from app.schemas.application_draft import (
    AcademicDocumentView,
    DraftView,
    EducationCheckView,
    EducationDetails,
)
from app.schemas.candidate import ApplicantBackground, PublicApplicantProfile
from app.services import (
    activity_service,
    candidate_document_service,
    email_verification_service,
    public_application_service,
    public_campus_drive_service,
)
from app.services.candidate_document_service import MarksCardUpload
from app.services.education import (
    catalog,
    document_check,
    job_qualification_service,
    specializations,
)
from app.services.education.catalog import QualificationOption
from app.services.education.job_qualification_service import (
    BRANCH_REQUIRED_FAMILIES,
    QualificationRequirements,
)

logger = get_logger(__name__)

DOCUMENT_KIND = "academic_document"

#: Failed AI attempts on one document after which the candidate may
#: continue to manual review (the AI itself is having trouble).
FAILED_ATTEMPTS_FOR_MANUAL_REVIEW = 2

_CHECK_TO_STATUS = {
    EducationVerificationOutcome.MATCH: EducationCheckStatus.VERIFIED,
    EducationVerificationOutcome.MISMATCH: EducationCheckStatus.MISMATCH,
    EducationVerificationOutcome.UNCLEAR: EducationCheckStatus.UNCLEAR,
    EducationVerificationOutcome.FAILED: EducationCheckStatus.FAILED,
}

_COMPLETE_STATUSES = frozenset({EducationCheckStatus.VERIFIED, EducationCheckStatus.MANUAL_REVIEW})


@dataclass(frozen=True)
class ApplyContext:
    """Where the application is going: always a job; a campus drive when
    the candidate came in through the drive's link."""

    organization: Organization
    job: Job
    campus_drive: CampusDrive | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# --- Lookup -----------------------------------------------------------------


async def _verified(
    db: AsyncSession, ctx: ApplyContext, *, email: str, token: str
) -> tuple[str, EmailVerification]:
    email = email_verification_service.normalize_email(email)
    verification = await email_verification_service.require_verified_token(
        db, organization_id=ctx.organization.id, email=email, token=token
    )
    return email, verification


async def _open_draft(
    db: AsyncSession, ctx: ApplyContext, email: str, *, for_update: bool = False
) -> ApplicationDraft | None:
    statement = select(ApplicationDraft).where(
        ApplicationDraft.organization_id == ctx.organization.id,
        ApplicationDraft.email == email,
        ApplicationDraft.job_id == ctx.job.id,
        ApplicationDraft.status == ApplicationDraftStatus.OPEN,
    )
    if for_update:
        statement = statement.with_for_update()
    draft = await db.scalar(statement)
    if draft is not None and _as_aware(draft.expires_at) <= _now():
        return None
    return draft


async def _require_draft(
    db: AsyncSession, ctx: ApplyContext, *, email: str, token: str, for_update: bool = True
) -> ApplicationDraft:
    email, _ = await _verified(db, ctx, email=email, token=token)
    draft = await _open_draft(db, ctx, email, for_update=for_update)
    if draft is None:
        submitted = await db.scalar(
            select(ApplicationDraft.id).where(
                ApplicationDraft.organization_id == ctx.organization.id,
                ApplicationDraft.email == email,
                ApplicationDraft.job_id == ctx.job.id,
                ApplicationDraft.status == ApplicationDraftStatus.SUBMITTED,
            )
        )
        if submitted is not None:
            raise public_application_service.already_applied_to_job_error(
                ctx.job, ctx.organization.careers_contact_email
            )
        raise NotFoundError(
            "Your application progress was not found or has expired. Please start again.",
            code="draft_not_found",
        )
    return draft


# --- Steps 1-3 ----------------------------------------------------------------


async def start_draft(
    db: AsyncSession,
    ctx: ApplyContext,
    *,
    email: str,
    token: str,
    full_name: str,
    phone: str,
) -> ApplicationDraft:
    """Runs the submission's identity rules for the verified email and this
    mobile number (a mobile number held by another profile, or a reapply
    window still running, burns the token exactly as a submission would),
    then opens — or resumes — the draft for this job. Idempotent: repeating
    it (a double click, coming back to Step 1 to fix a typo) updates the
    same draft, never a second one."""
    email, verification = await _verified(db, ctx, email=email, token=token)
    await public_application_service.identify_applicant(
        db,
        organization=ctx.organization,
        email=email,
        phone=phone,
        job=ctx.job,
        verification=verification,
    )
    expires_at = _now() + timedelta(days=get_settings().application_draft_ttl_days)
    draft = await _open_draft(db, ctx, email, for_update=True)
    if draft is None:
        # An expired OPEN draft would collide with the partial unique index.
        stale = await db.scalar(
            select(ApplicationDraft).where(
                ApplicationDraft.organization_id == ctx.organization.id,
                ApplicationDraft.email == email,
                ApplicationDraft.job_id == ctx.job.id,
                ApplicationDraft.status == ApplicationDraftStatus.OPEN,
            )
        )
        if stale is not None:
            await _delete_draft(db, stale)
        draft = ApplicationDraft(
            organization_id=ctx.organization.id,
            job_id=ctx.job.id,
            campus_drive_id=ctx.campus_drive.id if ctx.campus_drive else None,
            email=email,
            full_name=full_name,
            phone=phone,
            expires_at=expires_at,
        )
        db.add(draft)
    else:
        draft.full_name = full_name
        draft.phone = phone
        draft.expires_at = expires_at
    await db.flush()
    return draft


async def get_draft(
    db: AsyncSession, ctx: ApplyContext, *, email: str, token: str
) -> ApplicationDraft:
    return await _require_draft(db, ctx, email=email, token=token, for_update=False)


async def save_background(
    db: AsyncSession, ctx: ApplyContext, *, email: str, token: str, background: ApplicantBackground
) -> ApplicationDraft:
    draft = await _require_draft(db, ctx, email=email, token=token)
    draft.background = background.model_dump(mode="json")
    await db.flush()
    return draft


# --- Step 4: education ---------------------------------------------------------


def offered_options(requirements: QualificationRequirements) -> list[QualificationOption]:
    """What the Education step offers, and the only qualifications
    `_validate_education` accepts: `requirements.offered_keys` — the JD's
    eligible qualifications within the platform's offered list, or that
    whole list when the JD doesn't restrict. Never anything below a stated
    minimum."""
    return [catalog.BY_KEY[key] for key in requirements.offered_keys]


def branch_required(option: QualificationOption, requirements: QualificationRequirements) -> bool:
    return requirements.field_mandatory or option.family in BRANCH_REQUIRED_FAMILIES


def _validate_education(
    details: EducationDetails, requirements: QualificationRequirements
) -> tuple[QualificationOption, EducationDetails]:
    """Validates the selection and returns it normalized: `branch` is always
    the selected specialization's label, never what the client sent. The
    category/specialization pairing is a fixed lookup in the catalogue
    (specializations.py) — an unknown key, or a specialization from another
    category, is rejected."""
    option = catalog.get_option(details.qualification_key)
    offered = {o.key for o in offered_options(requirements)}
    if option is None or option.key not in offered:
        raise UnprocessableError(
            "This qualification isn't one of the eligible qualifications for this role. "
            + requirements.summary,
            code="qualification_not_eligible",
        )
    if option.is_other and not details.degree_name:
        raise UnprocessableError("Enter the name of your degree.", code="degree_name_required")
    if not details.specialization_category:
        raise UnprocessableError(
            "Select your specialization category.", code="specialization_category_required"
        )
    if specializations.get_category(details.specialization_category) is None:
        raise UnprocessableError(
            "Select a specialization category from the list.",
            code="specialization_category_invalid",
        )
    if not details.specialization:
        raise UnprocessableError("Select your specialization.", code="specialization_required")
    specialization = specializations.resolve(
        details.specialization_category, details.specialization
    )
    if specialization is None:
        raise UnprocessableError(
            "Select a specialization from the selected category.",
            code="specialization_invalid",
        )
    # A JD's required field of study doesn't narrow the choice: a
    # specialization that doesn't match it flags the submitted application
    # for HR instead (`eligibility_review`).
    return option, details.model_copy(update={"branch": specialization.label})


@dataclass(frozen=True)
class EligibilityReview:
    """Why HR should check a submitted application's eligibility (empty
    `reasons` = no flag), and the JD's field of study it was checked
    against. Stored on the application (`education_eligibility`) so HR sees
    exactly what was decided at submission, whatever the JD says later."""

    fields_of_study: list[str]
    field_mandatory: bool
    matched_field: str | None
    reasons: list[dict[str, str]]

    def as_json(self) -> dict[str, Any]:
        return {
            "fields_of_study": self.fields_of_study,
            "field_mandatory": self.field_mandatory,
            "matched_field": self.matched_field,
            "reasons": self.reasons,
        }


def eligibility_review(
    details: EducationDetails, requirements: QualificationRequirements
) -> EligibilityReview:
    reasons: list[dict[str, str]] = []
    if requirements.needs_review:
        reasons.append(
            {
                "code": "REQUIREMENTS_UNCLEAR",
                "message": (
                    "The job's qualification requirements couldn't be determined "
                    "automatically, so the default qualification list was offered."
                ),
            }
        )
    match = specializations.match_field_of_study(
        details.specialization, requirements.fields_of_study
    )
    if requirements.field_mandatory and requirements.fields_of_study and not match.matched:
        label = specializations.specialization_label(details.specialization) or "—"
        reasons.append(
            {
                "code": "FIELD_OF_STUDY_NOT_MATCHED",
                "message": (
                    "The job requires "
                    + " or ".join(requirements.fields_of_study)
                    + f", and the selected specialization (“{label}”) couldn't be matched "
                    "to it automatically."
                ),
            }
        )
    return EligibilityReview(
        fields_of_study=list(requirements.fields_of_study),
        field_mandatory=requirements.field_mandatory,
        matched_field=match.field_of_study,
        reasons=reasons,
    )


def _branch_key(education: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if not education:
        return None, None
    branch = education.get("branch")
    return education.get("qualification_key"), branch.casefold() if branch else None


async def save_education(
    db: AsyncSession, ctx: ApplyContext, *, email: str, token: str, details: EducationDetails
) -> ApplicationDraft:
    """Saves the education details. Changing the qualification or the
    branch resets the document check (the document stays, so the candidate
    can re-check it or upload another)."""
    draft = await _require_draft(db, ctx, email=email, token=token)
    requirements = await job_qualification_service.get_requirements(db, ctx.job)
    _, details = _validate_education(details, requirements)
    new_education = details.model_dump(mode="json")
    if _branch_key(draft.education) != _branch_key(new_education):
        draft.education_status = EducationCheckStatus.NOT_STARTED
    draft.education = new_education
    draft.qualification_source_hash = requirements.source_hash
    await db.flush()
    return draft


async def _failed_attempts_on(db: AsyncSession, document_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count(EducationVerificationRun.id)).where(
                EducationVerificationRun.academic_document_id == document_id,
                EducationVerificationRun.outcome == EducationVerificationOutcome.FAILED,
            )
        )
        or 0
    )


async def _latest_run(db: AsyncSession, draft: ApplicationDraft) -> EducationVerificationRun | None:
    if draft.academic_document_id is None:
        return None
    run: EducationVerificationRun | None = await db.scalar(
        select(EducationVerificationRun)
        .where(
            EducationVerificationRun.draft_id == draft.id,
            EducationVerificationRun.academic_document_id == draft.academic_document_id,
        )
        .order_by(EducationVerificationRun.created_at.desc(), EducationVerificationRun.id.desc())
        .limit(1)
    )
    return run


async def _manual_review_allowed(db: AsyncSession, draft: ApplicationDraft) -> bool:
    """Only when the check couldn't reach a confident answer for the
    current document: UNCLEAR, the AI not configured at all, or the AI
    failing repeatedly. A MISMATCH never qualifies."""
    if draft.academic_document_id is None or draft.education is None:
        return False
    if draft.education_status == EducationCheckStatus.UNCLEAR:
        return True
    if draft.education_status != EducationCheckStatus.FAILED:
        return False
    latest = await _latest_run(db, draft)
    if latest is not None and latest.reason_code == "ai_not_configured":
        return True
    return await _failed_attempts_on(db, draft.academic_document_id) >= (
        FAILED_ATTEMPTS_FOR_MANUAL_REVIEW
    )


def _require_attempt_budget(draft: ApplicationDraft) -> None:
    if draft.verification_attempts >= get_settings().max_education_checks_per_draft:
        raise TooManyRequestsError(
            "You've reached the limit of document checks for this application. Please contact "
            "our recruitment team if you need help.",
            code="education_check_limit",
        )


async def upload_document(
    db: AsyncSession,
    storage: ResumeStorage,
    ctx: ApplyContext,
    *,
    email: str,
    token: str,
    filename: str,
    content: bytes,
) -> ApplicationDraft:
    """Stores (or replaces) the academic document, then checks it. The file
    and its metadata are committed *before* the AI call, so an AI failure
    or timeout never loses the upload; a replaced document is deleted, file
    included, once the new one is committed."""
    upload = candidate_document_service.validate_academic_document(
        filename=filename, content=content
    )
    draft = await _require_draft(db, ctx, email=email, token=token)
    if draft.education is None:
        raise UnprocessableError(
            "Select your qualification before uploading the document.",
            code="education_details_required",
        )
    _require_attempt_budget(draft)
    try:
        saved = await storage.save(
            organization_id=ctx.organization.id,
            candidate_id=None,
            application_id=None,
            original_filename=upload.filename,
            content_type=upload.content_type,
            content=upload.content,
            document_kind=DOCUMENT_KIND,
        )
    except StorageError as exc:
        raise AppError(str(exc), code="storage_error") from exc

    previous = draft.academic_document
    document = AcademicDocument(
        organization_id=ctx.organization.id,
        draft_id=draft.id,
        qualification_key=draft.education["qualification_key"],
        storage_provider=saved.storage_provider,
        storage_path=saved.storage_path,
        original_filename=upload.filename[:500],
        content_type=upload.content_type,
        size_bytes=saved.size_bytes,
        sha256=hashlib.sha256(upload.content).hexdigest(),
    )
    try:
        db.add(document)
        await db.flush()
        draft.academic_document_id = document.id
        draft.academic_document = document
        draft.education_status = EducationCheckStatus.NOT_STARTED
        if previous is not None:
            await db.delete(previous)
        await db.commit()
    except BaseException:
        await _discard_file(saved.storage_provider, saved.storage_path)
        raise
    if previous is not None:
        await _discard_file(previous.storage_provider, previous.storage_path)
    await set_tenant_context(db, ctx.organization.id)
    await _run_check(db, ctx, draft, upload.content)
    return draft


async def recheck_document(
    db: AsyncSession, ctx: ApplyContext, *, email: str, token: str
) -> ApplicationDraft:
    """Runs the check again on the stored document (after an AI failure, or
    after the candidate corrected their qualification/branch)."""
    draft = await _require_draft(db, ctx, email=email, token=token)
    document = draft.academic_document
    if document is None or draft.education is None:
        raise UnprocessableError(
            "Upload your academic document first.", code="academic_document_required"
        )
    if draft.education_status in _COMPLETE_STATUSES:
        return draft
    _require_attempt_budget(draft)
    try:
        content = await get_resume_storage_for_provider(document.storage_provider).read(
            document.storage_path
        )
    except StorageError as exc:
        raise AppError(
            "Your document could not be read back. Please upload it again.",
            code="academic_document_unavailable",
        ) from exc
    await _run_check(db, ctx, draft, content)
    return draft


def _text_layer(content: bytes, content_type: str) -> str | None:
    if content_type != "application/pdf":
        return None
    try:
        return extract_resume_text(content=content, filename="document.pdf")
    except AIProviderError:
        return None


def _entry(option: QualificationOption) -> VocabularyEntry:
    return VocabularyEntry(
        key=option.key, label=option.label, level=option.level.value, family=option.family
    )


async def _run_check(
    db: AsyncSession, ctx: ApplyContext, draft: ApplicationDraft, content: bytes
) -> None:
    document = draft.academic_document
    assert document is not None and draft.education is not None
    details = EducationDetails.model_validate(draft.education)
    option = catalog.get_option(details.qualification_key)
    assert option is not None
    requirements = await job_qualification_service.get_requirements(db, ctx.job)

    draft.verification_attempts += 1
    provider = get_education_ai_provider()
    analysis_json: dict[str, Any] | None = None
    error: str | None = None
    try:
        analysis = await provider.analyze_academic_document(
            content=content,
            content_type=document.content_type,
            extracted_text=_text_layer(content, document.content_type),
            selected=_entry(option),
            selected_branch=details.branch,
            vocabulary=job_qualification_service.vocabulary(),
        )
    except AIProviderNotConfiguredError as exc:
        result = document_check.CheckResult(
            EducationVerificationOutcome.FAILED,
            "ai_not_configured",
            document_check.AI_UNAVAILABLE_MESSAGE,
        )
        error = str(exc)[:500]
    except AIProviderError as exc:
        logger.warning(
            "Academic document check failed",
            extra={"extra_fields": {"draft_id": str(draft.id), "error_type": type(exc).__name__}},
        )
        result = document_check.CheckResult(
            EducationVerificationOutcome.FAILED, "ai_failed", document_check.AI_FAILED_MESSAGE
        )
        error = str(exc)[:500]
    else:
        analysis_json = analysis.model_dump(mode="json")
        result = document_check.evaluate(
            analysis,
            selected=option,
            selected_branch=details.branch,
            requirements=requirements,
            candidate_name=draft.full_name,
        )

    db.add(
        EducationVerificationRun(
            organization_id=ctx.organization.id,
            draft_id=draft.id,
            academic_document_id=document.id,
            qualification_key=option.key,
            specialization_category=details.specialization_category,
            specialization=details.specialization,
            outcome=result.outcome,
            reason_code=result.reason_code,
            confidence=analysis_json.get("confidence") if analysis_json else None,
            candidate_message=result.message,
            analysis=analysis_json,
            provider=provider.name,
            model=provider.model,
            error_message=error,
            qualification_source_hash=requirements.source_hash,
        )
    )
    draft.education_status = _CHECK_TO_STATUS[result.outcome]
    draft.qualification_source_hash = requirements.source_hash
    await db.flush()


async def accept_manual_review(
    db: AsyncSession, ctx: ApplyContext, *, email: str, token: str
) -> ApplicationDraft:
    draft = await _require_draft(db, ctx, email=email, token=token)
    if draft.education_status == EducationCheckStatus.MANUAL_REVIEW:
        return draft
    if not await _manual_review_allowed(db, draft):
        raise UnprocessableError(
            "Manual review isn't available for this document. Please upload a document that "
            "matches the qualification you selected.",
            code="manual_review_not_allowed",
        )
    draft.education_status = EducationCheckStatus.MANUAL_REVIEW
    await db.flush()
    return draft


# --- View -----------------------------------------------------------------------


async def view(db: AsyncSession, draft: ApplicationDraft) -> DraftView:
    latest = await _latest_run(db, draft)
    status = draft.education_status
    message: str | None = None
    if status == EducationCheckStatus.MANUAL_REVIEW:
        message = (
            "Your document will be reviewed by our recruitment team. You can continue with "
            "your application."
        )
    elif status != EducationCheckStatus.NOT_STARTED and latest is not None:
        message = latest.candidate_message
    remaining = max(0, get_settings().max_education_checks_per_draft - draft.verification_attempts)
    document = draft.academic_document
    return DraftView(
        email=draft.email,
        full_name=draft.full_name,
        phone=draft.phone,
        background=(
            ApplicantBackground.model_validate(draft.background) if draft.background else None
        ),
        education=EducationDetails.model_validate(draft.education) if draft.education else None,
        document=(
            AcademicDocumentView(
                filename=document.original_filename,
                content_type=document.content_type,
                size_bytes=document.size_bytes,
                uploaded_at=document.created_at,
            )
            if document is not None
            else None
        ),
        education_check=EducationCheckView(
            status=status,
            message=message,
            can_continue=status in _COMPLETE_STATUSES,
            can_retry=(
                document is not None
                and remaining > 0
                and status in (EducationCheckStatus.NOT_STARTED, EducationCheckStatus.FAILED)
            ),
            can_request_manual_review=await _manual_review_allowed(db, draft),
            attempts_remaining=remaining,
        ),
        expires_at=draft.expires_at,
    )


# --- Step 6: submission -----------------------------------------------------


def _qualification_text(option: QualificationOption, details: EducationDetails) -> str:
    name = details.degree_name if option.is_other and details.degree_name else option.label
    return f"{name} in {details.branch}"[:255] if details.branch else name[:255]


async def _recheck_eligibility(
    db: AsyncSession, ctx: ApplyContext, draft: ApplicationDraft, details: EducationDetails
) -> QualificationRequirements:
    """The JD may have changed since the education step: the selection is
    re-validated against the job's *current* requirements, and a selection
    that's no longer eligible sends the candidate back to Step 4. Always
    re-validated (it's deterministic and cheap), not only when the JD hash
    changed, so a change to the offered list is enforced too."""
    requirements = await job_qualification_service.get_requirements(db, ctx.job)
    try:
        _validate_education(details, requirements)
    except UnprocessableError as exc:
        draft.education_status = EducationCheckStatus.NOT_STARTED
        draft.qualification_source_hash = requirements.source_hash
        await db.commit()
        raise ConflictError(
            "This role's eligibility requirements have changed since you entered your "
            f"education. {exc.message} Please review your education details.",
            code="education_recheck_required",
        ) from exc
    draft.qualification_source_hash = requirements.source_hash
    return requirements


async def submit(
    db: AsyncSession,
    storage: ResumeStorage,
    ctx: ApplyContext,
    *,
    email: str,
    token: str,
    resume_filename: str,
    resume_bytes: bytes,
    marks_card: MarksCardUpload | None,
) -> "SubmittedDraft":
    """Builds the application from the draft — every step's data as the
    server validated it — and submits it through the standard path. The
    draft row is locked for the duration, so a double submit waits and then
    finds it SUBMITTED (409), never creating a second application."""
    draft = await _require_draft(db, ctx, email=email, token=token)
    if draft.background is None:
        raise UnprocessableError(
            "Complete your professional background first.", code="background_required"
        )
    if draft.education is None:
        raise UnprocessableError(
            "Complete your education details first.", code="education_required"
        )
    if draft.academic_document is None:
        raise UnprocessableError(
            "Upload your academic document first.", code="academic_document_required"
        )
    if draft.education_status not in _COMPLETE_STATUSES:
        raise UnprocessableError(
            "Your academic document hasn't been verified yet. Please complete the Education step.",
            code="education_not_verified",
        )
    details = EducationDetails.model_validate(draft.education)
    option = catalog.get_option(details.qualification_key)
    if option is None:
        raise UnprocessableError("Select your qualification again.", code="education_required")
    if not details.specialization_category or not details.specialization:
        # A draft saved before the specialization catalogue existed.
        raise UnprocessableError(
            "Select your specialization category and specialization in the Education step.",
            code="education_required",
        )
    requirements = await _recheck_eligibility(db, ctx, draft, details)
    review = eligibility_review(details, requirements)

    background = ApplicantBackground.model_validate(draft.background)
    profile = PublicApplicantProfile.model_validate(
        {
            **background.model_dump(),
            "full_name": draft.full_name,
            "phone": draft.phone,
            "qualification": _qualification_text(option, details),
            "tenth_marks_percentage": details.tenth_marks_percentage,
        }
    )
    education_status = (
        ApplicationEducationStatus.VERIFIED
        if draft.education_status == EducationCheckStatus.VERIFIED
        else ApplicationEducationStatus.MANUAL_REVIEW
    )
    document = draft.academic_document

    async def attach_education(
        session: AsyncSession, application: Application, candidate: Candidate
    ) -> None:
        application.education_verification_status = education_status
        application.education_qualification_key = option.key
        application.education_specialization_category = details.specialization_category
        application.education_specialization = details.specialization
        application.eligibility_review_required = bool(review.reasons)
        application.education_eligibility = review.as_json()
        for reason in review.reasons:
            await activity_service.record_activity(
                session,
                organization_id=ctx.organization.id,
                actor=None,
                action="QUALIFICATION_REVIEW_REQUIRED",
                entity_type="application",
                entity_id=application.id,
                entity_label=f"{candidate.full_name} — {ctx.job.title}",
                description=reason["message"]
                + " Please check the candidate's eligibility against the job description.",
            )
        candidate.education_level = option.education_level
        candidate.specialization = details.branch[:150] if details.branch else None
        candidate.institution_name = details.institution[:255]
        candidate.institution_id = None
        candidate.qualification_year = details.completion_year
        document.candidate_id = candidate.id
        document.application_id = application.id
        await session.execute(
            update(EducationVerificationRun)
            .where(EducationVerificationRun.draft_id == draft.id)
            .values(application_id=application.id)
        )
        draft.status = ApplicationDraftStatus.SUBMITTED
        draft.submitted_application_id = application.id
        # The document now belongs to the application (and candidate); the
        # submitted draft no longer points at it, so deleting the
        # application later cascades cleanly.
        draft.academic_document_id = None
        if education_status == ApplicationEducationStatus.MANUAL_REVIEW:
            await activity_service.record_activity(
                session,
                organization_id=ctx.organization.id,
                actor=None,
                action="EDUCATION_MANUAL_REVIEW_REQUIRED",
                entity_type="application",
                entity_id=application.id,
                entity_label=f"{candidate.full_name} — {ctx.job.title}",
                description=(
                    "The academic document could not be checked automatically with confidence; "
                    "please review it manually."
                ),
            )

    submission = await public_application_service.submit_application(
        db,
        storage,
        organization=ctx.organization,
        job_id=ctx.job.id,
        email=draft.email,
        verification_token=token,
        profile=profile,
        resume_filename=resume_filename,
        resume_content_type="application/pdf",
        resume_bytes=resume_bytes,
        marks_card=marks_card,
        campus_drive=ctx.campus_drive,
        on_created=attach_education,
    )
    invitation_link = (
        await public_campus_drive_service.fast_track_to_default_assessment(
            db, ctx.campus_drive, submission
        )
        if ctx.campus_drive is not None
        else None
    )
    return SubmittedDraft(submission=submission, assessment_invitation_link=invitation_link)


@dataclass(frozen=True)
class SubmittedDraft:
    submission: public_application_service.SubmissionResult
    #: A campus drive with a default assessment fast-tracks the candidate.
    assessment_invitation_link: str | None = None


# --- Housekeeping ------------------------------------------------------------


async def _discard_file(provider: str, storage_path: str) -> None:
    """Best-effort: a storage hiccup must neither mask the original error
    nor fail a completed step — it is logged for cleanup."""
    try:
        await get_resume_storage_for_provider(provider).delete(storage_path)
    except Exception:
        logger.exception(
            "Could not delete an unreferenced academic document from storage",
            extra={"extra_fields": {"storage_provider": provider, "storage_path": storage_path}},
        )


async def _delete_draft(db: AsyncSession, draft: ApplicationDraft) -> list[tuple[str, str]]:
    """Deletes an unsubmitted draft and its document row; returns the files
    to remove once the deletion is committed."""
    files: list[tuple[str, str]] = []
    documents = (
        await db.scalars(
            select(AcademicDocument).where(
                AcademicDocument.draft_id == draft.id, AcademicDocument.application_id.is_(None)
            )
        )
    ).all()
    draft.academic_document_id = None
    await db.flush()
    for document in documents:
        files.append((document.storage_provider, document.storage_path))
        await db.delete(document)
    await db.delete(draft)
    await db.flush()
    return files


async def purge_expired_drafts(
    db: AsyncSession, *, now: datetime | None = None, organization_id: uuid.UUID | None = None
) -> int:
    """Removes every expired, unsubmitted draft (all organizations — the
    caller runs it under the RLS bypass) together with its academic
    document, file included, and drops the step data of submitted drafts
    past the same age: that data now lives on the Candidate/Application,
    the draft row is only kept as a pointer. Returns the number of drafts
    deleted. `organization_id` limits it to one organization."""
    now = now or _now()
    scope = (
        [ApplicationDraft.organization_id == organization_id] if organization_id is not None else []
    )
    expired = (
        await db.scalars(
            select(ApplicationDraft).where(
                ApplicationDraft.status == ApplicationDraftStatus.OPEN,
                ApplicationDraft.expires_at <= now,
                *scope,
            )
        )
    ).all()
    files: list[tuple[str, str]] = []
    for draft in expired:
        files.extend(await _delete_draft(db, draft))
    await db.execute(
        update(ApplicationDraft)
        .where(
            ApplicationDraft.status == ApplicationDraftStatus.SUBMITTED,
            ApplicationDraft.expires_at <= now,
            *scope,
        )
        .values(background=None, education=None)
    )
    await db.commit()
    for provider, path in files:
        await _discard_file(provider, path)
    return len(expired)


async def application_documents(
    db: AsyncSession, application_id: uuid.UUID
) -> tuple[AcademicDocument | None, list[EducationVerificationRun]]:
    """The academic document and every check of it for one application
    (recruiter view). Tenant scoping is the caller's (and RLS's)."""
    document = await db.scalar(
        select(AcademicDocument).where(AcademicDocument.application_id == application_id)
    )
    runs = (
        await db.scalars(
            select(EducationVerificationRun)
            .where(EducationVerificationRun.application_id == application_id)
            .order_by(EducationVerificationRun.created_at.desc())
        )
    ).all()
    return document, list(runs)
