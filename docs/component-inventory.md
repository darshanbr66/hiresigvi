# Component & Module Inventory

A map of the codebase by area: what each module is for, what it talks to,
who may use it and where it is tested. Read [`developer-guide.md`](developer-guide.md)
first for how the layers fit together.

**Coverage.** Every backend API module, service, workflow, integration and
model is listed. On the frontend, every feature directory is listed with
its pages and main components; the modules added or changed for campus
institutions, the question bank, programs, grading and error handling are
described in more detail. Small presentational helpers (formatters,
icons, motion wrappers) are grouped rather than described one by one.
Paths are relative to `backend/` or `frontend/src/`.

---

## Backend

### API routers — `app/api/v1/`

Route handlers only authenticate, check the permission, parse input and
call a service. Base path `/api/v1/<audience>/…`.

| Module | Prefix | Permissions | Responsibility | Tests |
|---|---|---|---|---|
| `recruiter/auth.py` | `/recruiter/auth` | signed-in user | login, refresh (httpOnly cookie), logout, `me` | test_login, test_token_refresh, test_session_cookie |
| `recruiter/users.py` | `/users` | user.read/create/update | team accounts, roles, deactivate/delete | test_team_management, test_user_deletion |
| `recruiter/departments.py`, `employees.py` | `/departments`, `/employees` | department.*, employee.* | org chart and employee records | test_team_hierarchy, test_employee_ordering |
| `recruiter/jobs.py` | `/jobs` | job.* | job CRUD and status | test_jobs |
| `recruiter/candidates.py` | `/candidates` | candidate.*, application.create, screening.read | profiles, history, HR job matches, resume+role intake, reapply grants | test_candidates, test_reapply_permission |
| `recruiter/applications.py` | `/applications` | application.*, assessment.read | list/search/filter (incl. `campus_drive_id`, `institution_id`), detail, status changes, resume download, email | test_applications, test_application_search |
| `recruiter/screening.py` | `/applications/{id}/screening` | screening.* | run/read AI screening | test_screening |
| `recruiter/assessments.py` | `/assessments` | assessment.read/manage/delete/grade | test CRUD, draft/publish, preview, invite/retest, file parse for the form, attempt review & manual grades | test_assessments, test_assessment_grading |
| `recruiter/question_bank.py` | `/question-bank` | question_bank.read/manage | categories, tags, questions, versions, candidate preview, status; **file import** (`/import/template`, `/import/preview`, `/import`) | test_question_bank, test_question_bank_import |
| `recruiter/campus_drives.py` | `/campus-drives` | campus_drive.* | drives, link regeneration, funnel, campus report, affiliation verifications, student roster | test_campus_drives, test_institutions_campus |
| `recruiter/institutions.py` | `/institutions` | campus_drive.read/manage | institution registry | test_institutions_campus |
| `recruiter/programs.py` | `/programs` | program.read/manage, program.finance.manage | programs, publication, report, enrollments and every enrollment action, agreement document | test_programs |
| `recruiter/reports.py` | `/reports` | application.read | live overview metrics | test_reports |
| `recruiter/notes.py` | `/notes`, `/applications/{id}/notes` | note.* | personal and application notes | test_notes, test_notes_workspace |
| `recruiter/calendar.py` | `/calendar` | calendar.* | events, attendees, reminders | test_calendar |
| `recruiter/notifications.py` | `/notifications` | signed-in, notification.send/announce | in-app notification inbox | test_in_app_notifications, test_notification_center |
| `recruiter/email_templates.py`, `general_email.py` | `/email-templates`, `/email` | application.email.send | manual email composer | test_email_templates, test_general_email |
| `recruiter/activities.py` | `/activities` | activity.read/delete | audit trail | test_activity_delete, test_activity_bulk_delete |
| `recruiter/admin_messages.py` | `/admin-messages` | signed-in | Talk to Admin | test_admin_messaging |
| `recruiter/internal_ai.py` | `/ai` | internal_ai.use | recruitment intelligence queries | test_internal_ai |
| `public/jobs.py`, `applications.py`, `candidate_intake.py` | `/organizations/{slug}/…` | none (rate-limited) | careers site, email code, applicant status, apply | test_public_applications, test_candidate_intake |
| `public/campus_drives.py` | `/campus-drive/{token}` | link token | drive page and apply | test_campus_drives, test_institutions_campus |
| `public/programs.py` | `/organizations/{slug}/programs`, `/programs/{id}` | none | program listing/detail, email code, precheck, apply | test_programs |
| `public/assessments.py` | `/assessment/{token}` | invitation token | start, monitoring, submit — no answer keys | test_assessments, test_assessment_grading |
| `public/ai.py` | `/ai` | none (rate-limited) | Sigvi public assistant | test_sigvi_chat |
| `admin/organizations.py` | `/admin/organizations` | SUPER_ADMIN | organization bootstrap, careers contact | test_organization_bootstrap |

