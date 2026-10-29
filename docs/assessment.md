# Assessment Platform

**Implementation status:** MCQ assessments are implemented end-to-end —
recruiter builds an assessment with single/multi-select questions
(`app/models/assessment.py`), invites a candidate from the application
detail page (only legal while the application is in `SCREENING`, per
`docs/recruitment-workflow.md`), and the candidate answers via an opaque
token link with no login (`app/api/v1/public/assessments.py`), auto-scored
on submit. Real simplifications from the design below, noted in
`app/models/assessment.py`'s docstring: no `AssessmentEvaluation` as a
separate row (the scoring process and its result are one `AssessmentResult`
row), no `DELIVERED`/`OPENED` webhook states, no `max_attempts` bound (one
attempt per invitation), and email is manual — assigning an assessment
only *prepares* the invitation (the link is also returned once in the
recruiter's API response so it can be copied manually, see
`app/api/v1/recruiter/assessments.py`); the recruiter emails it explicitly
with "Send Assessment Invitation" (see § 8).

## 0. Assessment monitoring ("proctoring") — SIGVITAS platform overhaul

Transparent, browser-based monitoring, not hidden surveillance. Before
starting an attempt, the candidate sees a "Before You Begin" screen
(`frontend/src/features/careers/MonitoringConsentScreen.tsx`) listing
exactly what's monitored and must explicitly check "I understand and
agree" before the "Start Assessment" button is enabled. Only what is
actually implemented is disclosed — nothing is claimed that the browser
can't reliably detect.

**Implemented events** (`app/models/assessment.py::MonitoringEventType`,
persisted to `assessment_monitoring_events`, recorded via
`POST /api/v1/public/assessment/{token}/events` while the attempt is
`STARTED`): `MONITORING_CONSENT_GIVEN`, `TAB_SWITCH`, `WINDOW_BLUR`,
`WINDOW_FOCUS`, `FULLSCREEN_EXIT`, `CAMERA_PERMISSION_CHANGED`,
`MICROPHONE_PERMISSION_CHANGED`, `CAMERA_DEVICE_CHANGED`,
`MICROPHONE_DEVICE_CHANGED`, `CAMERA_UNAVAILABLE`,
`MICROPHONE_UNAVAILABLE`, `CONNECTION_INTERRUPTED`,
`CONNECTION_RESTORED`. The frontend listener
(`frontend/src/features/careers/useProctoring.ts`) batches events and
flushes every ~4 seconds — a failed/dropped flush never blocks or fails the
candidate's attempt.

**No raw audio/video is ever captured or stored.** On accepting the consent
screen, the browser is asked for camera/microphone permission purely to
detect whether they're available (`getUserMedia`, then every track is
immediately stopped) — no stream is displayed, transmitted, or persisted.
This is a deliberate scope boundary (CLAUDE.md: "do not store raw
audio/video unless there is a clearly defined, secure requirement and
architecture for it" — there isn't one here).

**Documented limitations** (do not oversell these to candidates or
recruiters):
- `navigator.mediaDevices.ondevicechange` fires on device add/remove, not
  on a live, mid-session *permission* revocation in every browser — some
  browsers don't surface that at all to page script.
- `Permissions.query({name: "camera"})`/`"microphone"` isn't supported in
  every browser; where unsupported, permission state is only inferred from
  the initial `getUserMedia` call's success/failure, not live-tracked.
- `visibilitychange`/`blur`/`focus` are reliable across modern browsers but
  can't distinguish *why* focus was lost (another app, OS notification,
  devtools) — the event only records that it happened.
- Fullscreen monitoring only fires if the assessment UI actually requested
  fullscreen; this build does not force fullscreen, so `FULLSCREEN_EXIT`
  only fires for candidates who opted into fullscreen themselves.

**Candidate-facing result never includes a score.** Submission returns only
an acknowledgement (`PublicSubmissionResult`, just `submitted_at`) — the
candidate sees a polished "Assessment Submitted" confirmation
(`frontend/src/features/careers/SubmissionSuccess.tsx`), never a
percentage. Recruiters/admins see the full score via the existing,
separate `AssessmentResultResponse` and the events above via "Assessment
Activity" on the application detail page — both permission-gated by the
existing `assessment.read` permission, and both described to recruiters in
neutral, non-accusatory language (an event is something *observed*, never
an automatic accusation of misconduct).

## 0.1 Inviter notifications (in-app)

