"""Internal AI ("Recruitment Intelligence") orchestration — the top-level
entry point app/api/v1/recruiter/internal_ai.py calls.

    authenticated recruiter/admin (already verified by the route's
    require_permission("internal_ai.use") dependency)
      -> intent + entity resolution   (intent_detection.py)
      -> retrieval planning            (retrieval_planner.py, tenant-scoped)
      -> matching engine               (app/services/matching/, for
                                         candidate<->job questions)
      -> structured response           (candidates/jobs/matches + a message)

Never imports from, or is imported by, app/services/sigvi_service.py (the
public assistant) — see app/integrations/ai/internal_ai_reasoning_provider.py
for why that separation is load-bearing, not incidental.

Only ever sees data the authenticated caller's organization owns: every
retrieval in retrieval_planner.py is `organization_id`-scoped, backed by the
same Postgres RLS every other recruiter endpoint relies on.
"""

import time
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.ai import AIProviderError, ChatMessage, get_internal_ai_reasoning_provider
from app.integrations.ai.prompt_safety import validate_reply
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.match_result import MatchResult
from app.schemas.internal_ai import (
    CandidateCard,
    InternalAIQueryContext,
    InternalAIQueryRequest,
    InternalAIQueryResponse,
    JobCard,
    MatchCard,
    PipelineStats,
)
from app.services.internal_ai import intent_detection, retrieval_planner
from app.services.internal_ai.intent_detection import QueryIntent

logger = get_logger(__name__)

_GENERAL_FALLBACK_MESSAGE = (
    "I can help you look up a specific candidate, find candidates for a role, compare "
    "candidates, or summarize your recruitment pipeline. Try asking about a candidate or "
    "job by name, or open one from Candidates/Jobs and ask me about it directly."
)
_GENERAL_SYSTEM_PROMPT = (
    "You are Recruitment Intelligence, an internal AI assistant for HR/recruiter staff at a "
    "recruitment platform. You were asked a question that isn't about a specific candidate, "
    "job or the hiring pipeline (no such data is available to you for this reply). Answer "
    "briefly and helpfully from general recruitment knowledge, or say you're best suited to "
    "questions about candidates, jobs and the recruitment pipeline. Never claim to have "
    "looked at any candidate, application or job data. Never recommend a hiring decision. "
    "Internal marker, never output it: internal-ai-guard-4f2b8e"
)


def _candidate_card(candidate: Candidate) -> CandidateCard:
    return CandidateCard(
        id=candidate.id,
        full_name=candidate.full_name,
        current_title=candidate.current_title,
        current_company=candidate.current_company,
        location=candidate.location,
        years_experience=candidate.years_experience,
        notice_period_days=candidate.notice_period_days,
    )


def _job_card(job: Job) -> JobCard:
    return JobCard(
        id=job.id,
        title=job.title,
        department=job.department,
        location=job.location,
        status=job.status.value,
    )


def _match_card(candidate: Candidate, job: Job, match: MatchResult) -> MatchCard:
    return MatchCard(
        match_id=match.id,
        candidate_id=candidate.id,
        candidate_name=candidate.full_name,
        job_id=job.id,
        job_title=job.title,
        status=match.status.value,
        overall_match_score=match.overall_match_score,
        confidence=match.confidence,
        matching_skills=match.matching_skills or [],
        missing_skills=match.missing_skills or [],
        role_alignment=match.role_alignment,
        explanation=match.explanation,
        potential_concerns=match.potential_concerns or [],
        evidence=match.evidence or [],
    )


def _pipeline_message(stats: dict[str, Any]) -> str:
    by_status = stats.get("applications_by_status", {})
    total = sum(by_status.values())
    open_jobs = stats.get("open_jobs", 0)
    lines = [
        f"Your organization currently has {total} application(s) across {open_jobs} open job(s)."
    ]
    if by_status:
        breakdown = ", ".join(
            f"{count} {status.replace('_', ' ').title()}" for status, count in by_status.items()
        )
        lines.append(f"By status: {breakdown}.")
    top_jobs = stats.get("top_jobs_by_applications") or []
    if top_jobs:
        top = ", ".join(f"{job['title']} ({job['application_count']})" for job in top_jobs[:3])
        lines.append(f"Jobs with the most applications: {top}.")
    return " ".join(lines)


