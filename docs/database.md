# Database Design

Status: Implemented — 9 migrations exist (`backend/alembic/versions/`)
covering identity/tenancy, jobs/candidates/applications, resumes, AI
screening, assessments, campus drives, and notes. This document was the
reference those migrations implemented against; where an actual table
deviates from what's described here (mostly: assessments/campus
hiring/screening built simpler than originally specced), the corresponding
domain doc (`docs/assessment.md`, `docs/campus-hiring.md`,
`docs/ai-screening.md`) has an "Implementation status" note explaining the
gap — this document was not rewritten table-by-table to match.

## 1. Primary key strategy (decision record)

**Decision: every table uses a `uuid` primary key (Postgres native `uuid`
type), server-generated with `gen_random_uuid()` (from `pgcrypto`, or the
built-in `gen_random_uuid()` on Postgres 13+/`pgcrypto`).**

Options considered:

| Option | Pros | Cons |
|---|---|---|
| **UUID v4 (chosen)** | No cross-table sequence coordination; safe to expose in URLs (assessment/candidate-facing IDs must not be guessable — explicit requirement); safe for future multi-region/offline ID generation; no enumeration attacks. | Random insert order fragments B-tree indexes somewhat; 16 bytes vs 4/8 for int. |
| UUID v7 (time-ordered) | Same safety properties as v4, better index locality. | Needs an app-side or extension-provided generator (not in core Postgres until v18); adds a dependency/complexity for a benefit that only matters at a scale this project isn't at yet. |
| bigint identity + separate public UUID | Best index locality, smallest joins. | Two ID systems per table (internal vs external) is exactly the "unnecessary complexity" the project should avoid; every API/service layer has to be careful which one it's using. |

v4 is chosen for simplicity and because the security requirement (no
predictable/enumerable IDs for candidates/assessments) matters more here than
insert-order index locality at current expected scale. If write volume ever
makes index fragmentation a measured problem, migrating the generation
function to UUIDv7 is a low-risk change (same column type, same app code) —
noted as a future optimization, not a blocker now.

Applies uniformly: no table mixes integer and UUID keys. Foreign keys are
always `uuid`.

## 2. Conventions

- Every table: `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`,
  `created_at timestamptz NOT NULL DEFAULT now()`,
  `updated_at timestamptz NOT NULL DEFAULT now()` (maintained by an
  `ON UPDATE` trigger or ORM `onupdate`).
- Tenant-owned tables: `organization_id uuid NOT NULL REFERENCES organizations(id)`,
  indexed, and covered by an RLS policy (see `docs/security.md`).
- Soft deletion (`deleted_at timestamptz NULL`) is used only where the
  business need is real: `Job` (closed/withdrawn jobs must remain visible in
  historical reports), `Candidate` (right-to-erasure/GDPR-style requests
  should be a status change with retained audit trail, not a hard delete),
  and `Assessment` (must remain referenceable by past invitations/attempts).
  Everything else uses normal hard deletes or relies on status enums —
  not adding `deleted_at` everywhere "just in case."
- Enums are implemented as Postgres native `ENUM` types (via SQLAlchemy
  `Enum`) for the fixed, rarely-changing vocabularies (roles, statuses).
- Every FK declares an explicit `ON DELETE` behavior — no implicit defaults.
  Default posture: `RESTRICT` for anything that would silently orphan
  business-critical history (e.g. don't allow deleting a Job that has
  Applications); `CASCADE` only for true ownership chains (e.g. deleting an
  `Assessment` cascades to its `Question`s and `QuestionOption`s, but not to
  `AssessmentInvitation`s/`CandidateAttempt`s, which are historical record —
  those `RESTRICT`).

## 3. Entities by domain

### 3.1 Identity & Tenancy

**organizations**
`id, name, slug (unique), status (ACTIVE/SUSPENDED), careers_contact_email
NULL, created_at, updated_at`
`careers_contact_email` is the recruitment team's public contact address.
It is set by a SUPER_ADMIN only and published on the careers site and in
candidate emails (`docs/recruitment-workflow.md` § 6).

