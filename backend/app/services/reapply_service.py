"""The self-service reapply rule: a candidate may self-apply again once
`CANDIDATE_REAPPLY_COOLDOWN_MONTHS` (default 3) calendar months have passed
since their previous *self-service* application — or earlier, if an HR user
grants reapply permission, which clears those previous self-service
applications (`grant_reapply_permission`). Single-use CandidateReapplyGrant
rows issued by the earlier "Allow Reapply" action are still honoured until
spent.

Only self-service applications count (`Application.is_self_service`): a
candidate HR created, imported or matched to jobs has no self-apply history
and is never locked out by it. Every application counts, whatever its
status (an AI_SCREENED_OUT one included) and even if archived — the window
is about when the person last applied, not how it went.

Callers pass the organization explicitly and every query filters on it, on
top of RLS (the two-layer tenant rule, docs/security.md § 2).
"""

import calendar
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.db.rls import set_tenant_context
from app.integrations.storage import get_resume_storage_for_provider
from app.models.application import Application
from app.models.candidate import Candidate
from app.models.candidate_reapply_grant import CandidateReapplyGrant
from app.models.candidate_resume_profile import CandidateResumeProfile
from app.models.resume import Resume
from app.models.resume_chunk import ResumeChunk
from app.models.user import User
from app.services import activity_service

logger = logging.getLogger(__name__)


def add_months(moment: datetime, months: int) -> datetime:
    """Same day-of-month `months` later, clamped to the last day of a
    shorter month (30 Nov + 3 months = 28/29 Feb)."""
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


@dataclass(frozen=True)
class ReapplyEligibility:
    """`last_self_applied_at` is None for a candidate with no self-service
    history. `eligible_from` is when the window opens (None = no history).
    `allowed` is the answer; `grant` is the open HR grant, if any."""

    allowed: bool
    last_self_applied_at: datetime | None
    eligible_from: datetime | None
    grant: CandidateReapplyGrant | None


async def last_self_application_at(
    db: AsyncSession, *, organization_id: uuid.UUID, candidate_id: uuid.UUID
) -> datetime | None:
    return await db.scalar(
        select(func.max(Application.applied_at)).where(
            Application.organization_id == organization_id,
            Application.candidate_id == candidate_id,
            Application.is_self_service.is_(True),
        )
    )


async def get_open_grant(
    db: AsyncSession, *, organization_id: uuid.UUID, candidate_id: uuid.UUID
) -> CandidateReapplyGrant | None:
    return await db.scalar(
        select(CandidateReapplyGrant).where(
            CandidateReapplyGrant.organization_id == organization_id,
            CandidateReapplyGrant.candidate_id == candidate_id,
            CandidateReapplyGrant.used_at.is_(None),
        )
    )


async def check_eligibility(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    candidate_id: uuid.UUID,
    now: datetime | None = None,
) -> ReapplyEligibility:
    now = now or datetime.now(UTC)
    last = await last_self_application_at(
        db, organization_id=organization_id, candidate_id=candidate_id
    )
    grant = await get_open_grant(db, organization_id=organization_id, candidate_id=candidate_id)
    if last is None:
        return ReapplyEligibility(
            allowed=True, last_self_applied_at=None, eligible_from=None, grant=grant
        )
    eligible_from = add_months(last, get_settings().candidate_reapply_cooldown_months)
    return ReapplyEligibility(
        allowed=now >= eligible_from or grant is not None,
        last_self_applied_at=last,
        eligible_from=eligible_from,
        grant=grant,
    )


def _format_day(value: date) -> str:
    return f"{value.day} {value.strftime('%B %Y')}"


