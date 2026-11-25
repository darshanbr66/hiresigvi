import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.resume import Resume


class ApplicationStatus(StrEnum):
    """The full transition table lives in
    app/workflows/application_workflow.py, not here — this enum is only the
    vocabulary (CLAUDE.md § 2: "Workflow state != ad hoc strings").
    """

    APPLIED = "APPLIED"
    # Set only by the system, right after the submission-time AI screening
    # judged the resume a NOT_MATCH for the job. Advisory, never final: an
    # HR user can override it back into review (with a recorded reason) or
    # confirm it as REJECTED. Distinct from REJECTED on purpose.
    AI_SCREENED_OUT = "AI_SCREENED_OUT"
    UNDER_REVIEW = "UNDER_REVIEW"
    SCREENING = "SCREENING"
    ASSESSMENT_INVITED = "ASSESSMENT_INVITED"
    ASSESSMENT_STARTED = "ASSESSMENT_STARTED"
    ASSESSMENT_COMPLETED = "ASSESSMENT_COMPLETED"
    SHORTLISTED = "SHORTLISTED"
    INTERVIEW = "INTERVIEW"
    SELECTED = "SELECTED"
    REJECTED = "REJECTED"
    HIRED = "HIRED"


class ApplicationSource(StrEnum):
    PORTAL = "PORTAL"
    RECRUITER_ADDED = "RECRUITER_ADDED"
    CAMPUS_IMPORT = "CAMPUS_IMPORT"
    REFERRAL = "REFERRAL"
    OTHER = "OTHER"
    # Created by an HR user matching an existing candidate to another job
    # (application_service.match_candidate_to_job) — never by the candidate,
    # who self-applies at most once per reapply window. The original PORTAL
    # application stays untouched alongside it.
    HR_MATCH = "HR_MATCH"


class ApplicationEducationStatus(StrEnum):
    """The academic-document check a self-service application was submitted
    with (app/services/application_draft_service.py). NULL = none was made:
    HR-created/imported/matched applications, programs, and every
    application from before the check existed."""

    #: The AI found the document consistent with the selected qualification
    #: and the job's requirements — advisory, never proof of authenticity.
    VERIFIED = "VERIFIED"
    #: The check could not reach a confident answer (unclear document, or
    #: the AI was unavailable) and the candidate continued: HR must review
    #: the document.
    MANUAL_REVIEW = "MANUAL_REVIEW"


class Application(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """The relationship between one Candidate and one Job — carries all
    workflow state (CLAUDE.md § 2: "Candidate != Application"). `resume` is
    reached via the Resume model's `application_id` FK (app/models/
    resume.py); `campus_drive_id` links a campus-sourced application to its
    CampusDrive (docs/campus-hiring.md § 2) and is null for ordinary
    recruitment applications.

    Unique on `(candidate_id, job_id)`: one application per candidate per
    job. Re-applying after REJECTED is explicitly an open product question
    per docs/database.md § 3.5 ("not finalized") — not implemented here.
    """

    __tablename__ = "applications"
    __table_args__ = (
        Index("uq_applications_candidate_job", "candidate_id", "job_id", unique=True),
        # The reapply-window check: one candidate's self-service applications.
        Index(
            "ix_applications_candidate_self_service",
            "candidate_id",
            "applied_at",
            postgresql_where="is_self_service",
        ),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    campus_drive_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("campus_drives.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, name="application_status", native_enum=True),
        nullable=False,
        default=ApplicationStatus.APPLIED,
        server_default=ApplicationStatus.APPLIED.value,
    )
    source: Mapped[ApplicationSource] = mapped_column(
        Enum(ApplicationSource, name="application_source", native_enum=True),
        nullable=False,
        default=ApplicationSource.RECRUITER_ADDED,
        server_default=ApplicationSource.RECRUITER_ADDED.value,
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # True only for an application the candidate submitted themselves through
    # the email-verified careers-site / campus-drive-link flow. `source` alone
    # can't say this (staff may create an application with any source), and
    # the 3-month self-apply rule (app/services/reapply_service.py) counts
    # only these.
    is_self_service: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    education_verification_status: Mapped[ApplicationEducationStatus | None] = mapped_column(
        Enum(ApplicationEducationStatus, name="application_education_status", native_enum=True),
        nullable=True,
    )
    #: The job's qualification requirements were ambiguous (or the AI was
    #: unavailable) when this was submitted: the general qualification list
    #: was offered, and HR must check eligibility against the JD.
    eligibility_review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: The Education step's three selections as submitted through the
    #: progressive apply flow — qualification (app/services/education/
    #: catalog.py) and specialization category + specialization (…/
    #: specializations.py) keys, kept per application because the Candidate
    #: profile is shared across applications. NULL for applications created
    #: any other way, or before these were recorded.
    education_qualification_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    education_specialization_category: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    education_specialization: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: What the eligibility check decided at submission (app/services/
    #: application_draft_service.py `EligibilityReview`): the JD's fields of
    #: study, whether they were mandatory, the field the specialization
    #: matched, and the reasons HR was asked to check eligibility. NULL for
    #: applications created any other way, or before it was recorded.
    education_eligibility: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Soft delete — same rationale/shape as Candidate's (app/models/candidate.py).
    # A hard DELETE here would CASCADE away AssessmentInvitation/
    # ApplicationStatusHistory/Note rows, destroying exactly the recruitment/
    # audit history CLAUDE.md says must never silently disappear. Deleted
    # applications are excluded from `application_service.list_applications`
    # but remain reachable by id with every linked record intact.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deletion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Read-only convenience relationships — populated via eager loading in
    # app/services/application_service.py so list/detail responses can
    # include the candidate's name and the job's title without a separate
    # round trip per row.
    candidate: Mapped[Candidate] = relationship(Candidate, lazy="raise")
    job: Mapped[Job] = relationship(Job, lazy="raise")
    resume: Mapped[Resume | None] = relationship(Resume, lazy="raise", uselist=False, viewonly=True)


class ApplicationStatusHistory(UUIDPrimaryKeyMixin, Base):
    """Append-only audit trail of every status change on an Application — see
    docs/recruitment-workflow.md § 1. No `organization_id` of its own;
    tenancy is derived through `application_id -> applications.organization_id`,
    same indirect-RLS pattern as `user_roles` (see app/db/rls.py).
    """

    __tablename__ = "application_status_history"

    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[ApplicationStatus | None] = mapped_column(
        Enum(
            ApplicationStatus, name="application_status", native_enum=True, create_type=False
        ),
        nullable=True,
    )
    to_status: Mapped[ApplicationStatus] = mapped_column(
        Enum(
            ApplicationStatus, name="application_status", native_enum=True, create_type=False
        ),
        nullable=False,
    )
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