**users** (staff)
`id, organization_id NULL (NULL only for SUPER_ADMIN), email, hashed_password,
full_name, is_active, last_login_at, created_at, updated_at`
Unique: `(organization_id, email)` where `organization_id IS NOT NULL`;
separate uniqueness for `SUPER_ADMIN` rows on `email` alone.

**roles**
`id, organization_id NULL (NULL = system role), name, is_system, created_at`

**permissions**
`id, code (unique, e.g. "job.create"), description`

**role_permissions**
`role_id, permission_id` (composite PK)

**user_roles**
`user_id, role_id` (composite PK)

**user_refresh_tokens**
`id, user_id, token_hash, family_id, issued_at, expires_at, revoked_at, replaced_by_id`

### 3.2 Candidate Identity

**candidates**
`id, organization_id, email, hashed_password NULL (NULL until candidate
self-registers a login; recruiter-imported candidates may not have one yet),
full_name, phone, location, current_title, years_experience, source
(PORTAL/RECRUITER_ADDED/CAMPUS_IMPORT/REFERRAL/OTHER), is_active, created_at,
updated_at, date_of_birth NULL, place_of_birth NULL, languages text[]
(default '{}'), email_verified_at NULL`
Unique: `(organization_id, email)`; `uq_candidates_org_phone` on
`(organization_id, phone) WHERE phone IS NOT NULL`. Phones are stored
normalized to E.164 (`app/core/phone.py`), so different formats of the same
number collide on this index.
Check `ck_candidates_verified_identity_complete`: when `email_verified_at` is
set (self-service applicants), `phone`, `date_of_birth`, `place_of_birth` and
at least one `languages` entry are required. `languages` has a GIN index for
filtering by language.

Self-declared education (migration `3c4d5e6f7081`, written when a candidate
applies to a recruitment program, `docs/programs.md` § 3), all NULL for
everyone else: `education_level (DIPLOMA/BACHELORS/MASTERS/MTECH/PHD/OTHER),
study_year, specialization, institution_id NULL -> institutions (SET NULL),
institution_name (snapshot), expected_completion_date`.

**email_verifications** (tenant-scoped, RLS)
`id, organization_id, email, purpose (CANDIDATE_APPLICATION), code_hash NULL
(an HMAC of the code, never the code itself), code_expires_at, failed_attempts,
last_sent_at, send_window_started_at, sends_in_window, verified_at, token_hash
NULL (SHA-256), token_expires_at, consumed_at, created_at, updated_at`
Unique: `(organization_id, email, purpose)`, `token_hash`. There is one row
per email address, reused across resends. The table holds the email
one-time-code state for the anonymous apply flow
(`docs/recruitment-workflow.md` § 6).

**candidate_refresh_tokens**
Mirrors `user_refresh_tokens`, keyed to `candidate_id`.

### 3.3 Jobs

**jobs**
`id, organization_id, title, department, location, employment_type, description,
status (DRAFT/OPEN/ON_HOLD/CLOSED/WITHDRAWN), openings_count, created_by
(user_id), deleted_at NULL, created_at, updated_at`

**job_requirements**
`id, job_id, label, description, weight, is_mandatory, created_at`

### 3.4 Resumes

**resumes** (planned shape below; see "Implementation status" note)
`id, organization_id, candidate_id, storage_key, original_filename,
content_type, size_bytes, checksum_sha256, status
(UPLOADED/PROCESSING/PROCESSED/FAILED), uploaded_at, processed_at`

**Implementation status**: the actual `resumes` table
(`app/models/resume.py`) is simpler — no `status`/`checksum_sha256`
columns, and the storage identifier column is named `storage_path` (plus
a `storage_provider` column added when MongoDB GridFS was introduced as a
storage backend alongside local disk). `storage_path`/`storage_provider`
together are the opaque, provider-specific key into whichever
`ResumeStorage` implementation wrote the file
(`app/integrations/storage/`) — a relative disk path for `"local"`, a
GridFS ObjectId for `"mongodb_gridfs"`. Either way this table stays
metadata-only; file bytes never live in Postgres. `resume_documents` /
`resume_chunks` below are still Phase 5 future work, not yet built.