def reapply_locked_error(
    eligibility: ReapplyEligibility, contact_email: str | None
) -> ConflictError:
    """Only ever raised to a caller who has just proven they own the
    candidate's email address, so naming their own dates is safe."""
    assert eligibility.last_self_applied_at is not None
    assert eligibility.eligible_from is not None
    months = get_settings().candidate_reapply_cooldown_months
    contact = (
        f" If your circumstances have changed, you can contact our recruitment team at "
        f"{contact_email}."
        if contact_email
        else ""
    )
    return ConflictError(
        f"You applied on {_format_day(eligibility.last_self_applied_at.date())}. Candidates can "
        f"apply again {months} months after their previous application, so you can apply again "
        f"from {_format_day(eligibility.eligible_from.date())}. Our recruitment team continues "
        f"to consider your profile for other suitable roles in the meantime.{contact}",
        code="reapply_locked",
        data={
            "last_applied_at": eligibility.last_self_applied_at.isoformat(),
            "eligible_from": eligibility.eligible_from.isoformat(),
        },
    )


#: Search results are for picking one person, not browsing the pipeline.
REAPPLY_SEARCH_LIMIT = 20


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search_candidates(
    db: AsyncSession, *, organization_id: uuid.UUID, query: str
) -> list[Candidate]:
    """HR's reapply-permission lookup: active candidates whose name or email
    contains `query` (case-insensitive), or whose mobile number contains its
    digits — so "+91 98765 43210", "98765-43210" and "9876543210" all find
    the stored "+919876543210"."""
    if len(query.strip()) < 2:  # e.g. only whitespace: never "match everyone"
        return []
    term = _escape_like(query.strip())
    conditions: list[ColumnElement[bool]] = [
        Candidate.full_name.ilike(f"%{term}%", escape="\\"),
        Candidate.email.ilike(f"%{term}%", escape="\\"),
    ]
    digits = re.sub(r"\D", "", query)
    if len(digits) >= 4:
        conditions.append(
            func.regexp_replace(Candidate.phone, r"\D", "", "g").contains(digits, autoescape=True)
        )
    result = await db.scalars(
        select(Candidate)
        .where(
            Candidate.organization_id == organization_id,
            Candidate.deleted_at.is_(None),
            or_(*conditions),
        )
        .order_by(Candidate.full_name)
        .limit(REAPPLY_SEARCH_LIMIT)
    )
    return list(result.all())


@dataclass(frozen=True)
class ReapplyPermissionResult:
    candidate: Candidate
    cleared_application_count: int