### Services — `app/services/`

Business rules and transactions. Every function takes the caller's
organization (or an actor) and scopes queries to it.

| Module | Responsibility |
|---|---|
| `application_service` | application lifecycle; every status change goes through `application_workflow` **and** `program_workflow.check_application_transition` |
| `public_application_service` | careers-site apply: verified email + identity rules + application + resume + screening + welcome email |
| `public_campus_drive_service` | drive-by-token access and apply; drive and institution come from the token only |
| `public_program_service` | program detail and apply (eligibility, terms version, enrollment created atomically with the application) |
| `candidate_service`, `reapply_service`, `hr_candidate_intake_service` | one profile per person, reapply window/grants, HR resume+role intake |
| `email_verification_service` | one-time email codes (hashed), verification tokens |
| `candidate_document_service`, `resume_service` | 10th-marks card and resume storage via `ResumeStorage` |
| `screening_service`, `resume_chunking_service`, `matching/`, `internal_ai/` | AI screening runs, resume chunks/embeddings, matching engine, internal AI queries |
| `assessment_service` | tests (draft/publish, structure lock once invited), bank-version snapshots, invitations, retests, form import parsing |
| `assessment_public_service` | token access, answer capture, submission (ignores tampered ids) |
| `assessment_grading_service` | automatic grading, manual grading bounded to 0..marks, result finalization |
| `question_bank_service` | categories, tags, versioned items with private answer keys, search, snapshot source checks |
| `question_bank_import_service` | import preview (parse + rule check + category resolution + duplicate flags) and all-or-nothing import; CSV template |
| `campus_drive_service` | drives, institution resolution, funnel, campus report |
| `institution_service` | institution registry (normalized unique names) |
| `campus_affiliation_service` | verification records, email-domain/student-list auto-verify, HR review, roster |
| `program_service` | program configuration, publication checks, disclosure text, eligibility, enrollment actions, finance records, report |
| `report_service` | live overview metrics |
| `job_service`, `organization_service`, `user_service`, `department_service`, `employee_service`, `auth_service` | jobs, tenants, accounts/roles, team hierarchy, sign-in |
| `note_service`, `calendar_service`, `calendar_reminder_service` | notes, calendar, reminder polling |
| `notification_service`, `email_composer`, `in_app_notification_service` | email delivery through `EmailProvider`, manual composer, in-app inbox |
| `activity_service`, `admin_messaging_service` | audit trail, Talk to Admin |
| `sigvi_service`, `sigvi_prompts`, `sigvi_job_context` | public assistant |

### Workflows, integrations, core

| Module | Responsibility | Tests |
|---|---|---|
| `workflows/application_workflow.py` | application status transition table | test_applications |
| `workflows/program_workflow.py` | program, eligibility, scholarship, training, offer, agreement transitions; program gates on application status | test_programs |
| `integrations/documents/question_import.py` | rule-based PDF/DOCX/XLSX/CSV question parser with size, signature, row/page and archive limits | test_question_import |
| `integrations/email/` | `EmailProvider`: Resend (HTTPS) or SMTP; never a fake success | test_email_resend, test_email_smtp |
| `integrations/storage/` | `ResumeStorage`: local disk or MongoDB GridFS | test_resume_storage, test_resume_mongodb_provider |
| `integrations/ai/` | LLM/embedding/chat providers (Groq, Gemini, Anthropic, OpenAI, Ollama) behind interfaces | test_ai_provider_selection, test_groq_screening_fallback |
| `core/exceptions.py` | `AppError` family and the error envelope | (every API test) |
| `db/rls.py`, `api/deps.py` | tenant context / RLS bypass; auth and `require_permission` | test_tenant_isolation, test_security |