**candidates — 10th marks** (migration `f9a0b1c2d3e4`): optional
`tenth_marks_percentage NUMERIC(5,2)` (0–100, checked) and a reference to
the optional 10th marks card: `tenth_marks_card_storage_provider`,
`_storage_path`, `_filename`, `_content_type`, `_size_bytes`,
`_uploaded_at` — all set or all NULL (`ck_candidates_tenth_marks_card_complete`).
The bytes are written through the same `ResumeStorage` abstraction (same
GridFS bucket, `metadata.document_kind = "tenth_marks_card"`, no
`application_id`); the same migration widened `candidates.full_name` to
`TEXT`.

**resume_documents**
`id, resume_id, extracted_text, extraction_engine, extracted_at`
(1:1 with `resumes`; kept separate so re-extraction/versioning doesn't bloat
the `resumes` row.)

**resume_chunks**
`id, resume_document_id, chunk_index, content, embedding vector(N),
token_count, created_at`
`N` (embedding dimension) is fixed by whichever `EmbeddingProvider` is
configured; recorded in `docs/ai-screening.md` once a provider is chosen.

### 3.5 Applications

**applications**
`id, organization_id, candidate_id, job_id, campus_drive_id NULL, status
(APPLIED/AI_SCREENED_OUT/UNDER_REVIEW/SCREENING/ASSESSMENT_INVITED/
ASSESSMENT_STARTED/ASSESSMENT_COMPLETED/SHORTLISTED/INTERVIEW/SELECTED/
REJECTED/HIRED), source (PORTAL/RECRUITER_ADDED/CAMPUS_IMPORT/REFERRAL/OTHER/
HR_MATCH), applied_at,
resume_id NULL (the resume used for this specific application, may differ
across applications), created_at, updated_at`
Unique: `(candidate_id, job_id)` — one active application per candidate per
job; re-applying after `REJECTED` is a product decision deferred to Phase 3
(likely: allow after a cooldown or on job re-open, tracked via a new row
once the unique constraint is scoped by e.g. a reopen counter — not
finalized).

`WITHDRAWN` was merged into `REJECTED` (SIGVITAS platform overhaul): the
platform no longer distinguishes a candidate-initiated withdrawal from a
recruiter rejection at the status-vocabulary level. Existing `WITHDRAWN`
rows were data-migrated to `REJECTED` and the value was dropped from the
native Postgres enum (`backend/alembic/versions/
c1a2f3b4d5e6_merge_withdrawn_into_rejected_add_hired.py`). `HIRED` was
added as the new terminal state reachable only from `SELECTED`.

`AI_SCREENED_OUT` (set only by the submission-time AI screening) and
`HR_MATCH` (an application HR created by matching an existing candidate to
another job) were added by `b4c5d6e7f8a9_candidate_intake_hr_requirements.py`.
Postgres can't drop an enum value, and older code can't read these two, so
that migration's **downgrade refuses to run** while any application or
status-history row uses them. It never rewrites them into older values,
because that would falsify the status history. A person has to resolve those
rows deliberately first.

**application_status_history**
`id, application_id, from_status NULL, to_status, changed_by_user_id NULL,
changed_by_candidate_id NULL, reason NULL, created_at`

### 3.6 Screening / AI

**screening_runs**
`id, organization_id, application_id, triggered_by_user_id NULL (NULL if
system-triggered), status (PENDING/RUNNING/COMPLETED/FAILED), overall_score
NULL, started_at, completed_at, decision NULL (MATCH/NOT_MATCH),
matched_requirements JSONB NULL, missing_requirements JSONB NULL`
`decision` is the binary outcome that the submission-time screening gate
acts on (`docs/recruitment-workflow.md` § 6). HR can always override it.

**ai_runs**
`id, screening_run_id, job_requirement_id NULL, provider, model,
prompt_version, input_hash, status, started_at, completed_at, error NULL`

