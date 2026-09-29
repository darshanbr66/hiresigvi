import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.models.activity import Activity
from app.models.application import Application
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.screening import ScreeningRun
from app.models.user import User
from app.schemas.candidate import (
    CandidateCreateRequest,
    CandidateProfileFields,
    CandidateUpdateRequest,
)
from app.schemas.candidate_history import (
    CandidateApplicationHistory,
    CandidateHistoryResponse,
    CandidateTimelineEntry,
)
from app.schemas.screening import ScreeningRunResponse
from app.services import activity_service, screening_service


def _identity_conflict(exc: IntegrityError) -> ConflictError:
    """Maps a unique/check violation on `candidates` to a clear 409 — which
    identity key collided is safe to say to staff (they can see every
    candidate in their organization anyway)."""
    detail = str(exc.orig)
    if "uq_candidates_org_phone" in detail:
        return ConflictError(
            "A candidate with this mobile number already exists in this organization."
        )
    if "ck_candidates_verified_identity_complete" in detail:
        return ConflictError(
            "This candidate verified their email through the careers site: mobile number, "
            "date of birth, place of birth and languages cannot be removed."
        )
    return ConflictError("A candidate with this email already exists in this organization.")


async def create_candidate(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    payload: CandidateCreateRequest,
    actor: User | None = None,
) -> Candidate:
    # Look before inserting. A unique-violation on flush leaves the session
    # unable to run any further query, so the collision has to be found
    # *before* it happens for the response to name the existing profile —
    # which is what lets staff add the resume/role there instead of hunting
    # for it (and is why a duplicate is never created).
    by_email, by_phone = await find_identity_matches(
        db, organization_id=organization_id, email=payload.email, phone=payload.phone
    )
    existing = by_email or by_phone
    if existing is not None:
        conflict = (
            ConflictError(
                "A candidate with this mobile number already exists in this organization."
            )
            if by_email is None
            else ConflictError(
                "A candidate with this email already exists in this organization."
            )
        )
        # A soft-deleted profile still owns the email/mobile, but pointing
        # staff at a deleted record would be a dead end.
        if existing.deleted_at is None:
            conflict.data = {"existing_candidate_id": str(existing.id)}
        raise conflict

    profile = payload.model_dump(include=set(CandidateProfileFields.model_fields))
    profile["languages"] = profile.get("languages") or []
    candidate = Candidate(
        organization_id=organization_id,
        email=payload.email,
        full_name=payload.full_name,
        phone=payload.phone,
        source=payload.source,
        **profile,
    )
    db.add(candidate)
    try:
        await db.flush()
    except IntegrityError as exc:
        # Only reachable by losing a race with a concurrent create: the
        # pre-check above catches every ordinary collision. A failed flush
        # deactivates the whole session transaction (a SAVEPOINT does not
        # contain it), so nothing may be queried here — the request ends on
        # this 409 and `get_db` rolls back.
        raise _identity_conflict(exc) from exc

    await activity_service.record_activity(
        db,
        organization_id=organization_id,
        actor=actor,
        action="CANDIDATE_CREATED",
        entity_type="candidate",
        entity_id=candidate.id,
        entity_label=f"{candidate.full_name} ({candidate.email})",
        description=f"Candidate {candidate.full_name} was added.",
    )
    return candidate


async def get_candidate_by_email(
    db: AsyncSession, *, organization_id: uuid.UUID, email: str
) -> Candidate | None:
    result = await db.execute(
        select(Candidate).where(
            Candidate.organization_id == organization_id, Candidate.email == email
        )
    )
    return result.scalar_one_or_none()


async def find_identity_matches(
    db: AsyncSession, *, organization_id: uuid.UUID, email: str, phone: str | None
) -> tuple[Candidate | None, Candidate | None]:
    """The candidates (soft-deleted included) owning this email
    (case-insensitively) and this normalized mobile number — separately, so
    the self-service flow can tell "the same person returning" (email match,
    mobile theirs or new) from "someone else's mobile number"."""
    by_email = await db.scalar(
        select(Candidate).where(
            Candidate.organization_id == organization_id,
            func.lower(Candidate.email) == email.lower(),
        )
    )
    by_phone = None
    if phone:
        by_phone = await db.scalar(
            select(Candidate).where(
                Candidate.organization_id == organization_id, Candidate.phone == phone
            )
        )
    return by_email, by_phone


async def list_candidates(db: AsyncSession, organization_id: uuid.UUID) -> list[Candidate]:
    result = await db.execute(
        select(Candidate)
        .where(Candidate.organization_id == organization_id, Candidate.deleted_at.is_(None))
        .order_by(Candidate.created_at.desc())
    )
    return list(result.scalars().all())


async def get_candidate(db: AsyncSession, candidate_id: uuid.UUID) -> Candidate | None:
    """RLS scopes this to the caller's own tenant — a cross-tenant id
    returns None exactly as if the row didn't exist (docs/security.md § 2)."""
    return await db.get(Candidate, candidate_id)