### Models — `app/models/`

Entities and relationships: [`database.md`](database.md) § 3–4. Files:
`organization`, `user`, `rbac`, `team_hierarchy` (departments, employees),
`candidate`, `candidate_reapply_grant`, `email_verification`, `job`,
`job_requirement`, `application`, `resume`, `resume_chunk`,
`candidate_resume_profile`, `match_result`, `screening`, `assessment`
(tests, questions, invitations, answers, results, answer keys),
`question_bank`, `campus_drive`, `institution` (institutions, affiliation
verifications, roster), `program` (programs, enrollments, disbursements,
fee payments), `note`, `calendar_event`, `notification`, `activity`,
`admin_message`.

---

## Frontend

### App shell and shared code

| Path | Responsibility | Tests |
|---|---|---|
| `app/routes.tsx` | route table; every page lazy-loaded | (page tests) |
| `app/AppShell.tsx`, `RecruiterLayout.tsx`, `AdminLayout.tsx`, `ProtectedRoute.tsx` | sidebar/topbar shell, nav items, role-gated trees | AppShell.test |
| `lib/apiClient.ts` | the only `fetch` wrapper; turns failures into `ApiError` (friendly `message`, `kind`, `fieldIssues`, `serverMessage`, `requestId`); network failures → status 0 | apiClient.test |
| `lib/apiErrors.ts` | `friendlyMessage`, field/issue humanizers — the error policy ([`developer-guide.md`](developer-guide.md) § 5) | apiErrors.test |
| `lib/queryClient.ts`, `lib/downloadBlob.ts` | TanStack Query defaults; save a downloaded blob | — |
| `features/auth/AuthContext.tsx` | in-memory access token, refresh, current user and roles | AuthContext.test |
| `features/theme/ThemeContext.tsx`, `ThemeToggle.tsx` | light/dark choice (`data-theme`, persisted) | (AppShell.test) |
| `styles/tokens.css` | design tokens for both themes, reduced motion | — |
| `index.css`, `styles/*.css` | component styles by area (shell, dashboard, filters, public, landing, sigvi…) | — |
| `shared/components/Alert` | inline error/success message (`role="alert"` / `status`) | — |
| `shared/components/Modal`, `ConfirmDialog` | accessible dialog, confirmation | Modal.test, ConfirmDialog.test |
| `shared/components/ToastContext` | transient notifications (`aria-live`) | ToastContext.test |
| `shared/components/Pagination`, `EmptyState`, `Skeleton`, `Spinner`, `BarList`, `PipelineTrack`, `BackLink`, `Avatar`, `Icon` | lists, loading/empty states, small visuals | Pagination.test, EmptyState.test, PipelineTrack.test, BackLink.test |
| `shared/components/NetworkBackdrop`, `Reveal`, `PageTransition`, `AnimatedNumber`, `Magnetic` | decorative/motion helpers (public pages; honour reduced motion) | NetworkBackdrop.test, Reveal.test, AnimatedNumber.test |
| `shared/hooks/*` | debounced value, media query, in-view, reduced motion | useDebouncedValue.test |

Each feature directory has an `api.ts` with its endpoint functions, and
the page uses TanStack Query keys of the form `["recruiter", "<area>", …]`.

### Recruiter features — `features/recruiter/`