**requirement_evaluations**
`id, ai_run_id, job_requirement_id, verdict (MEETS/PARTIAL/DOES_NOT_MEET/
INSUFFICIENT_EVIDENCE), score, rationale, created_at`

**evaluation_evidence**
`id, requirement_evaluation_id, resume_chunk_id, similarity_score, excerpt,
created_at`

### 3.7 Assessments

**assessments**
`id, organization_id, title, description, instructions, duration_minutes,
pass_score, status (DRAFT/ACTIVE/ARCHIVED), created_by, deleted_at NULL,
created_at, updated_at`

**questions**
`id, assessment_id, type (MCQ_SINGLE/MCQ_MULTI — extensible for
APTITUDE/CODING/SUBJECTIVE later), prompt, points, order_index, created_at`

**question_options**
`id, question_id, label, is_correct, order_index`

**assessment_invitations**
`id, organization_id, assessment_id, application_id, token_hash (unique),
status (NOT_SENT/SENT/DELIVERED/OPENED/STARTED/SUBMITTED/EXPIRED/CANCELLED),
expires_at, max_attempts, sent_at, delivered_at NULL, opened_at NULL,
started_at NULL, submitted_at NULL, cancelled_at NULL, created_by, created_at`

**candidate_attempts**
`id, invitation_id, status (IN_PROGRESS/SUBMITTED/EXPIRED), started_at,
submitted_at NULL, ip_address NULL, created_at`

**candidate_answers**
`id, attempt_id, question_id, selected_option_ids uuid[] NULL, answer_text
NULL, is_correct NULL, score_awarded NULL, created_at`

**assessment_evaluations**
`id, attempt_id, evaluator_type (AUTO/AI/MANUAL), evaluated_by NULL,
evaluated_at, notes NULL`

**assessment_results**
`id, attempt_id (unique), score, max_score, percentage, passed, breakdown
jsonb, created_at`

**Question bank, publication and manual grading** (migration
`2b3c4d5e6f70`, `docs/assessment.md` § 6 / § 10):

- `assessments.status` DRAFT/PUBLISHED (existing rows PUBLISHED).
- `question_type` adds TRUE_FALSE, SHORT_ANSWER, DESCRIPTIVE, CODING.
- `questions.negative_points` (default 0, ≤ points) and
  `questions.source_bank_version_id NULL -> question_bank_item_versions
  (RESTRICT)` — the exact bank version the question was copied from.
- `question_answer_keys` (1:1 with a question, CASCADE): accepted answers,
  case sensitivity, grading notes, explanation — private grading data.
- `candidate_answers` gains the free-text answer and per-answer grading:
  awarded points, method (AUTO/MANUAL), grader, graded_at, private comment.
- `assessment_results.passed` is NULL while `grading_status` is
  PENDING_MANUAL_REVIEW (FINAL otherwise).
- Bank tables (all tenant-scoped, RLS): `question_categories` (tree via
  `parent_id`, unique normalized name among siblings), `question_tags`
  (unique normalized name per org), `question_bank_items` (category,
  difficulty, status, `current_version_number`, created/updated by),
  `question_bank_item_tags`, `question_bank_item_versions` (immutable
  content; unique `(item_id, version_number)`; points ≥ 1 and
  negative_points ≤ points checks), `question_bank_answer_keys` (1:1 with a
  version). Bank rows are archived, never deleted, and a version referenced
  by a test is RESTRICTed from deletion.

### 3.8 Campus Hiring

**institutions** (migration `1a2b3c4d5e6f`, `docs/campus-hiring.md` § 1a)
`id, organization_id, name, normalized_name, institution_type NULL, city,
state, country, website, email_domains text[] (may be empty), placement
contact name/email/phone, notes, is_active, created_by, created_at,
updated_at`. Unique `(organization_id, normalized_name)`. Deactivated, never
deleted.

**campus_drives**
`id, organization_id, job_id, institution_id -> institutions (RESTRICT, NOT
NULL), college_name (display snapshot), affiliation_verification_method
(NONE/EMAIL_DOMAIN/STUDENT_LIST/MANUAL), description, batch_year,
default_assessment_id NULL, start_date, end_date, registration_deadline,
link_token_hash, status (DRAFT/ACTIVE/PAUSED/CLOSED), created_by,
deleted_at, created_at, updated_at`

