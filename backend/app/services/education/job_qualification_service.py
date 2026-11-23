"""A job's qualification requirements, derived from the job's own JD.

The JD (description + eligibility criteria) is the only source of
eligibility. Jobs have no structured qualification fields, so the AI reads
the text once per JD version and maps it onto the qualification vocabulary
(catalog.py); the result is cached in `job_qualification_profiles` keyed by
a hash of that text, and recomputed only when the recruiter changes it.

What the AI says is normalized deterministically here, and the rules are
enforced in code, not trusted from the model:

* unknown keys are dropped; nothing below the stated minimum level is ever
  offered;
* "related higher qualifications" are derived from the vocabulary's
  families when the JD doesn't restrict to exactly the listed degrees;
* the job is *restricted* only when the JD clearly **requires** a
  qualification (not merely "preferred") with enough confidence. An
  ambiguous JD, a "preferred" one, or an AI outage never invents strict
  rules: the job is treated as unrestricted (every qualification offered),
  and the academic-document check still runs.
"""

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.ai import AIProviderError, get_education_ai_provider
from app.integrations.ai.education import JobQualificationAnalysis, VocabularyEntry
from app.models.job import Job
from app.models.job_qualification import JobQualificationProfile, JobQualificationStatus
from app.services.education import catalog
from app.services.education.catalog import CATALOG, LEVEL_LABELS, QualificationLevel

logger = get_logger(__name__)

#: Bump when the normalization rules or prompt change, so every cached
#: profile is recomputed under the new rules.
RULES_VERSION = "2"

#: Below this, a JD's requirement is too ambiguous to restrict on.
MIN_RESTRICT_CONFIDENCE = 0.6

#: Families whose degrees name a branch/subject the candidate must give.
BRANCH_REQUIRED_FAMILIES = frozenset({"ENGINEERING", "SCIENCE", "ANY"})


def vocabulary() -> list[VocabularyEntry]:
    return [
        VocabularyEntry(key=o.key, label=o.label, level=o.level.value, family=o.family)
        for o in CATALOG
    ]


def source_text(job: Job) -> str:
    parts = [f"Title: {job.title}", job.description or ""]
    if job.eligibility_criteria:
        parts.append(f"Eligibility criteria: {job.eligibility_criteria}")
    return "\n\n".join(parts)


def source_hash(job: Job) -> str:
    return hashlib.sha256(f"{RULES_VERSION}\n{source_text(job)}".encode()).hexdigest()


@dataclass(frozen=True)
class QualificationRequirements:
    """What the apply flow offers for one job, at one JD version."""

    source_hash: str
    #: True = only `eligible_keys` may be chosen (the JD clearly requires
    #: them). False = every qualification is offered.
    restricted: bool
    eligible_keys: list[str]
    minimum_level: str | None = None
    fields_of_study: list[str] = field(default_factory=list)
    field_mandatory: bool = False
    #: REQUIRED / PREFERRED / NOT_STATED, or UNAVAILABLE (AI not reachable).
    strength: str = "NOT_STATED"
    confidence: float | None = None
    evidence: str = ""
    #: The requirements couldn't be determined with confidence — the AI was
    #: unavailable, or the JD states a required qualification that couldn't
    #: be resolved. The general list is offered (no invented rule) and the
    #: submitted application is flagged for HR to check eligibility. A JD
    #: that simply states no qualification, or only a preferred one, is not
    #: ambiguous and isn't flagged.
    needs_review: bool = False

    @property
    def summary(self) -> str:
        """Candidate-facing, composed here from structured fields."""
        if not self.restricted:
            return "Select your highest qualification."
        labels = catalog.describe_options(self.eligible_keys)
        text = ""
        if self.minimum_level:
            text = f"Minimum qualification: {level_label(self.minimum_level)}. "
        text += f"Eligible qualifications for this role: {labels}."
        if self.field_mandatory and self.fields_of_study:
            text += " Required field: " + " or ".join(self.fields_of_study) + "."
        return text

    def allows(self, key: str) -> bool:
        return key in self.eligible_keys

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "QualificationRequirements":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _all_keys() -> list[str]:
    return [o.key for o in CATALOG]


def unrestricted(hash_: str, *, strength: str = "NOT_STATED") -> QualificationRequirements:
    return QualificationRequirements(
        source_hash=hash_,
        restricted=False,
        eligible_keys=_all_keys(),
        strength=strength,
        needs_review=strength == "UNAVAILABLE",
    )


