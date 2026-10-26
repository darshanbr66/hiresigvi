"""Public-job context for Sigvi.

Jobs come from the existing services — the same OPEN-only, tenant-scoped path
the anonymous careers page uses (app/api/v1/public/jobs.py) — and are reduced
immediately to the fields that page already shows publicly. The description is
included only when the recruiter left it visible. Nothing else about a job
(creator, status history, applications, notes) is ever read here."""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_tenant_context
from app.knowledge.keyword_retriever import tokenize
from app.models.job import EMPLOYMENT_TYPE_LABELS, JobStatus
from app.schemas.sigvi import ChatHistoryMessage, ChatJobCard
from app.services import job_service, organization_service

MAX_JOBS_IN_PROMPT = 10
_PROMPT_DESCRIPTION_CHARS = 500
_CARD_SUMMARY_CHARS = 140
MAX_JOB_CARDS = 3

# Words that mean the visitor is asking about openings. Deliberately not "work"
# / "apply" — "how does the assessment work" and "how do I apply" are answered
# by platform knowledge and must not trigger a jobs lookup.
_JOB_INTENT_TERMS = frozenset(
    "job jobs role roles opening openings position positions vacancy vacancies hiring hire "
    "hires career careers opportunity opportunities internship internships posting postings".split()
)


@dataclass(frozen=True)
class PublicJob:
    id: uuid.UUID
    title: str
    department: str | None
    location: str | None
    employment_type: str | None
    openings_count: int
    #: None when the recruiter hid the description from the public page.
    description: str | None


async def load_public_jobs(db: AsyncSession, slug: str) -> list[PublicJob]:
    organization = await organization_service.get_organization_by_slug(db, slug)
    if organization is None:
        return []
    await set_tenant_context(db, organization.id)
    jobs = await job_service.list_jobs(db, organization.id, status=JobStatus.OPEN)
    return [
        PublicJob(
            id=job.id,
            title=job.title,
            department=job.department,
            location=job.location,
            employment_type=(
                EMPLOYMENT_TYPE_LABELS[job.employment_type] if job.employment_type else None
            ),
            openings_count=job.openings_count,
            description=job.description if job.description_visible else None,
        )
        for job in jobs
    ]


def needs_job_context(message: str, history: Sequence[ChatHistoryMessage]) -> bool:
    """True when the visitor is asking about openings — in this message, or in
    the recent conversation (so "which one requires React?" still gets jobs)."""
    recent = [message, *(turn.content for turn in history[-4:])]
    return any(tokenize(text) & _JOB_INTENT_TERMS for text in recent)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    text = _collapse(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut[: cut.rfind(" ")].rstrip(" ,.;:") + "…" if " " in cut else cut + "…"


def select_relevant_jobs(jobs: Sequence[PublicJob], query: str) -> list[PublicJob]:
    """At most `MAX_JOBS_IN_PROMPT`, the best lexical matches first (newest
    first among equals, which is the order `jobs` arrives in)."""
    query_tokens = tokenize(query) - _JOB_INTENT_TERMS

    def score(job: PublicJob) -> int:
        head = tokenize(" ".join(filter(None, [job.title, job.department, job.location])))
        body = tokenize(job.description or "")
        return 3 * len(query_tokens & head) + len(query_tokens & body)

    ranked = sorted(jobs, key=score, reverse=True)  # stable: keeps newest-first on ties
    return ranked[:MAX_JOBS_IN_PROMPT]


def _clean(text: str) -> str:
    return _collapse(text.replace("<", "(").replace(">", ")"))


def format_jobs_block(shown: Sequence[PublicJob], total: int) -> str:
    """The <open_jobs> reference block for the system prompt."""
    if total == 0:
        return '<open_jobs count="0">There are currently no open public roles.</open_jobs>'

    lines = []
    for index, job in enumerate(shown, start=1):
        facts = [f"Title: {_clean(job.title)}"]
        if job.department:
            facts.append(f"Department: {_clean(job.department)}")
        if job.location:
            facts.append(f"Location: {_clean(job.location)}")
        if job.employment_type:
            facts.append(f"Type: {_clean(job.employment_type)}")
        facts.append(f"Openings: {job.openings_count}")
        description = (
            _truncate(_clean(job.description), _PROMPT_DESCRIPTION_CHARS)
            if job.description
            else "(not published)"
        )
        lines.append(f"{index}. {' | '.join(facts)}\n   Description: {description}")

    header = f'<open_jobs count="{total}" shown="{len(shown)}">'
    return header + "\n" + "\n".join(lines) + "\n</open_jobs>"


def find_mentioned_jobs(reply: str, jobs: Sequence[PublicJob], slug: str) -> list[ChatJobCard]:
    """Cards for jobs the reply actually names, by exact title, in the order
    they appear. Deterministic grounding: a card can only ever be a real open
    job the model was given."""
    # Whole-word matches only ("patent engineering" is not "Patent Engineer"),
    # and a title that only appears inside a longer matched title ("Patent
    # Engineer" within "Senior Patent Engineer") is the same mention, not a
    # second job.
    spans: list[tuple[int, int, PublicJob]] = []
    for job in jobs:
        title = job.title.strip()
        if not title:
            continue
        pattern = rf"(?<![A-Za-z0-9]){re.escape(title)}(?![A-Za-z0-9])"
        spans.extend((m.start(), m.end(), job) for m in re.finditer(pattern, reply, re.IGNORECASE))

    def _inside_longer(start: int, end: int) -> bool:
        return any(s <= start and end <= e and (e - s) > (end - start) for s, e, _ in spans)

    first_mention: dict[uuid.UUID, tuple[int, PublicJob]] = {}
    for start, end, job in sorted(spans, key=lambda span: span[0]):
        if job.id not in first_mention and not _inside_longer(start, end):
            first_mention[job.id] = (start, job)
    hits = sorted(first_mention.values(), key=lambda item: item[0])

    cards: list[ChatJobCard] = []
    for _, job in hits[:MAX_JOB_CARDS]:
        path = f"/org/{slug}/jobs/{job.id}"
        summary = _truncate(job.description, _CARD_SUMMARY_CHARS) if job.description else None
        cards.append(
            ChatJobCard(
                id=job.id,
                title=job.title,
                department=job.department,
                location=job.location,
                employment_type=job.employment_type,
                summary=summary,
                organization_slug=slug,
                view_path=path,
                apply_path=f"{path}#apply",
            )
        )
    return cards
