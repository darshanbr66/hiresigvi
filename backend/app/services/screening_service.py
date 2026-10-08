"""Orchestrates one AI screening run: load the application's resume from
storage, extract its text, call the configured LLM provider, and persist
the structured result (or the failure) onto a new ScreeningRun row — never
mutating a prior run (docs/ai-screening.md § 4).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, NotFoundError
from app.integrations.ai import AIProviderError, LLMProvider, get_llm_provider
from app.integrations.ai.extraction import extract_resume_text
from app.integrations.storage import StorageError, get_resume_storage_for_provider
from app.models.screening import ScreeningDecision, ScreeningRun, ScreeningStatus
from app.services import application_service


async def run_screening(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    application_id: uuid.UUID,
    requested_by_user_id: uuid.UUID | None,
) -> ScreeningRun:
    """`requested_by_user_id=None` is the system's own submission-time run
    (app/services/public_application_service.py). Evaluates against the
    job's full stored JD — also when the JD is hidden from the public page
    (that flag is presentation only)."""
    application = await application_service.get_application(db, application_id)
    if application is None:
        raise NotFoundError("Application not found.")
    if application.resume is None:
        raise AppError(
            "This application has no resume on file to screen.", code="no_resume"
        )

    provider = get_llm_provider()
    run = ScreeningRun(
        organization_id=organization_id,
        application_id=application_id,
        requested_by_user_id=requested_by_user_id,
        status=ScreeningStatus.PENDING,
        provider=provider.name,
        model=provider.model,
    )
    db.add(run)
    await db.flush()

    try:
        storage = get_resume_storage_for_provider(application.resume.storage_provider)
        resume_bytes = await storage.read(application.resume.storage_path)
        resume_text = extract_resume_text(
            content=resume_bytes, filename=application.resume.original_filename
        )
        verdict = await provider.screen_candidate(
            resume_text=resume_text,
            job_title=application.job.title,
            job_description=application.job.description,
        )
    except (AIProviderError, StorageError) as exc:
        _record_provider(run, provider)
        run.status = ScreeningStatus.FAILED
        run.error_message = str(exc)
        await db.flush()
        return run

    _record_provider(run, provider)
    # Audit trail for a fallback chain: a COMPLETED run whose primary
    # provider failed keeps that (sanitized) failure here.
    failed = getattr(provider, "failed_attempts", ())
    if failed:
        run.error_message = "Fallback used after: " + "; ".join(
            f"{f.provider} ({f.model}): {f.error}" for f in failed
        )
    run.status = ScreeningStatus.COMPLETED
    run.overall_score = verdict.overall_score
    run.recommendation = verdict.recommendation
    run.summary = verdict.summary
    run.matching_skills = verdict.matching_skills
    run.missing_skills = verdict.missing_skills
    run.strengths = verdict.strengths
    run.concerns = verdict.concerns
    run.experience_assessment = verdict.experience_assessment
    run.education_assessment = verdict.education_assessment
    run.decision = ScreeningDecision(verdict.decision) if verdict.decision else None
    run.matched_requirements = verdict.matched_requirements
    run.missing_requirements = verdict.missing_requirements
    run.completed_at = datetime.now(UTC)
    await db.flush()
    return run


def _record_provider(run: ScreeningRun, provider: LLMProvider) -> None:
    """A fallback chain's `name`/`model` move to whichever provider
    produced (or last failed) the result — record that one, not the
    provider the PENDING row was created with."""
    run.provider = provider.name
    run.model = provider.model


#: `created_at` is the transaction's `now()`, so two runs in one transaction
#: tie on it; `completed_at` is set in application code and always advances
#: (same reasoning as match_engine.get_latest_match).
NEWEST_FIRST = (
    ScreeningRun.created_at.desc(),
    func.coalesce(ScreeningRun.completed_at, ScreeningRun.created_at).desc(),
)


async def list_screening_runs(
    db: AsyncSession, application_id: uuid.UUID
) -> list[ScreeningRun]:
    result = await db.execute(
        select(ScreeningRun)
        .where(ScreeningRun.application_id == application_id)
        .order_by(*NEWEST_FIRST)
    )
    return list(result.scalars().all())
