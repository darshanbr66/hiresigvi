import { lazy, Suspense, type ComponentType } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { LoginPage } from "../features/auth/LoginPage";
import { PublicHomePage } from "../features/public/PublicHomePage";
import { SigviLayer } from "../features/sigvi/SigviLayer";
import { AppReady, HoldBrandLoading } from "../shared/components/BrandLoading";
import { RouteFallback } from "../shared/components/RouteFallback";
import { ProtectedRoute } from "./ProtectedRoute";

/**
 * Route-level code splitting. The public home page (the first thing most
 * visitors see) and the sign-in page ship in the main bundle; every other
 * screen is its own chunk, fetched when its route is first visited. A
 * candidate opening the landing page therefore never downloads the recruiter
 * app, and the recruiter shell/pages load in parallel with sign-in.
 */
function page<T extends Record<string, ComponentType>>(
  load: () => Promise<T>,
  name: keyof T & string,
) {
  return lazy(async () => ({ default: (await load())[name] }));
}

const CandidatePlaceholderPage = page(() => import("../features/candidate/CandidatePlaceholderPage"), "CandidatePlaceholderPage");
const AssessmentTakingPage = page(() => import("../features/careers/AssessmentTakingPage"), "AssessmentTakingPage");
const CampusDriveApplyPage = page(() => import("../features/careers/CampusDriveApplyPage"), "CampusDriveApplyPage");
const ProgramApplyPage = page(() => import("../features/careers/ProgramApplyPage"), "ProgramApplyPage");
const CareersPage = page(() => import("../features/careers/CareersPage"), "CareersPage");
const JobDetailPage = page(() => import("../features/careers/JobDetailPage"), "JobDetailPage");
const OrganizationsPage = page(() => import("../features/admin/organizations/OrganizationsPage"), "OrganizationsPage");
const ActivitiesPage = page(() => import("../features/recruiter/activities/ActivitiesPage"), "ActivitiesPage");
const AiWorkspacePage = page(() => import("../features/recruiter/ai/AiWorkspacePage"), "AiWorkspacePage");
const ApplicationDetailPage = page(() => import("../features/recruiter/applications/ApplicationDetailPage"), "ApplicationDetailPage");
const ApplicationsPage = page(() => import("../features/recruiter/applications/ApplicationsPage"), "ApplicationsPage");
const AssessmentDetailPage = page(() => import("../features/recruiter/assessments/AssessmentDetailPage"), "AssessmentDetailPage");
const AssessmentsPage = page(() => import("../features/recruiter/assessments/AssessmentsPage"), "AssessmentsPage");
const AttemptReviewPage = page(() => import("../features/recruiter/assessments/AttemptReviewPage"), "AttemptReviewPage");
const InstitutionsPage = page(() => import("../features/recruiter/institutions/InstitutionsPage"), "InstitutionsPage");
const ProgramDetailPage = page(() => import("../features/recruiter/programs/ProgramDetailPage"), "ProgramDetailPage");
const ProgramsPage = page(() => import("../features/recruiter/programs/ProgramsPage"), "ProgramsPage");
const QuestionBankPage = page(() => import("../features/recruiter/questionBank/QuestionBankPage"), "QuestionBankPage");
const CalendarPage = page(() => import("../features/recruiter/calendar/CalendarPage"), "CalendarPage");
const TalkToAdminPage = page(() => import("../features/recruiter/adminMessages/TalkToAdminPage"), "TalkToAdminPage");
const CampusDriveDetailPage = page(() => import("../features/recruiter/campusDrives/CampusDriveDetailPage"), "CampusDriveDetailPage");
const CampusDrivesPage = page(() => import("../features/recruiter/campusDrives/CampusDrivesPage"), "CampusDrivesPage");
const CandidateDetailPage = page(() => import("../features/recruiter/candidates/CandidateDetailPage"), "CandidateDetailPage");
const CandidatesPage = page(() => import("../features/recruiter/candidates/CandidatesPage"), "CandidatesPage");
const EmailPage = page(() => import("../features/recruiter/email/EmailPage"), "EmailPage");
const JobsPage = page(() => import("../features/recruiter/jobs/JobsPage"), "JobsPage");
const NotesPage = page(() => import("../features/recruiter/notes/NotesPage"), "NotesPage");
const NotificationsPage = page(() => import("../features/recruiter/notifications/NotificationsPage"), "NotificationsPage");
const OverviewPage = page(() => import("../features/recruiter/overview/OverviewPage"), "OverviewPage");
const ReportsPage = page(() => import("../features/recruiter/reports/ReportsPage"), "ReportsPage");
const UsersPage = page(() => import("../features/recruiter/users/UsersPage"), "UsersPage");
const AdminLayout = page(() => import("./AdminLayout"), "AdminLayout");
const RecruiterLayout = page(() => import("./RecruiterLayout"), "RecruiterLayout");

