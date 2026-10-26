import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class CandidateResumeProfile(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """The structured signal deterministically extracted from a Candidate's
    most recent resume + existing profile fields (app/services/matching/
    candidate_profile_extraction.py) — the matching engine's normalized
    candidate side, mirroring `JobRequirement` on the job side. One row per
    Candidate (`candidate_id` unique); recomputed and replaced in place
    whenever the candidate's active resume changes.

    Deliberately its own table, not new columns on `Candidate`
    (app/models/candidate.py) — this is a *derived, re-computable* AI
    artifact, not reusable profile data a recruiter edits directly
    (CLAUDE.md § 2: "AI != Candidate" applies here in spirit even though
    this isn't a screening verdict: never merge derived/AI-produced fields
    onto the source-of-truth Candidate row).
    """

    __tablename__ = "candidate_resume_profiles"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        # A unique *constraint* (uq_…), as migration c4d5e6f7a8b9 created it.
        unique=True,
    )
    source_resume_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True
    )
    # Skill labels matched against the same taxonomy requirement_extraction
    # uses on the job side, so the two sides can be compared directly.
    skills: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    total_experience_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Free-text education mentions found in the resume (e.g. "B.Tech Computer
    # Science", "MBA") — matched loosely against a job requirement's
    # `raw_source_text`, not a controlled vocabulary (education phrasing
    # varies too much to taxonomy-match reliably at this stage).
    education: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notice_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Word count of the resume text this profile was extracted from (0 if no
    # resume/no text at all) — an evidence-substance signal independent of
    # whether any given category happens to be "informative". A near-empty
    # resume that coincidentally lists exactly the required skills must not
    # score the same as a detailed resume backing up the same skills
    # (app/services/matching/deterministic_scorer.py's evidence-density
    # factor reads this). Nullable only for rows written before this column
    # existed; extraction always sets it going forward.
    resume_word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