When a candidate **starts** and when they **submit** an assessment, the one
staff user who sent that invitation (`assessment_invitations.invited_by_user_id`
— for a campus drive, the drive's creator) gets an in-app notification:
"*<candidate> has started/submitted the <assessment> assessment.*" It is shown
as a toast in the recruiter portal. It is **not** an email and not a broadcast:
other recruiters, other admins and other organizations never receive it, and an
invitation whose inviter was removed (or is inactive, or not in the
organization) notifies no one.

- **Source of truth is the backend.** It is created inside the existing
  authoritative transitions in `assessment_public_service`: `start_attempt`
  (only on the `SENT -> STARTED` transition) and `submit_attempt` (after the
  result and the existing `ASSESSMENT_COMPLETED` activity are written). The
  candidate's browser is never involved in deciding whether to notify.
- **Once per event.** A reload, a repeat/StrictMode `start` call, or a second
  `submit` (rejected as `already_submitted`) never reaches the notification
  code, and the unique `(assessment_invitation_id, type)` index makes a racing
  duplicate insert a no-op (`ON CONFLICT DO NOTHING`). A retest is a new
  invitation row, so it notifies again.
- **Never breaks the candidate.** The insert runs in a savepoint; a database
  failure is logged (`In-app notification could not be stored`, error type
  only) and swallowed, so a notification problem cannot lose submitted answers.
- **Privacy.** `NotificationResponse` carries only id, type, title, message,
  timestamps — the message names the candidate and the assessment title, and
  nothing else (no email, token, score or invitation id).
- **Audit is unchanged.** Submission still writes `ASSESSMENT_COMPLETED` to
  Activities; starting has always been recorded by `started_at` and the
  `ASSESSMENT_STARTED` application status history. Notifications are additional.
- **Delivery is polling.** There is no server-push channel in this stack, so
  `NotificationToaster` (mounted in `RecruiterLayout` only) polls
  `GET /recruiter/notifications` every 30 s while the tab is visible, shows each
  new row with the existing toast (title + message), and acknowledges it via
  `POST /recruiter/notifications/read` so it is not delivered again. A backlog
  larger than five is folded into one summary toast. There is no bell/history UI
  yet: an acknowledged notification is retained in the table but not listed.

## 1. Design goal

Assessments are a reusable domain, independent of *why* a candidate was
invited (normal recruitment or campus drive) and, longer-term, independent
of *what kind* of question is being asked (MCQ now; aptitude, technical,
coding, subjective/AI-evaluated later). Implemented question types:
single- and multi-select MCQ, true/false, short answer, descriptive, and
coding (stored as text and graded by a person — **there is no code
execution engine**; running candidate code would need a separately
approved, sandboxed integration). The same assessments are used by normal
jobs, campus drives (`default_assessment_id`) and recruitment programs
(`docs/programs.md`) — never coupled to any one of them.

## 2. Entities and their responsibilities

```
Assessment            -- org-owned definition: title, instructions, duration, pass score
  └─ Question          -- belongs to one Assessment; type, prompt, points, order
       └─ QuestionOption   -- MCQ choices, is_correct flag (never sent to the candidate)

AssessmentInvitation   -- the secure, single-purpose link from ONE Application
                          to ONE Assessment
  └─ CandidateAttempt      -- one attempt at answering (bounded by max_attempts)
       └─ CandidateAnswer[]    -- one row per question answered
       └─ AssessmentEvaluation -- HOW it was scored (auto/AI/manual), when, by what
       └─ AssessmentResult     -- the durable scored outcome (score, pass/fail)
```

`AssessmentEvaluation` and `AssessmentResult` are deliberately separate
(mirroring the AI screening domain's Run/Result split in
`docs/ai-screening.md`): `AssessmentEvaluation` is the record of the scoring
*process* (useful once AI-evaluated subjective answers exist and evaluation
might be re-run or reviewed), `AssessmentResult` is the *current* scored
outcome a recruiter/candidate actually reads.

Questions belong to one `Assessment`; a test's question set is its own
snapshot. Reuse goes through the question bank (§ 10): a bank question is
*copied* into the test from one exact bank version, recorded in
`Question.source_bank_version_id`, so editing the bank never changes a test
or anyone's attempt.

Private grading data is kept apart from what candidates receive:
`QuestionOption.is_correct` and `QuestionAnswerKey` (accepted short
answers, case sensitivity, grading notes, explanation) are never part of a
candidate response schema (`app/schemas/public_assessment.py`).

**Draft / published.** `Assessment.status` is DRAFT or PUBLISHED. Only a
published assessment can be invited to, attached to a campus drive or used
by a program; it needs at least one question to be published, and can go
back to draft only while nobody has been invited. Once any invitation
exists, the question structure is locked (title/instructions/duration/pass
score stay editable); existing assessments were migrated as PUBLISHED.

## 3. Invitation lifecycle

States (from `docs/database.md` § 3.7):

```
NOT_SENT → SENT → [DELIVERED] → [OPENED] → STARTED → SUBMITTED
                                                    ↘ EXPIRED (if not started/submitted in time)
   → CANCELLED (recruiter action, any point before SUBMITTED)
```

`DELIVERED`/`OPENED` are populated only where the email provider supports
delivery/open webhooks — their absence never blocks progression to
`STARTED`/`SUBMITTED`, which are derived from the candidate's own actions
against `/api/v1/assessment/invitations/{token}/start` and `.../submit`.

Creating an invitation is always an explicit recruiter action (single or
bulk — see `docs/campus-hiring.md` § 3); it is never a side effect of adding
or importing a candidate.

## 4. Secure access mechanics

- Token: high-entropy random value, shown once (in the email), never
  persisted in plaintext. The DB stores `sha256(token)` only.
- Lookup path (`GET /api/v1/assessment/invitations/{token}`) hashes the
  incoming token and looks up `assessment_invitations.token_hash` — no
  candidate/assessment ID is trusted from the URL.
- Expired/cancelled/exhausted-attempts tokens return a generic
  "this invitation is no longer valid" response — never a detailed reason
  that could help an attacker distinguish "expired" from "wrong token" from
  "already submitted."
- Taking an assessment does **not** require a Candidate portal login —
  possession of the token is the authorization. This is required because
  campus-imported candidates may never register a portal account. A
  candidate who *does* have a portal account can still see the same
  invitation surfaced in "My Applications," but clicking through still uses
  the token link, not their session.
- Every lifecycle transition is audit-logged with the invitation `id` (never
  the token value) and actor context (system/candidate-via-token/recruiter).

## 5. Attempt & answer capture

- `CandidateAttempt.status = IN_PROGRESS` is created on `/start`; answers are
  captured incrementally via `/answers` (so a connectivity drop doesn't lose
  earlier answers), and `/submit` finalizes it (`status = SUBMITTED`,
  `submitted_at` set).
- `max_attempts` on the invitation bounds how many `CandidateAttempt` rows
  can exist for it; a new attempt beyond that limit is rejected.
- Server-side duration enforcement: `submitted_at - started_at` checked
  against `Assessment.duration_minutes` at evaluation time (not solely
  trusted from client-side timers) — a submission arriving after the
  server-computed deadline is flagged, not silently accepted as on-time.

## 6. Evaluation (automatic + manual grading)

`app/services/assessment_grading_service.py`. On submission each answer is
stored (`candidate_answers`: selected options or text) and graded if it can
be decided automatically:

- choice questions (MCQ, true/false): exact match against
  `QuestionOption.is_correct`; option ids that don't belong to the question
  are dropped, so a tampered payload can't earn marks;
- short answer: match against the answer key's accepted answers
  (case-insensitive unless the key is case-sensitive);
- descriptive, coding, and short answers with no accepted answers: left for
  a person (`awarded_points` NULL).

Negative marks (`Question.negative_points`, at most the question's marks)
apply only to automatically graded wrong answers; the total never drops
below 0.

`AssessmentResult` is written at submission. If anything awaits a person,
`grading_status = PENDING_MANUAL_REVIEW` and `passed` is NULL — no pass/fail
is shown or used (program gates treat it as "not passed yet"). Graders
(`assessment.grade`: ORG_ADMIN, RECRUITER, HIRING_MANAGER) use the attempt
review page (`GET /recruiter/assessments/attempts/{invitation_id}/review`,
`POST .../grades`; queue at `GET /recruiter/assessments/pending-review`) to
award 0..points per answer — the server refuses more — with an optional
staff-only comment. Grader, method (AUTO/MANUAL) and time are kept per
answer. When the last answer is graded the result becomes FINAL and
pass/fail is computed against the pass mark **fixed on the result at
submission** (`assessment_results.pass_score_applied`, migration
`5e6f70819203`; existing results were backfilled from their assessment), so
a pass/fail decision is reproducible and shown to HR as "X% against a pass
mark of Y%". An assessment's **pass mark and question set can't be changed
once it has been sent to anyone** (409 `pass_score_locked` / create a new
assessment instead). Candidates never see per-answer marks, comments or
keys.

## 7. Interaction with the application workflow

Invitation/attempt events call into `application_workflow.transition(...)`
(see `docs/recruitment-workflow.md`) rather than the assessment domain
writing to `Application.status` directly:

- Invitation assigned/prepared → `Application.status = ASSESSMENT_INVITED`
  (independent of whether the invitation has been emailed yet)
- Attempt started → `Application.status = ASSESSMENT_STARTED`
- Attempt submitted + evaluated → `Application.status = ASSESSMENT_COMPLETED`

This keeps the "who is allowed to move an application into which state"
rule in one place, even though the trigger originates in a different domain.

## 8. Email integration

Invitation email is **manual only**. Assigning an assessment
(`POST /recruiter/assessments/invite`) or authorizing a retest creates the
invitation and moves the application to `ASSESSMENT_INVITED`, but sends
nothing; `assessment_invitations.emailed_at` stays `NULL` ("Not emailed yet").

The recruiter then clicks **Send Assessment Invitation** on the application,
which opens the composer with the `ASSESSMENT_INVITATION` template
(`app/services/email_composer.py`): assessment name, deadline (the
invitation's expiry) and the candidate/job/company details are filled in, the
recruiter may edit the text, previews it, and sends. Because only
`sha256(token)` is stored, the raw link cannot be recovered later — so the
*send* issues a fresh personal link (the "Start the assessment" button), and
it replaces the previous token only if delivery succeeds. A failed send leaves
the existing link working. Only an invitation that is still `SENT` (not
started, submitted or expired) can be emailed. Delivery goes through the
`EmailProvider` interface; services never import a provider SDK directly
(`CLAUDE.md` § 2).

## 9. Importing questions from a file

`POST /api/v1/recruiter/assessments/parse-questions` (multipart file
upload) extracts candidate questions from a PDF, DOCX, XLSX, or CSV file
and returns them shaped exactly like `AssessmentCreateRequest.questions` —
**nothing is persisted by this endpoint.** The frontend shows the result as
an editable preview (select which questions to keep, edit/delete/reorder,
add more by hand) and only calls the existing
`POST /api/v1/recruiter/assessments` once the recruiter clicks "Create
assessment," so import and manual entry share one persistence path.

Parsing (`app/integrations/documents/question_import.py`) is deliberately
**rule-based, not AI** — free/local/open-source libraries only (`pypdf`,
`python-docx`, `openpyxl`, stdlib `csv`), consistent with "no paid API
dependency for convenience." It never guesses at a correct answer it can't
find explicitly in the document: a row/question with no resolvable correct
option is skipped and surfaced as a warning, not silently included with a
fabricated answer.

- **CSV/XLSX**: a header row with a `question` column, `option_1`/
  `option_2`/… (or `a`/`b`/`c`/…) columns, and a `correct` column naming the
  right option(s) by 1-based index, letter, or exact text — comma-separated
  for multi-select. Optional `type` and `points` columns.
- **PDF/DOCX**: numbered questions ("1. ...") with lettered options
  ("A) ..."), where the correct option is marked either with a trailing `*`
  or an explicit `Answer: B` / `Correct: B, C` line. In DOCX, Word's
  automatic list numbering counts the same as typed numbers (a numbered
  list for questions, a lettered list for options). Each option must be on
  its own line; options written side by side on one line are not split, so
  that question is skipped with a warning.

Malformed files (wrong type, missing required columns/structure) raise a
clear `QuestionImportError` rather than returning empty or guessed data. The same upload limits and content checks as the bank import apply
(§ 10a "Safety"), and short-answer/descriptive rows (via the `type` column)
can be imported here too.

## 10. Question bank and test builder

**Model** (`app/models/question_bank.py`, migration `2b3c4d5e6f70`), all
tenant-scoped with RLS:

```
QuestionCategory          -- subject / topic tree (parent_id), unique name among siblings,
                             deactivated not deleted. "Programming / Python",
                             "Mechanical / Robotics", ... — data, not code.
QuestionTag               -- free tags, unique per org (normalized)
QuestionBankItem          -- the reusable question: category, difficulty (EASY/MEDIUM/HARD),
                             status DRAFT / PUBLISHED / ARCHIVED, current_version_number,
                             created_by / updated_by
  └─ QuestionBankItemVersion   -- IMMUTABLE content: type, prompt, marks, negative marks,
                                  options (key + label)
       └─ QuestionBankAnswerKey -- private: correct option keys, accepted answers,
                                  case sensitivity, grading notes, explanation
QuestionBankItemTag       -- item <-> tag
```

Editing content creates a new version with its own answer key; metadata
(category, difficulty, tags) edits don't. Old versions are never changed.
Status moves follow `QUESTION_BANK_TRANSITIONS`; archived items are hidden
from search unless asked for.

**API** (`/api/v1/recruiter/question-bank`; `question_bank.read` to view —
ORG_ADMIN, RECRUITER, HIRING_MANAGER; `question_bank.manage` to change —
ORG_ADMIN, RECRUITER):

```
GET/POST   /categories, PATCH /categories/{id}
GET/POST   /tags
GET        /questions?q=&category_id=&type=&difficulty=&status=&tag_id=&include_archived=&limit=&offset=
POST       /questions
GET/PATCH  /questions/{id}
GET        /questions/{id}/versions
GET        /questions/{id}/preview     -- the candidate's view only (no keys, notes)
POST       /questions/{id}/status
```

Search is server-side and paginated (total in `X-Total-Count`).

**Test builder.** The assessment form (Assessments -> New / Edit) mixes
written questions with questions picked from the bank (filter by subject,
type, difficulty, tag, text), reorders them and overrides marks per test.
The picker lists only PUBLISHED items and always refetches when it opens
and when the window regains focus (plus a manual Refresh), so questions
published in the Question Bank — typically in another tab — appear
without reloading the page.
A bank question is sent as `bank_version_id` (+ optional `points`); the
server copies that exact version's content and answer key and ignores any
content sent with it. Only the *current* version of a *published* item from
the caller's organization can be added (another organization's id behaves
as unknown); a version already in the test may be kept when the test is
re-saved even if the bank has moved on. "Preview" shows the test exactly as
a candidate would see it (`GET /recruiter/assessments/{id}/preview`, no
answers). There is no random question selection: every candidate of a test
gets its fixed, ordered question set, so each attempt is auditable from the
test's snapshot.

**Not implemented:** AI-generated questions (when added, generated
questions must land as DRAFT bank items for HR review), random selection
from a pool, and code execution.

## 10a. Importing questions into the bank

Question Bank → **Import questions** (`QuestionImportDialog`). Same parser
as the assessment form's import (§ 9,
`app/integrations/documents/question_import.py`), extended with optional
columns; service `app/services/question_bank_import_service.py`.
Needs `question_bank.manage`.

```
GET  /api/v1/recruiter/question-bank/import/template   -- CSV template (UTF-8 with BOM, opens in Excel)
POST /api/v1/recruiter/question-bank/import/preview    -- multipart file -> what was read; saves nothing
POST /api/v1/recruiter/question-bank/import            -- {items, status: DRAFT|PUBLISHED} -> all saved, or none
```

**Formats**

| Format | Rules |
|---|---|
| CSV / XLSX | header row; `question` (required); `option_1`, `option_2`, … (or `a`, `b`, …); `correct` by number (`2`), letter (`B`), exact option text, or several separated by commas (`A,C`). Optional: `type` (`single`, `multiple`, `true_false`, `short_answer`, `descriptive`), `difficulty` (easy/medium/hard), `subject`, `topic`, `marks` (or `points`, 1–1000), `tags` (`;`-separated), `explanation`. `short_answer`: `correct` lists accepted answers separated by `\|`. `descriptive`: no options or answer. First sheet only for XLSX. |
| PDF / DOCX | numbered questions (`1.` / `1)`, or Word's automatic numbering), lettered options (`A)` / `A.`, or an automatic lettered list), one option per line, the correct option marked with a trailing `*` or an `Answer: B` / `Correct: A, C` line; `[2 marks]` after the question sets marks. Choice questions only. |

**Review step.** The preview lists every question read with its source
(`Row 4`, `Question 2`), its answer key, the subject/topic it will be filed
under (and whether that category will be created), and flags:

- **Not imported** — rows/questions that couldn't be read, each with the
  reason (no answer given, answer names a missing option, fewer than two
  options, bad marks/difficulty/type). Nothing is guessed.
- **Can't import** — read, but rejected by the bank's content rules (e.g.
  a single-choice question with two correct answers); the checkbox is
  disabled until the file is fixed.
- **Possible duplicates** — same text (ignoring case and spacing) as an
  existing bank question in the organization, or as an earlier row of the
  file. Shown unticked; HR may tick them deliberately.

HR chooses which questions to keep and whether they're saved as draft or
published. The import request is validated as a whole and saved in one
transaction: one invalid item rejects the request and nothing is stored.
Missing subjects/topics are created as categories (subject = top-level
category, topic = its child). One activity entry records the import.

**Safety.** 5 MB file limit (read bounded on the server), content must match
the extension (PDF magic bytes; DOCX/XLSX zip containing the expected part;
CSV text without binary content), at most 500 questions and 100 PDF pages
per file, DOCX/XLSX archives refused if they expand past 50 MB, encrypted
PDFs refused, macros never run, errors never include library exception
text. Files are parsed in memory and not stored; nothing is sent to an AI
or other external service; parsed answer keys only go back to the staff
user who uploaded the file. The template has no cell starting with
`=`, `+`, `-` or `@`.