/** Full-page pending state for public routes (the app shell has its own).
 * On a cold start the HireZ startup screen covers it until the code arrives. */
function PublicFallback() {
  return (
    <div className="public-shell">
      <HoldBrandLoading />
      <RouteFallback />
    </div>
  );
}

/**
 * Three route trees, kept structurally separate per docs/architecture.md
 * § 6: public (anonymous), candidate (candidate JWT, from Phase 4), and
 * recruiter (staff JWT, from Phase 2). Each grows independently as its
 * phase is built — none of these subtrees should import from another.
 *
 * `/admin` is its own protected tree (SUPER_ADMIN only), mirroring the
 * backend's `/api/v1/admin/*` split from `/api/v1/recruiter/*`.
 */
export function AppRoutes() {
  return (
    <Suspense fallback={<PublicFallback />}>
      <Routes>
        {/* The Sigvi assistant appears on these three pages only — never on
            the assessment / campus-drive pages, nor in the staff app. */}
        <Route element={<SigviLayer />}>
          <Route path="/" element={<PublicHomePage />} />
          <Route path="/org/:slug" element={<CareersPage />} />
          <Route path="/org/:slug/jobs/:jobId" element={<JobDetailPage />} />
        </Route>
        <Route path="/assessment/:token" element={<AssessmentTakingPage />} />
        <Route path="/campus-drive/:token" element={<CampusDriveApplyPage />} />
        <Route path="/programs/:programId" element={<ProgramApplyPage />} />
        <Route path="/candidate/*" element={<CandidatePlaceholderPage />} />

        <Route path="/recruiter/login" element={<LoginPage />} />
        <Route
          path="/recruiter"
          element={
            <ProtectedRoute>
              <RecruiterLayout />
            </ProtectedRoute>
          }
        >
          <Route index element={<OverviewPage />} />
          <Route path="ai" element={<AiWorkspacePage />} />
          <Route path="notifications" element={<NotificationsPage />} />
          <Route path="notes" element={<NotesPage />} />
          <Route path="calendar" element={<CalendarPage />} />
          <Route path="jobs" element={<JobsPage />} />
          <Route path="candidates" element={<CandidatesPage />} />
          <Route path="candidates/:candidateId" element={<CandidateDetailPage />} />
          <Route path="applications" element={<ApplicationsPage />} />
          <Route path="applications/:applicationId" element={<ApplicationDetailPage />} />
          <Route path="assessments" element={<AssessmentsPage />} />
          <Route path="assessments/attempts/:invitationId" element={<AttemptReviewPage />} />
          <Route path="assessments/:assessmentId" element={<AssessmentDetailPage />} />
          <Route path="question-bank" element={<QuestionBankPage />} />
          <Route path="programs" element={<ProgramsPage />} />
          <Route path="programs/:programId" element={<ProgramDetailPage />} />
          <Route path="institutions" element={<InstitutionsPage />} />
          <Route path="campus-drives" element={<CampusDrivesPage />} />
          <Route path="campus-drives/:driveId" element={<CampusDriveDetailPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="users" element={<UsersPage />} />
          <Route path="activities" element={<ActivitiesPage />} />
          <Route path="email" element={<EmailPage />} />
          <Route path="talk-to-admin" element={<TalkToAdminPage />} />
        </Route>

        <Route
          path="/admin"
          element={
            <ProtectedRoute requireSuperAdmin>
              <AdminLayout />
            </ProtectedRoute>
          }
        >
          <Route index element={<OrganizationsPage />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      {/* lets the startup screen go once real route content has committed */}
      <AppReady />
    </Suspense>
  );
}