async def grant_reapply_permission(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    candidate_id: uuid.UUID,
    actor: User,
) -> ReapplyPermissionResult:
    """HR "Grant Reapply Permission": clears the candidate's previous
    *self-service* applications so the same email and mobile number can
    apply again from scratch (verification, resume, AI screening and the
    reapply window all start over).

    Removed, for each self-service application: the application and, through
    the database's own ON DELETE CASCADE, its status history, AI screening
    runs, assessment invitations (with their answers, monitoring events and
    notifications) and the notes on it; its resume rows, and each stored
    resume file no remaining resume row still points at; and the candidate's
    derived resume profile if it was built from one of those resumes (it is
    recomputed on demand).

    Kept: the candidate profile; every HR-created or HR-matched application,
    with any resume file it shares (whose embeddings move onto the surviving
    row); calendar events and match results (their link to the removed
    application is cleared by the FK's SET NULL); earlier reapply grants;
    and the whole Activity log, plus a new entry recording who granted this.

    The database work is one transaction, committed here, and stored files
    are only removed once that commit has succeeded — a failure part-way
    leaves the rows and their files exactly as they were. A file that can't
    be removed afterwards is logged rather than failing the grant: the rows
    are already gone and the candidate can apply."""
    candidate = await db.get(Candidate, candidate_id, with_for_update=True)
    if (
        candidate is None
        or candidate.deleted_at is not None
        or candidate.organization_id != organization_id
    ):
        raise NotFoundError("Candidate not found.")

    eligibility = await check_eligibility(
        db, organization_id=organization_id, candidate_id=candidate_id
    )
    if eligibility.allowed:
        raise ConflictError(
            "This candidate can already apply again — there is no reapply restriction to lift.",
            code="reapply_not_restricted",
        )

    application_ids = list(
        await db.scalars(
            select(Application.id).where(
                Application.organization_id == organization_id,
                Application.candidate_id == candidate_id,
                Application.is_self_service.is_(True),
            )
        )
    )
    resumes = list(
        await db.scalars(
            select(Resume).where(
                Resume.organization_id == organization_id,
                Resume.application_id.in_(application_ids),
            )
        )
    )
    removed_resume_ids = [resume.id for resume in resumes]
    files_to_delete: list[tuple[str, str]] = []
    for resume in resumes:
        survivor_id = await db.scalar(
            select(Resume.id)
            .where(
                Resume.organization_id == organization_id,
                Resume.storage_provider == resume.storage_provider,
                Resume.storage_path == resume.storage_path,
                Resume.id.not_in(removed_resume_ids),
            )
            .order_by(Resume.created_at)
            .limit(1)
        )
        if survivor_id is None:
            files_to_delete.append((resume.storage_provider, resume.storage_path))
            continue
        # An HR job match shares this file and was never chunked itself
        # (resume_service.link_existing_resume): hand it the embeddings
        # rather than letting the cascade drop them.
        await db.execute(
            update(ResumeChunk)
            .where(ResumeChunk.resume_id == resume.id)
            .values(resume_id=survivor_id)
        )

    await db.execute(
        delete(CandidateResumeProfile).where(
            CandidateResumeProfile.candidate_id == candidate_id,
            CandidateResumeProfile.source_resume_id.in_(removed_resume_ids),
        )
    )
    await db.execute(delete(Resume).where(Resume.id.in_(removed_resume_ids)))
    await db.execute(
        delete(Application).where(
            Application.organization_id == organization_id,
            Application.id.in_(application_ids),
        )
    )

    count = len(application_ids)
    await activity_service.record_activity(
        db,
        organization_id=organization_id,
        actor=actor,
        action="CANDIDATE_REAPPLY_PERMISSION_GRANTED",
        entity_type="candidate",
        entity_id=candidate.id,
        entity_label=f"{candidate.full_name} ({candidate.email})",
        description=(
            f"{candidate.full_name} was given permission to apply again. Their previous "
            f"{count} self-service application{'s' if count != 1 else ''} and related data "
            "were cleared."
        ),
    )
    await db.commit()
    # RLS tenant context is transaction-scoped (SET LOCAL): re-apply it for
    # anything the request still reads after this commit.
    await set_tenant_context(db, organization_id)

    for provider, storage_path in dict.fromkeys(files_to_delete):
        try:
            # Re-checked after the commit: an HR job match committed in the
            # meantime may have linked a new resume row to this same file
            # (resume_service.link_existing_resume).
            if await db.scalar(
                select(Resume.id)
                .where(
                    Resume.organization_id == organization_id,
                    Resume.storage_provider == provider,
                    Resume.storage_path == storage_path,
                )
                .limit(1)
            ):
                continue
            await get_resume_storage_for_provider(provider).delete(storage_path)
        except Exception:
            logger.exception(
                "Could not remove a cleared resume file",
                extra={"extra_fields": {"candidate_id": str(candidate_id)}},
            )

    return ReapplyPermissionResult(candidate=candidate, cleared_application_count=count)


async def consume_open_grant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    candidate: Candidate,
    application_id: uuid.UUID,
) -> None:
    """Called for every new self-service application: an open grant is
    spent by the candidate's next self-application whether or not it was
    needed, so it can never linger and allow a second early one later."""
    grant = await get_open_grant(db, organization_id=organization_id, candidate_id=candidate.id)
    if grant is None:
        return
    grant.used_at = datetime.now(UTC)
    grant.used_by_application_id = application_id
    await db.flush()
    await activity_service.record_activity(
        db,
        organization_id=organization_id,
        actor=None,
        action="CANDIDATE_REAPPLY_GRANT_USED",
        entity_type="candidate",
        entity_id=candidate.id,
        entity_label=f"{candidate.full_name} ({candidate.email})",
        description=f"{candidate.full_name} used the early reapply HR had granted.",
    )