**campus_affiliation_verifications** — one per campus application that
needs verification (`application_id` unique): `campus_drive_id,
institution_id (the drive's), status (PENDING/VERIFIED/REJECTED), method
NULL, reviewed_by_user_id NULL, reviewed_at NULL, review_note`.

**campus_drive_roster_entries** — the college-provided student list:
`campus_drive_id (CASCADE), email (unique per drive), student_identifier,
full_name, added_by_user_id, created_at`.

Note: no separate "drive membership" table — a candidate's participation in
a drive is represented by an `applications` row with `campus_drive_id` set
and `job_id = campus_drives.job_id`. This avoids duplicating the
candidate–opportunity relationship that `Application` already models (see
`CLAUDE.md` § 2, "Candidate != Application").

### 3.8a Recruitment Programs

Migration `3c4d5e6f7081`, `docs/programs.md`. All tenant-scoped with RLS.

**recruitment_programs** — configuration: title, description, status
(DRAFT/PUBLISHED/PAUSED/CLOSED/ARCHIVED), `job_id` (the carrier job,
RESTRICT), eligibility arrays (education levels, study years,
specializations), completion window, training duration options (check:
only 6 and/or 12), fee amount/currency/terms/disclosure, scholarship
min/max/currency/rules/disbursement timing/disclosure, employment pathway,
bond duration (default 36)/terms summary/start rule, application window,
`assessment_id NULL`, interview stages, required documents, candidate
disclosures, terms version, optional-stage switches, published by/at. Every
not-yet-finalized money/date value is nullable; checks keep amounts
positive and min ≤ max.

**recruitment_program_institutions** — `(program_id, institution_id)`
eligible-institution list.

**program_enrollments** — one per program application (`application_id`
unique, RESTRICT): the education declaration, accepted terms version/time
and `disclosure_snapshot`, eligibility (status, reviewer, time, note),
`interview_completed_at/by`, scholarship eligibility (+ by/at), scholarship
status, approved amount/currency (+ by/at, note), training (status,
duration, dates), offer (status, made_at, joined_on), agreement (status,
reference, signed_on, stored document metadata), bond (duration, start,
end).

**scholarship_disbursements** — append-only: amount (> 0), currency,
status (SCHEDULED/PAID/CANCELLED), scheduled_on, paid_on, reference, note,
recorded_by.

**program_fee_payments** — append-only manual records: amount (> 0),
currency, paid_on, method, receipt reference, note, status
(RECORDED/VOIDED), void reason/by/at, recorded_by. No card or bank data.

Added by migration `5e6f70819203` (`docs/programs.md` § 3a, § 5):

- **recruitment_programs**: `company_funding_type` (FIXED_AMOUNT /
  PERCENTAGE) + `company_funding_value` (both NULL or both set; % in
  (0, 100]), `funding_requires_independent_approval` (default true),
  `affiliation_verification_method` (existing rows NONE).
- **program_enrollments**: fee obligation snapshot (`fee_amount`,
  `fee_currency`, `fee_due_date`, `company_contribution_amount`,
  `candidate_payable_amount`; checks: contribution ≤ fee and payable = fee −
  contribution), accepted funding revision/time, training
  enrolled_at/completed_on/outcome note, employment review (status, role,
  `employment_department_id` → departments, proposed start, decided by/at,
  note), `employee_id` → employees (SET NULL), agreement version, terms
  snapshot (JSONB), sent_at, effective/expiry dates (ordered), status note,
  `bond_applies_reason`. New enum values: scholarship PROPOSED, training
  WITHDRAWN, agreement EXPIRED/TERMINATED.
- **program_funding_revisions** — append-only funding proposals/decisions:
  revision number (unique per enrollment), kind (INITIAL/REVISION/AMENDMENT),
  status (PROPOSED/APPROVED/REJECTED/WITHDRAWN/SUPERSEDED; at most one
  PROPOSED per enrollment — partial unique index), fee, currency, basis and
  value, computed contribution/payable (same checks), reason, proposer,
  decider, decision time/note.