async def update_candidate(
    db: AsyncSession, candidate: Candidate, payload: CandidateUpdateRequest, *, actor: User
) -> Candidate:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return candidate
    if "languages" in updates and updates["languages"] is None:
        updates["languages"] = []

    for field, value in updates.items():
        setattr(candidate, field, value)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        raise _identity_conflict(exc) from exc

    await activity_service.record_activity(
        db,
        organization_id=candidate.organization_id,
        actor=actor,
        action="CANDIDATE_UPDATED",
        entity_type="candidate",
        entity_id=candidate.id,
        entity_label=f"{candidate.full_name} ({candidate.email})",
        description=f"Candidate {candidate.full_name} was updated.",
    )
    return candidate


async def delete_candidate(
    db: AsyncSession, candidate: Candidate, *, actor: User, reason: str
) -> Candidate:
    """Soft-deletes: keeps the row (and every Application/Note/
    AssessmentInvitation pointing at it) but removes it from
    `list_candidates` and records an Activity, per CLAUDE.md's "never
    silently destroy historical recruitment records" rule."""
    if candidate.deleted_at is not None:
        raise ConflictError("This candidate has already been deleted.")

    candidate.deleted_at = datetime.now(UTC)
    candidate.deleted_by_user_id = actor.id
    candidate.deletion_reason = reason
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=candidate.organization_id,
        actor=actor,
        action="CANDIDATE_DELETED",
        entity_type="candidate",
        entity_id=candidate.id,
        entity_label=f"{candidate.full_name} ({candidate.email})",
        description=f"Candidate {candidate.full_name} was deleted.",
        reason=reason,
    )
    return candidate


#: The candidate-journey audit actions shown on the candidate page — a
#: deliberately narrow slice of the organization's audit log (which itself
#: stays ORG_ADMIN-only), scoped to this one candidate and their applications.
CANDIDATE_TIMELINE_ACTIONS: frozenset[str] = frozenset(
    {
        "CANDIDATE_CREATED",
        "CANDIDATE_REGISTERED",
        "CANDIDATE_EMAIL_VERIFIED",
        "CANDIDATE_UPDATED",
        "CANDIDATE_MATCHED_TO_JOB",
        "CANDIDATE_REAPPLIED",
        "CANDIDATE_REAPPLY_PERMISSION_GRANTED",
        # Written by the earlier single-use "Allow Reapply" grant; kept so
        # existing history still shows.
        "CANDIDATE_REAPPLY_GRANTED",
        "CANDIDATE_REAPPLY_GRANT_USED",
        "CANDIDATE_RESUME_ADDED",
        "DUPLICATE_APPLICATION_BLOCKED",
        "SELF_REAPPLY_BLOCKED",
        "APPLICATION_CREATED",
        "APPLICATION_STATUS_CHANGED",
        "AI_SCREENING_COMPLETED",
        "AI_SCREENING_FAILED",
        "AI_SCREENED_OUT",
        "AI_SCREENING_OVERRIDDEN",
        "CANDIDATE_WELCOME_EMAIL_SENT",
        "CANDIDATE_WELCOME_EMAIL_FAILED",
    }
)


async def get_candidate_history(
    db: AsyncSession, candidate: Candidate
) -> CandidateHistoryResponse:
    """Three set-based queries (applications+jobs, screening runs, timeline),
    no per-application round trips."""
    app_rows = (
        await db.execute(
            select(Application, Job.title)
            .join(Job, Job.id == Application.job_id)
            .where(Application.candidate_id == candidate.id)
            .order_by(Application.applied_at, Application.id)
        )
    ).all()
    applications = [row[0] for row in app_rows]
    application_ids = [a.id for a in applications]

    runs_by_application: dict[uuid.UUID, list[ScreeningRunResponse]] = {}
    if application_ids:
        runs = (
            await db.execute(
                select(ScreeningRun)
                .where(ScreeningRun.application_id.in_(application_ids))
                .order_by(*screening_service.NEWEST_FIRST)
            )
        ).scalars()
        for run in runs:
            runs_by_application.setdefault(run.application_id, []).append(
                ScreeningRunResponse.model_validate(run)
            )

    # The first self-service application is the candidate's "original" one
    # (later self-service ones are reapplies).
    original_id = next((a.id for a in applications if a.is_self_service), None)
    history = [
        CandidateApplicationHistory(
            application_id=application.id,
            job_id=application.job_id,
            job_title=job_title,
            source=application.source,
            status=application.status,
            applied_at=application.applied_at,
            is_original=application.id == original_id,
            is_self_service=application.is_self_service,
            deleted_at=application.deleted_at,
            screenings=runs_by_application.get(application.id, []),
        )
        for application, job_title in app_rows
    ]

    entity_filter = (Activity.entity_type == "candidate") & (Activity.entity_id == candidate.id)
    if application_ids:
        entity_filter = entity_filter | (
            (Activity.entity_type == "application") & Activity.entity_id.in_(application_ids)
        )
    activities = (
        await db.execute(
            select(Activity)
            .where(
                Activity.organization_id == candidate.organization_id,
                Activity.action.in_(CANDIDATE_TIMELINE_ACTIONS),
                entity_filter,
            )
            .order_by(Activity.created_at.desc(), Activity.id)
            .limit(200)
        )
    ).scalars()
    return CandidateHistoryResponse(
        candidate_id=candidate.id,
        applications=history,
        timeline=[CandidateTimelineEntry.model_validate(a) for a in activities],
    )