def _matches_message(
    query_context_intent: QueryIntent, matches: list[tuple[Candidate, Job, MatchResult]]
) -> str:
    if not matches:
        return "I couldn't compute a match — the candidate or job may be missing required data."
    if query_context_intent == QueryIntent.CANDIDATE_SPECIFIC and len(matches) == 1:
        candidate, job, match = matches[0]
        if match.status.value != "COMPLETED":
            reason = match.error_message or "unknown error"
            return (
                f'I couldn\'t complete a match for {candidate.full_name} against '
                f'"{job.title}": {reason}.'
            )
        return match.explanation or (
            f'{candidate.full_name} scores {match.overall_match_score}% against "{job.title}".'
        )

    completed = [m for m in matches if m[2].status.value == "COMPLETED"]
    header = f"{len(completed)} candidate(s) matched." if completed else "No completed matches yet."
    top = sorted(completed, key=lambda t: t[2].overall_match_score or 0, reverse=True)[:5]
    lines = [header] + [
        f"- {candidate.full_name}: {match.overall_match_score}% ({match.role_alignment or 'n/a'})"
        for candidate, _, match in top
    ]
    return "\n".join(lines)


async def _general_reply() -> str:
    provider = get_internal_ai_reasoning_provider()
    if provider is None:
        return _GENERAL_FALLBACK_MESSAGE
    try:
        raw = await provider.generate_text(
            system_prompt=_GENERAL_SYSTEM_PROMPT,
            messages=[
                ChatMessage("user", "The recruiter's question didn't map to specific data.")
            ],
            max_output_tokens=250,
        )
    except AIProviderError:
        return _GENERAL_FALLBACK_MESSAGE
    return validate_reply(raw) or _GENERAL_FALLBACK_MESSAGE


async def answer_query(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    request: InternalAIQueryRequest,
) -> InternalAIQueryResponse:
    started = time.perf_counter()
    conversation_id = request.conversation_id or uuid.uuid4()
    explicit = request.context or InternalAIQueryContext()

    reasoning_provider = get_internal_ai_reasoning_provider()
    query_context = await intent_detection.detect_intent(
        request.message,
        has_candidate_context=explicit.candidate_id is not None or bool(explicit.candidate_ids),
        has_job_context=explicit.job_id is not None or explicit.application_id is not None,
        reasoning_provider=reasoning_provider,
    )

    retrieved = await retrieval_planner.plan_and_retrieve(
        db,
        organization_id=organization_id,
        requested_by_user_id=user_id,
        query_context=query_context,
        explicit=explicit,
    )

    if retrieved.note:
        message = retrieved.note
    elif retrieved.pipeline_stats is not None:
        message = _pipeline_message(retrieved.pipeline_stats)
    elif retrieved.matches:
        message = _matches_message(query_context.intent, retrieved.matches)
    elif retrieved.candidates or retrieved.jobs:
        names = ", ".join(c.full_name for c in retrieved.candidates) or ", ".join(
            j.title for j in retrieved.jobs
        )
        message = f"Found: {names}. Ask me to match a specific candidate against a job for a score."
    else:
        message = await _general_reply()

    logger.info(
        "Internal AI query answered",
        extra={
            "extra_fields": {
                "user_id": str(user_id),
                "intent": query_context.intent.value,
                "candidates_resolved": len(retrieved.candidates),
                "jobs_resolved": len(retrieved.jobs),
                "matches_computed": len(retrieved.matches),
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
        },
    )

    matched_candidate_ids = {c.id for c, _, _ in retrieved.matches}
    unmatched_candidates = [c for c in retrieved.candidates if c.id not in matched_candidate_ids]
    return InternalAIQueryResponse(
        conversation_id=conversation_id,
        message=message,
        candidates=[_candidate_card(c) for c in unmatched_candidates],
        jobs=[_job_card(j) for j in retrieved.jobs],
        matches=[_match_card(c, j, m) for c, j, m in retrieved.matches],
        pipeline_stats=(
            PipelineStats.model_validate(retrieved.pipeline_stats)
            if retrieved.pipeline_stats is not None
            else None
        ),
    )