- **training_milestones** — title, description, due_on, status
  (PENDING/COMPLETED/MISSED; completed ⇔ completed_on), note, created/updated by.
- **application_interviews** — stage, scheduled_at, duration, location,
  interviewer, status, outcome (set ⇔ COMPLETED), rating 1–5, feedback,
  cancellation reason, scheduled/recorded by/at; one SCHEDULED per
  (application, stage).
- **campus_affiliation_verifications**: `campus_drive_id` and
  `institution_id` nullable, new `program_id`; check: exactly one of drive /
  program, and drive rows keep an institution. New methods
  STUDENT_ID_DOCUMENT, COORDINATOR_CONFIRMATION.
- **assessment_results.pass_score_applied** (backfilled).

### 3.9 Notes

**notes**
`id, organization_id, candidate_id NULL, application_id NULL (at least one
required — CHECK constraint), author_user_id, body, visibility (INTERNAL —
single value for now, kept as an enum so PRIVATE/mentions/tags can be added
without a schema redesign), created_at, updated_at`

### 3.10 Audit

**audit_logs**
`id, organization_id NULL (NULL for platform-level actions), actor_type
(USER/CANDIDATE/SYSTEM), actor_id NULL, action, resource_type, resource_id
NULL, metadata jsonb, ip_address NULL, created_at`
Append-only: no update/delete path in application code.

### 3.11 Notification

**email_messages**
`id, organization_id NULL, template_key, recipient_email, recipient_type
(USER/CANDIDATE), subject, status (QUEUED/SENT/FAILED), provider_message_id
NULL, related_resource_type NULL, related_resource_id NULL, sent_at NULL,
created_at`
Body content is not stored (templates are code/config, not DB rows) unless a
future audit requirement demands it.

**notifications** (implemented — migration `b2c3d4e5f6a7`)
In-app notifications, distinct from outbound email above.
`id, organization_id, recipient_user_id -> users (ON DELETE CASCADE), type
(enum notification_type: ASSESSMENT_STARTED / ASSESSMENT_SUBMITTED), title,
message, assessment_invitation_id NULL -> assessment_invitations (ON DELETE
CASCADE), read_at NULL (NULL = not yet acknowledged), created_at, updated_at`
Tenant-owned with the standard `tenant_isolation` RLS policy. Unique index
`uq_notifications_invitation_type (assessment_invitation_id, type)` makes
"once per invitation and event" a database guarantee; a partial index on
`(recipient_user_id, created_at) WHERE read_at IS NULL` serves the poller.
Rows are only ever addressed to one user, so unlike `activities` they are not
an org-wide log.

## 4. Key relationships (summary)

```
Organization 1─* User
Organization 1─* Candidate
Organization 1─* Job 1─* JobRequirement
Candidate 1─* Resume 1─1 ResumeDocument 1─* ResumeChunk
Candidate 1─* Application *─1 Job
Application *─0..1 CampusDrive *─1 Institution
Application 1─0..1 CampusAffiliationVerification
Application 1─0..1 ProgramEnrollment *─1 RecruitmentProgram ─1 Job (carrier)
RecruitmentProgram *─* Institution, *─0..1 Assessment
ProgramEnrollment 1─* ScholarshipDisbursement, 1─* ProgramFeePayment
QuestionCategory 1─* QuestionBankItem 1─* QuestionBankItemVersion 1─1 QuestionBankAnswerKey
Question *─0..1 QuestionBankItemVersion (snapshot source), 1─0..1 QuestionAnswerKey
Application 1─* ApplicationStatusHistory
Application 1─* ScreeningRun 1─* AIRun 1─* RequirementEvaluation 1─* EvaluationEvidence
Application 0..1─* AssessmentInvitation *─1 Assessment 1─* Question 1─* QuestionOption
AssessmentInvitation 1─* CandidateAttempt 1─* CandidateAnswer
CandidateAttempt 1─1 AssessmentEvaluation, 1─1 AssessmentResult
Institution 1─* CampusDrive
Candidate|Application 1─* Note
(everything) ─* AuditLog (write sink)
```

