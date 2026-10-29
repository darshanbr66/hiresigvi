"""Import every model module here so `Base.metadata` is fully populated for
Alembic autogenerate and for `Base.metadata.create_all()` in tests.
"""

from app.models.activity import Activity
from app.models.admin_message import AdminConversation, AdminMessage
from app.models.application import (
    Application,
    ApplicationSource,
    ApplicationStatus,
    ApplicationStatusHistory,
)
from app.models.assessment import (
    Assessment,
    AssessmentInvitation,
    AssessmentMonitoringEvent,
    AssessmentResult,
    AssessmentStatus,
    CandidateAnswer,
    InvitationStatus,
    MonitoringEventType,
    Question,
    QuestionAnswerKey,
    QuestionOption,
    QuestionType,
)
from app.models.calendar_event import (
    CalendarEvent,
    CalendarEventAttendee,
    CalendarEventStatus,
    CalendarEventType,
)
from app.models.campus_drive import CampusDrive, CampusDriveStatus
from app.models.candidate import Candidate, CandidateSource, EducationLevel
from app.models.candidate_reapply_grant import CandidateReapplyGrant
from app.models.candidate_resume_profile import CandidateResumeProfile
from app.models.email_verification import EmailVerification, EmailVerificationPurpose
from app.models.institution import (
    AffiliationStatus,
    AffiliationVerificationMethod,
    CampusAffiliationVerification,
    CampusDriveRosterEntry,
    Institution,
)
from app.models.interview import ApplicationInterview, InterviewOutcome, InterviewStatus
from app.models.job import Job, JobStatus
from app.models.job_requirement import JobRequirement, RequirementCategory
from app.models.match_result import AI_MATCH_DISCLAIMER, MatchResult, MatchStatus
from app.models.note import Note, NoteVisibility
from app.models.notification import Notification, NotificationType
from app.models.organization import Organization
from app.models.program import (
    ProgramEnrollment,
    ProgramFeePayment,
    ProgramFundingRevision,
    ProgramStatus,
    RecruitmentProgram,
    RecruitmentProgramInstitution,
    ScholarshipDisbursement,
    TrainingMilestone,
)
from app.models.question_bank import (
    QuestionBankAnswerKey,
    QuestionBankItem,
    QuestionBankItemTag,
    QuestionBankItemVersion,
    QuestionCategory,
    QuestionTag,
)
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.resume import Resume
from app.models.resume_chunk import EMBEDDING_DIMENSIONS, ResumeChunk
from app.models.screening import ScreeningDecision, ScreeningRun, ScreeningStatus
from app.models.team_hierarchy import Department, Employee, EmploymentStatus
from app.models.user import User, UserRefreshToken

__all__ = [
    "Activity",
    "Organization",
    "User",
    "UserRefreshToken",
    "Role",
    "Permission",
    "RolePermission",
    "UserRole",
    "Candidate",
    "CandidateSource",
    "Job",
    "JobStatus",
    "Application",
    "ApplicationSource",
    "ApplicationStatus",
    "ApplicationStatusHistory",
    "Resume",
    "ScreeningRun",
    "ScreeningStatus",
    "ScreeningDecision",
    "EmailVerification",
    "EmailVerificationPurpose",
    "Assessment",
    "Question",
    "QuestionType",
    "QuestionOption",
    "AssessmentInvitation",
    "InvitationStatus",
    "CandidateAnswer",
    "AssessmentResult",
    "AssessmentMonitoringEvent",
    "MonitoringEventType",
    "CampusDrive",
    "CampusDriveStatus",
    "Note",
    "NoteVisibility",
    "Notification",
    "NotificationType",
    "Department",
    "Employee",
    "EmploymentStatus",
    "ResumeChunk",
    "EMBEDDING_DIMENSIONS",
    "JobRequirement",
    "RequirementCategory",
    "CandidateResumeProfile",
    "MatchResult",
    "MatchStatus",
    "AI_MATCH_DISCLAIMER",
    "CalendarEvent",
    "CalendarEventAttendee",
    "CalendarEventType",
    "CalendarEventStatus",
    "CandidateReapplyGrant",
    "AdminConversation",
    "AdminMessage",
    "AssessmentStatus",
    "QuestionAnswerKey",
    "EducationLevel",
    "Institution",
    "AffiliationStatus",
    "AffiliationVerificationMethod",
    "CampusAffiliationVerification",
    "CampusDriveRosterEntry",
    "QuestionCategory",
    "QuestionTag",
    "QuestionBankItem",
    "QuestionBankItemTag",
    "QuestionBankItemVersion",
    "QuestionBankAnswerKey",
    "RecruitmentProgram",
    "RecruitmentProgramInstitution",
    "ProgramEnrollment",
    "ProgramStatus",
    "ScholarshipDisbursement",
    "ProgramFeePayment",
    "ProgramFundingRevision",
    "TrainingMilestone",
    "ApplicationInterview",
    "InterviewStatus",
    "InterviewOutcome",
]