def normalize(analysis: JobQualificationAnalysis, hash_: str) -> QualificationRequirements:
    """The deterministic rules on top of the AI's reading (module docstring)."""
    accepted = {
        key
        for key in analysis.accepted_qualification_keys
        if key in catalog.BY_KEY and not catalog.BY_KEY[key].is_other
    }
    minimum = QualificationLevel(analysis.minimum_level) if analysis.minimum_level else None
    if minimum is None and accepted:
        minimum = min((catalog.BY_KEY[k].level for k in accepted), key=catalog.rank)

    if minimum is not None:
        floor = catalog.rank(minimum)
        accepted = {k for k in accepted if catalog.rank(catalog.BY_KEY[k].level) >= floor}
    if accepted and analysis.higher_qualifications_accepted:
        accepted |= catalog.related_higher_keys(accepted)
    if not accepted and minimum is not None:
        # "Any bachelor's degree" — restricted by level only.
        accepted = {
            o.key for o in CATALOG if catalog.rank(o.level) >= catalog.rank(minimum)
        }

    clear_requirement = (
        analysis.determinable
        and analysis.requirement_strength == "REQUIRED"
        and analysis.confidence >= MIN_RESTRICT_CONFIDENCE
        and bool(accepted)
    )
    if not clear_requirement:
        return QualificationRequirements(
            source_hash=hash_,
            restricted=False,
            eligible_keys=_all_keys(),
            minimum_level=minimum.value if minimum else None,
            fields_of_study=analysis.fields_of_study,
            field_mandatory=False,
            strength=analysis.requirement_strength,
            confidence=analysis.confidence,
            evidence=analysis.evidence,
            # A stated requirement that couldn't be resolved confidently.
            needs_review=analysis.determinable and analysis.requirement_strength == "REQUIRED",
        )
    ordered = [o.key for o in CATALOG if o.key in accepted]
    return QualificationRequirements(
        source_hash=hash_,
        restricted=True,
        eligible_keys=ordered,
        minimum_level=minimum.value if minimum else None,
        fields_of_study=analysis.fields_of_study,
        field_mandatory=analysis.field_mandatory and bool(analysis.fields_of_study),
        strength=analysis.requirement_strength,
        confidence=analysis.confidence,
        evidence=analysis.evidence,
    )


async def get_requirements(db: AsyncSession, job: Job) -> QualificationRequirements:
    """The cached requirements for the job's current JD, computing them
    (one AI call) when the JD changed or was never analyzed. Never raises
    for an AI problem: an unreachable AI yields an unrestricted result,
    cached briefly so a burst of candidates doesn't hammer it."""
    hash_ = source_hash(job)
    row = await db.scalar(
        select(JobQualificationProfile).where(JobQualificationProfile.job_id == job.id)
    )
    now = datetime.now(UTC)
    if row is not None and row.source_hash == hash_:
        if row.status == JobQualificationStatus.READY and row.requirements is not None:
            return QualificationRequirements.from_json(row.requirements)
        retry_after = timedelta(minutes=get_settings().job_qualification_retry_minutes)
        if now - row.analyzed_at < retry_after:
            return unrestricted(hash_, strength="UNAVAILABLE")

    provider = get_education_ai_provider()
    try:
        analysis = await provider.analyze_job_qualifications(
            job_title=job.title, job_text=source_text(job), vocabulary=vocabulary()
        )
    except AIProviderError as exc:
        logger.warning(
            "Job qualification analysis unavailable",
            extra={"extra_fields": {"job_id": str(job.id), "error_type": type(exc).__name__}},
        )
        await _store(
            db, job, hash_, JobQualificationStatus.UNAVAILABLE, None, provider, str(exc)[:500]
        )
        return unrestricted(hash_, strength="UNAVAILABLE")

    requirements = normalize(analysis, hash_)
    await _store(db, job, hash_, JobQualificationStatus.READY, requirements, provider, None)
    return requirements


async def _store(
    db: AsyncSession,
    job: Job,
    hash_: str,
    status: JobQualificationStatus,
    requirements: QualificationRequirements | None,
    provider: Any,
    error: str | None,
) -> None:
    values = {
        "organization_id": job.organization_id,
        "job_id": job.id,
        "source_hash": hash_,
        "status": status,
        "requirements": requirements.to_json() if requirements else None,
        "provider": getattr(provider, "name", None),
        "model": getattr(provider, "model", None),
        "error_message": error,
        "analyzed_at": datetime.now(UTC),
    }
    statement = insert(JobQualificationProfile).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[JobQualificationProfile.job_id],
        set_={k: statement.excluded[k] for k in values if k not in ("organization_id", "job_id")}
        | {"updated_at": datetime.now(UTC)},
    )
    await db.execute(statement)


def level_label(level: str | None) -> str:
    if level is None:
        return ""
    try:
        return LEVEL_LABELS[QualificationLevel(level)]
    except ValueError:
        return ""