## 5. Indexing (baseline, extend as query patterns emerge)

- `organization_id` on every tenant-owned table (required for RLS + normal
  filtering).
- `(organization_id, email)` unique on `users` and `candidates`.
- `applications(candidate_id)`, `applications(job_id)`,
  `applications(campus_drive_id)`, `applications(status)`.
- `assessment_invitations(token_hash)` unique — this is the hot lookup path
  for the public assessment-taking endpoint.
- `resume_chunks` — an IVFFlat or HNSW index on `embedding` once pgvector
  usage begins (Phase 5), tuned after real data volume exists.
- `audit_logs(organization_id, created_at)` for time-ranged audit queries.

## 6. Migrations

Alembic from Phase 1 onward. One migration per logical schema change, no
hand-edited production schema, migrations committed to version control.
Seed data (system roles/permissions) ships as a data migration, not
application-startup logic.

**Data-safety guards, not guesses.** When a migration's data step could
lose or misattribute data, it stops with an explicit message for a person to
resolve. It never picks a winner on its own. Example:
`b4c5d6e7f8a9_candidate_intake_hr_requirements.py` normalizes existing
candidate phones to E.164 before creating `uq_candidates_org_phone`. It first
checks for numbers that only collide *after* normalization ("98765 43210" and
"+919876543210" in one organization). If there are any, it rewrites nothing
and fails, listing the affected candidate ids but never the numbers. Deploys
run `alembic upgrade head` automatically, so before deploying that revision,
run the same check against production data and fix any duplicates by hand.
Its downgrade guard is described in § 3.5.

Recent revisions (linear chain, current head `5e6f70819203`):

| Revision | Adds |
|---|---|
| `1a2b3c4d5e6f` | institution registry, `campus_drives.institution_id` (backfilled from existing `college_name`, additive), affiliation verification, student roster |
| `2b3c4d5e6f70` | question bank, assessment DRAFT/PUBLISHED, new question types, answer keys, manual grading |
| `3c4d5e6f7081` | recruitment programs, enrollments, disbursements, fee payments, candidate education fields |
| `4d5e6f708192` | job employment types (internship / part-time / full-time) and type details |
| `5e6f70819203` | program funding revisions, fee obligations, training milestones, agreement terms, employment review, `application_interviews`, program affiliation verification, `pass_score_applied`, permissions `interview.read/manage`, `program.employment.approve` |

All three are non-destructive on upgrade. Their downgrades drop what they
added; `2b3c4d5e6f70`'s downgrade refuses to run while any question uses a
new type or any result awaits manual grading (PostgreSQL can't drop enum
values, so the new `question_type` values stay on the type).

**Model ↔ migration drift (resolved without a migration).** `alembic check`
had reported differences on five older tables. In each case the database —
built by the migrations — was right and the model declaration was
incomplete, so the models were corrected and the schema left untouched:

| Table | Database (from migration) | Model said | Fix |
|---|---|---|---|
| `resumes.application_id` | unique index (one resume per application, `a5afbdf6f1b5`) | non-unique index | `unique=True` |
| `assessment_results.invitation_id` | unique constraint `uq_…` (`89d2c5e04700`) | unique *index* `ix_…` | plain `unique=True` |
| `candidate_resume_profiles.candidate_id` | unique constraint `uq_…` (`c4d5e6f7a8b9`) | unique *index* `ix_…` | plain `unique=True` |
| `calendar_events` | partial index `ix_calendar_events_due_reminders` (`a8b9c0d1e2f3`) | missing | declared in `__table_args__` |
| `employees` | partial unique index `uq_employees_org_email WHERE deleted_at IS NULL` (`e8f9a0b1c2d3`) | missing | declared in `__table_args__` |

None of these were introduced by the institution/question-bank/program
work. Left as they were, a future `--autogenerate` would have proposed
dropping the reminder index and the active-email uniqueness and weakening
the resume uniqueness. `alembic check` now reports no differences.
