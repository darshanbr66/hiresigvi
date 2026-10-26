"""Request/response schemas for the internal AI ("Recruitment Intelligence")
endpoints — app/api/v1/recruiter/internal_ai.py. Structured card shapes
(candidates/jobs/matches), not just a prose message, so the frontend (Phase
B) can render candidate/job/match cards rather than parsing text.

Every response that carries a match score also carries `disclaimer`
(app/models/match_result.py::AI_MATCH_DISCLAIMER) — the human-in-the-loop
requirement baked into the wire format, not left to UI copy alone.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.match_result import AI_MATCH_DISCLAIMER

_MAX_MESSAGE_CHARS = 1000
_MAX_HISTORY_MESSAGES = 10
_MAX_HISTORY_MESSAGE_CHARS = 4000


class InternalAIHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=_MAX_HISTORY_MESSAGE_CHARS)


class InternalAIQueryContext(BaseModel):
    """What the caller already knows — e.g. the recruiter is viewing this
    candidate's or this job's page. Short-circuits intent/entity resolution
    entirely when present (app/services/internal_ai/retrieval_planner.py)."""

    candidate_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    application_id: uuid.UUID | None = None
    #: For an explicit "compare these candidates" call from a UI that already
    #: has a candidate multi-select (Phase B) — free-text comparison without
    #: this falls back to name resolution in intent_detection.py.
    candidate_ids: list[uuid.UUID] = Field(default_factory=list)


class InternalAIQueryRequest(BaseModel):
    message: str = Field(min_length=1, max_length=_MAX_MESSAGE_CHARS)
    conversation_id: uuid.UUID | None = None
    history: list[InternalAIHistoryMessage] = Field(
        default_factory=list, max_length=_MAX_HISTORY_MESSAGES
    )
    context: InternalAIQueryContext | None = None


class CandidateCard(BaseModel):
    id: uuid.UUID
    full_name: str
    current_title: str | None = None
    current_company: str | None = None
    location: str | None = None
    years_experience: int | None = None
    notice_period_days: int | None = None
    application_status: str | None = None


class JobCard(BaseModel):
    id: uuid.UUID
    title: str
    department: str | None = None
    location: str | None = None
    status: str


class MatchCard(BaseModel):
    match_id: uuid.UUID | None = None
    candidate_id: uuid.UUID
    candidate_name: str
    job_id: uuid.UUID
    job_title: str
    status: str
    overall_match_score: int | None = None
    confidence: str | None = None
    matching_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    role_alignment: str | None = None
    explanation: str | None = None
    potential_concerns: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    disclaimer: str = AI_MATCH_DISCLAIMER


class TopJobStat(BaseModel):
    title: str
    application_count: int


class PipelineStats(BaseModel):
    """Org-wide pipeline counts for a PIPELINE_INTELLIGENCE query — computed
    from persisted applications/jobs (retrieval_planner._pipeline_stats).
    Typed so clients get a documented shape instead of an opaque dict."""

    # ApplicationStatus value -> count; only statuses with >=1 application.
    applications_by_status: dict[str, int] = Field(default_factory=dict)
    open_jobs: int = 0
    top_jobs_by_applications: list[TopJobStat] = Field(default_factory=list)


class InternalAIQueryResponse(BaseModel):
    conversation_id: uuid.UUID
    message: str
    candidates: list[CandidateCard] = Field(default_factory=list)
    jobs: list[JobCard] = Field(default_factory=list)
    matches: list[MatchCard] = Field(default_factory=list)
    pipeline_stats: PipelineStats | None = None
    disclaimer: str = AI_MATCH_DISCLAIMER


class MatchRequest(BaseModel):
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    application_id: uuid.UUID | None = None


class JobMatchRequest(BaseModel):
    """Job-wide: rank every applicant (or every candidate, if `candidates_
    only=False` is added later) against one job."""

    job_id: uuid.UUID
    limit: int = Field(default=10, ge=1, le=50)


class MatchResponse(BaseModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    application_id: uuid.UUID | None
    status: str
    provider: str
    model: str
    overall_match_score: int | None
    confidence: str | None
    matching_skills: list[str]
    missing_skills: list[str]
    matching_experience: dict[str, Any] | None
    matching_education: dict[str, Any] | None
    matching_location: dict[str, Any] | None
    notice_period_fit: dict[str, Any] | None
    role_alignment: str | None
    potential_concerns: list[str]
    evidence: list[dict[str, Any]]
    explanation: str | None
    scoring_breakdown: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    disclaimer: str = AI_MATCH_DISCLAIMER