| Directory | Pages / main components | API | UI gating (server enforces) | Tests |
|---|---|---|---|---|
| `overview/` | `OverviewPage` — key figures, pipeline by stage, recent activity | reports, applications | — | OverviewPage.test, greeting.test |
| `jobs/` | `JobsPage` | `/jobs` | job.* | JobsPage.test |
| `candidates/` | `CandidatesPage`, `CandidateDetailPage`, `CandidateJourney`, `AddResumeAndRoleModal`, `GrantReapplyModal`, `ReapplyPermissionPanel` | `/candidates` | candidate.* | CandidatesPage.test, CandidateDetailPage.test, ReapplyPermissionPanel.test |
| `applications/` | `ApplicationsPage` (server-side search/filter/sort/page in the URL; filters include **campus drive** and **institution**), `ApplicationDetailPage`, `ApplicationListControls`, `applicationFilters.ts`, `EmailComposerModal` | `/applications` | application.* | ApplicationsPage.test, ApplicationListControls.test, applicationFilters.test, ApplicationDetailPage.test, EmailComposerModal.test |
| `assessments/` | `AssessmentsPage`, `AssessmentDetailPage`, `AssessmentForm` (test builder: written + bank questions, order, marks, file import, preview), `QuestionEditor` (per-type editor incl. answer keys), `BankQuestionPicker` (filter and add published bank questions), `AttemptReviewPage` (answers, keys, manual grading 0..marks) | `/assessments`, `/question-bank` | assessment.manage, assessment.grade (ORG_ADMIN, RECRUITER, HIRING_MANAGER) | AssessmentDetailPage.test, AttemptReviewPage.test |
| `questionBank/` | `QuestionBankPage` — search, filters (subject/topic, type, difficulty, status, tag), pagination, create/edit with versions, candidate preview, publish/archive, subjects & topics; `QuestionImportDialog` — choose file / download template → review (per-row problems, duplicates unticked, new categories) → import as draft or published | `/question-bank` | question_bank.manage (ORG_ADMIN, RECRUITER) | QuestionBankPage.test, QuestionImportDialog.test |
| `campusDrives/` | `CampusDrivesPage` (drives + campus report by institution), `CampusDriveDetailPage` (funnel, candidates, link), `AffiliationVerificationPanel` (verify/reject, student list) | `/campus-drives`, `/institutions` | campus_drive.manage | CampusDriveDetailPage.test |
| `institutions/` | `InstitutionsPage` — registry, register/edit/deactivate | `/institutions` | campus_drive.manage (ORG_ADMIN, RECRUITER) | InstitutionsPage.test |
| `programs/` | `ProgramsPage`, `ProgramDetailPage` (configuration, publication problems, report, participants), `ProgramForm`, `EnrollmentPanel` (eligibility, test invite, interview, scholarship eligibility/decision, disbursements, fees, training, offer, agreement, bond) | `/programs` | program.manage (ORG_ADMIN, RECRUITER); finance actions program.finance.manage (ORG_ADMIN) | ProgramDetailPage.test, EnrollmentPanel.test |
| `reports/` | `ReportsPage` | `/reports` | application.read | — |
| `screening/` | `api.ts` (used by application detail) | `/applications/{id}/screening` | screening.* | (ApplicationDetailPage.test) |
| `ai/` | `AiWorkspacePage`, `MatchResultCard`, `PickerModal` | `/ai` | internal_ai.use | AiWorkspacePage.test, MatchResultCard.test |
| `notes/`, `calendar/`, `notifications/`, `email/`, `activities/`, `adminMessages/` | notes workspace; calendar with attendees; inbox + toaster; general email; audit trail; Talk to Admin | respective endpoints | respective permissions | NotesPage.test, CalendarPage.test, NotificationsPage.test, EmailPage.test, ActivitiesPage.test |
| `team/`, `users/` | org chart and departments; accounts and roles | `/departments`, `/employees`, `/users` | user.*, department.*, employee.* | DepartmentsHierarchy.test, UsersPage.test |

### Public and candidate features

| Path | Responsibility | Tests |
|---|---|---|
| `features/public/` | `PublicHomePage`, `HeroSection` (+ `hero/` 3D scene with static poster fallback), `LandingNav`, `PublicHeader` | PublicHomePage.test, HeroScene.test |
| `features/careers/CareersPage`, `JobDetailPage` | organization careers site, open jobs, programs listing | JobDetailPage.test |
| `features/careers/JobApplicationForm` | the shared apply form: email code, identity fields, resume and marks card, early "already applied" check; also used by drives and programs | JobApplicationForm.test |
| `features/careers/CampusDriveApplyPage` | drive page from its link token | CampusDriveApplyPage.test |
| `features/careers/ProgramApplyPage` | program disclosures (amounts only when finalized), education declaration, terms acceptance | ProgramApplyPage.test |
| `features/careers/AssessmentTakingPage`, `MonitoringConsentScreen`, `useProctoring` | taking a test by token; monitoring consent | AssessmentTakingPage.test |
| `features/sigvi/` | public AI assistant (lazy-loaded widget) | SigviLayer.test, SigviWidget.test |
| `features/admin/organizations/` | platform admin: organizations | — |
| `features/candidate/` | placeholder for the future candidate portal (not implemented) | — |
