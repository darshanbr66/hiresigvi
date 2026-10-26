"""Recruitment-program workflow rules — docs/programs.md § 4.

Per CLAUDE.md § 2 ("Workflow state != ad hoc strings") every program state
dimension has its legal-transition table here, in one place, and the
program-specific gates on the platform-wide application pipeline live here
too (`check_application_transition`, called by
application_service.change_status for every status change):

* an application can't move past review until HR found the candidate
  eligible;
* with a selection test configured, a candidate can't be shortlisted or
  interviewed without a final *passing* result on their latest attempt;
* a selection decision needs the interview recorded as completed;
* HIRED (joined full-time employment) needs an accepted offer.

Nothing here makes a decision on its own: every change is an explicit HR
action, and nothing is ever inferred from passing a test, receiving a
scholarship or completing training.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.models.application import Application, ApplicationStatus
from app.models.assessment import AssessmentInvitation, AssessmentResult, ResultGradingStatus
from app.models.program import (
    AgreementStatus,
    EligibilityStatus,
    OfferStatus,
    ProgramEnrollment,
    ProgramStatus,
    RecruitmentProgram,
    ScholarshipEligibility,
    ScholarshipStatus,
    TrainingStatus,
)

PROGRAM_STATUS_TRANSITIONS: dict[ProgramStatus, frozenset[ProgramStatus]] = {
    ProgramStatus.DRAFT: frozenset({ProgramStatus.PUBLISHED, ProgramStatus.ARCHIVED}),
    ProgramStatus.PUBLISHED: frozenset({ProgramStatus.PAUSED, ProgramStatus.CLOSED}),
    ProgramStatus.PAUSED: frozenset({ProgramStatus.PUBLISHED, ProgramStatus.CLOSED}),
    ProgramStatus.CLOSED: frozenset({ProgramStatus.PUBLISHED, ProgramStatus.ARCHIVED}),
    ProgramStatus.ARCHIVED: frozenset(),
}

ELIGIBILITY_TRANSITIONS: dict[EligibilityStatus, frozenset[EligibilityStatus]] = {
    EligibilityStatus.PENDING: frozenset({EligibilityStatus.ELIGIBLE, EligibilityStatus.NOT_ELIGIBLE}),
    EligibilityStatus.ELIGIBLE: frozenset({EligibilityStatus.NOT_ELIGIBLE}),
    EligibilityStatus.NOT_ELIGIBLE: frozenset({EligibilityStatus.ELIGIBLE}),
}

SCHOLARSHIP_ELIGIBILITY_TRANSITIONS: dict[ScholarshipEligibility, frozenset[ScholarshipEligibility]] = {
    ScholarshipEligibility.NOT_DETERMINED: frozenset(
        {ScholarshipEligibility.ELIGIBLE, ScholarshipEligibility.NOT_ELIGIBLE}
    ),
    ScholarshipEligibility.ELIGIBLE: frozenset({ScholarshipEligibility.NOT_ELIGIBLE}),
    ScholarshipEligibility.NOT_ELIGIBLE: frozenset({ScholarshipEligibility.ELIGIBLE}),
}

SCHOLARSHIP_STATUS_TRANSITIONS: dict[ScholarshipStatus, frozenset[ScholarshipStatus]] = {
    ScholarshipStatus.NOT_STARTED: frozenset({ScholarshipStatus.PENDING}),
    ScholarshipStatus.PENDING: frozenset(
        {ScholarshipStatus.APPROVED, ScholarshipStatus.DECLINED, ScholarshipStatus.NOT_STARTED}
    ),
    ScholarshipStatus.APPROVED: frozenset(),
    ScholarshipStatus.DECLINED: frozenset({ScholarshipStatus.PENDING}),
}

TRAINING_TRANSITIONS: dict[TrainingStatus, frozenset[TrainingStatus]] = {
    TrainingStatus.NOT_ENROLLED: frozenset({TrainingStatus.ENROLLED}),
    TrainingStatus.ENROLLED: frozenset({TrainingStatus.IN_PROGRESS, TrainingStatus.DISCONTINUED}),
    TrainingStatus.IN_PROGRESS: frozenset({TrainingStatus.COMPLETED, TrainingStatus.DISCONTINUED}),
    TrainingStatus.COMPLETED: frozenset(),
    TrainingStatus.DISCONTINUED: frozenset(),
}

OFFER_TRANSITIONS: dict[OfferStatus, frozenset[OfferStatus]] = {
    OfferStatus.NOT_OFFERED: frozenset({OfferStatus.OFFERED}),
    OfferStatus.OFFERED: frozenset({OfferStatus.ACCEPTED, OfferStatus.DECLINED, OfferStatus.WITHDRAWN}),
    OfferStatus.ACCEPTED: frozenset({OfferStatus.WITHDRAWN}),
    OfferStatus.DECLINED: frozenset(),
    OfferStatus.WITHDRAWN: frozenset(),
}

AGREEMENT_TRANSITIONS: dict[AgreementStatus, frozenset[AgreementStatus]] = {
    AgreementStatus.NOT_REQUIRED: frozenset({AgreementStatus.PENDING}),
    AgreementStatus.PENDING: frozenset({AgreementStatus.SENT, AgreementStatus.VOID}),
    AgreementStatus.SENT: frozenset(
        {AgreementStatus.SIGNED, AgreementStatus.DECLINED, AgreementStatus.VOID}
    ),
    AgreementStatus.SIGNED: frozenset({AgreementStatus.VOID}),
    AgreementStatus.DECLINED: frozenset({AgreementStatus.PENDING}),
    AgreementStatus.VOID: frozenset({AgreementStatus.PENDING}),
}

#: Application statuses that mean "selected through the program's process".
SELECTED_STATUSES = frozenset({ApplicationStatus.SELECTED, ApplicationStatus.HIRED})

#: Application statuses still at the review stage (before eligibility gating).
REVIEW_STATUSES = frozenset(
    {ApplicationStatus.APPLIED, ApplicationStatus.AI_SCREENED_OUT, ApplicationStatus.UNDER_REVIEW}
)

_NEEDS_ELIGIBILITY = frozenset(
    {
        ApplicationStatus.SCREENING,
        ApplicationStatus.ASSESSMENT_INVITED,
        ApplicationStatus.SHORTLISTED,
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.SELECTED,
        ApplicationStatus.HIRED,
    }
)
_NEEDS_PASSED_TEST = frozenset({ApplicationStatus.SHORTLISTED, ApplicationStatus.INTERVIEW})


def require_transition[S](table: dict[S, frozenset[S]], current: S, target: S, *, what: str) -> None:
    if target not in table[current]:
        raise ConflictError(
            f"The {what} can't move from {str(current).replace('_', ' ').lower()} "
            f"to {str(target).replace('_', ' ').lower()}.",
            code="invalid_program_transition",
        )


async def latest_result(db: AsyncSession, application_id: object) -> AssessmentResult | None:
    """The result of the application's most recent attempt (None if it has
    no submitted attempt)."""
    invitation = await db.scalar(
        select(AssessmentInvitation)
        .where(AssessmentInvitation.application_id == application_id)
        .order_by(AssessmentInvitation.attempt_number.desc())
        .limit(1)
    )
    if invitation is None:
        return None
    return await db.scalar(
        select(AssessmentResult).where(AssessmentResult.invitation_id == invitation.id)
    )


async def check_application_transition(
    db: AsyncSession, application: Application, to_status: ApplicationStatus
) -> None:
    """Program-specific gates on a platform-wide status change. A no-op for
    applications outside any program. REJECTED is always allowed."""
    enrollment = await db.scalar(
        select(ProgramEnrollment).where(ProgramEnrollment.application_id == application.id)
    )
    if enrollment is None or to_status == ApplicationStatus.REJECTED:
        return
    program = await db.get(RecruitmentProgram, enrollment.program_id)
    assert program is not None

    if to_status in _NEEDS_ELIGIBILITY and enrollment.eligibility_status != EligibilityStatus.ELIGIBLE:
        raise ConflictError(
            "Complete the program eligibility review (mark the candidate eligible) before "
            "moving this application forward.",
            code="program_eligibility_required",
        )
    if to_status in _NEEDS_PASSED_TEST and program.assessment_id is not None:
        result = await latest_result(db, application.id)
        if (
            result is None
            or result.grading_status != ResultGradingStatus.FINAL
            or result.passed is not True
        ):
            raise ConflictError(
                "This program requires a passing result on its selection test before the "
                "candidate can be shortlisted or interviewed.",
                code="program_test_pass_required",
            )
    if to_status == ApplicationStatus.SELECTED and enrollment.interview_completed_at is None:
        raise ConflictError(
            "Record the interview as completed before making a selection decision.",
            code="program_interview_required",
        )
    if to_status == ApplicationStatus.HIRED and enrollment.offer_status != OfferStatus.ACCEPTED:
        raise ConflictError(
            "A program participant can be marked as joined only after accepting a "
            "full-time employment offer.",
            code="program_offer_required",
        )


def derive_stage(enrollment: ProgramEnrollment, application: Application) -> str:
    """One readable HR-facing stage label for lists (the detailed state is
    on the individual fields)."""
    status = application.status
    if status == ApplicationStatus.REJECTED:
        return "Not selected"
    if enrollment.joined_on is not None or status == ApplicationStatus.HIRED:
        return "Joined full-time"
    if enrollment.offer_status in (OfferStatus.OFFERED, OfferStatus.ACCEPTED):
        return f"Employment offer {enrollment.offer_status.value.lower()}"
    if enrollment.training_status != TrainingStatus.NOT_ENROLLED:
        return f"Training {enrollment.training_status.value.replace('_', ' ').lower()}"
    if enrollment.scholarship_status in (
        ScholarshipStatus.PENDING,
        ScholarshipStatus.APPROVED,
        ScholarshipStatus.DECLINED,
    ):
        return f"Scholarship {enrollment.scholarship_status.value.lower()}"
    if status == ApplicationStatus.SELECTED:
        return "Selected"
    if status == ApplicationStatus.INTERVIEW:
        return "Interview completed" if enrollment.interview_completed_at else "Interview scheduled"
    if status == ApplicationStatus.SHORTLISTED:
        return "Test passed — awaiting interview"
    if status == ApplicationStatus.ASSESSMENT_COMPLETED:
        return "Test submitted"
    if status in (ApplicationStatus.ASSESSMENT_INVITED, ApplicationStatus.ASSESSMENT_STARTED):
        return "Assessment pending"
    if enrollment.eligibility_status == EligibilityStatus.NOT_ELIGIBLE:
        return "Not eligible"
    if enrollment.eligibility_status == EligibilityStatus.ELIGIBLE:
        return "Eligible"
    return "Eligibility review"
