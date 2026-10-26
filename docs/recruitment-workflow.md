# Recruitment Workflow

**Implementation status:** The `Application` status model below (§ 2) is
implemented exactly as specified, in `backend/app/workflows/
application_workflow.py`, and is what actually gates every status change in
the app (recruiter-triggered and assessment-triggered alike — see
`app/services/assessment_public_service.py`). `AssessmentInvitation.status`
is a separate, simpler enum (`SENT/STARTED/SUBMITTED/EXPIRED/CANCELLED`)
with its transitions handled directly in `app/services/assessment_service.py`
and `assessment_public_service.py`, not by a dedicated
`invitation_workflow.py` module (that split wasn't warranted at this size —
see `app/models/assessment.py`'s docstring for the full list of
simplifications from the original assessment design). § 5 ("Candidate
portal flow") describes a self-service candidate account that does not
exist yet — see `docs/ai-screening.md`/`docs/assessment.md`/
`docs/campus-hiring.md` implementation-status notes for what candidates
actually get today (an anonymous public apply flow and token-based
assessment links, no login). The anonymous apply flow — email verification,
one-profile-per-person identity rules, submission-time AI screening and HR
override/matching — is § 6.

## 1. Why a workflow module, not scattered status checks

`Application.status` is a finite-state value with rules about which
transitions are legal and who/what can trigger them. Per `CLAUDE.md` § 2
("Workflow state != ad hoc strings"), these rules live in one place:

- `backend/app/workflows/application_workflow.py`

It defines an explicit transition table (`{current_status: {allowed_next
statuses}}`) and a single `transition(application, to_status, actor, reason)`
function that: validates the transition is legal, writes the new status,
and appends an `ApplicationStatusHistory` row — so no call site can move an
application into an invalid state or skip the history write.

Applications that belong to a recruitment program get extra gates on top of
the table (eligibility review, passing selection test, completed interview,
accepted offer), checked by `app/workflows/program_workflow.py::check_application_transition`
on every status change — see `docs/programs.md` § 4.

## 2. Application status model

```
APPLIED
  → UNDER_REVIEW
  → REJECTED
  → AI_SCREENED_OUT          (system only — submission-time AI screening, § 6)

AI_SCREENED_OUT
  → UNDER_REVIEW             (HR override — reason required, audited)
  → REJECTED                 (HR confirms the screen-out)

UNDER_REVIEW
  → SCREENING
  → REJECTED

SCREENING
  → ASSESSMENT_INVITED       (if an assessment is required for this pipeline)
  → SHORTLISTED              (if no assessment required)
  → REJECTED

ASSESSMENT_INVITED
  → ASSESSMENT_STARTED
  → REJECTED                 (e.g. invitation expired without action — recruiter decision, not automatic)

ASSESSMENT_STARTED
  → ASSESSMENT_COMPLETED
  → REJECTED

ASSESSMENT_COMPLETED
  → SHORTLISTED
  → REJECTED

SHORTLISTED
  → INTERVIEW
  → REJECTED

INTERVIEW
  → SELECTED
  → REJECTED

SELECTED
  → HIRED

REJECTED, HIRED   (terminal)
```

Notes:

- `ASSESSMENT_STARTED`/`ASSESSMENT_COMPLETED` are driven by the assessment
  domain (see `docs/assessment.md`) calling into the workflow module when an
  invitation's `CandidateAttempt` starts/submits — the application workflow
  does not know about assessment internals, it only receives a
  transition request.
- Interview scheduling/feedback is out of scope for the initial phases
  (tracked as a future domain); `INTERVIEW` is a status the recruiter moves
  an application into/out of manually for now.
- `REJECTED`/`HIRED` are terminal — no code path transitions out of them. A
  mis-rejection is corrected by recruiter override (explicit, audited,
  permission-gated), not by "un-terminal-izing" a state silently.
- **`WITHDRAWN` was removed (SIGVITAS platform overhaul):** the platform no
  longer distinguishes a candidate-initiated withdrawal from a recruiter
  rejection — every prior `WITHDRAWN` application became `REJECTED`, and the
  value no longer exists in the enum (application code or database). `HIRED`
  was added as the new outcome after `SELECTED`, matching the product's
  "Selected → Hired" flow.
- **`AI_SCREENED_OUT` is advisory, never final, and system-only.** It is in
  `SYSTEM_ONLY_TARGETS`: only the automatic submission-time screening
  (`actor_user_id=None`) can set it — a person asking for it gets a 409. It
  is deliberately distinct from `REJECTED`. Moving out of it to anything but
  `REJECTED` is an *AI override* (`POST /recruiter/applications/{id}/ai-override`,
  or a normal status change): a reason is mandatory (422 `reason_required`
  otherwise) and an `AI_SCREENING_OVERRIDDEN` activity is recorded with it.
- **`ApplicationSource.HR_MATCH`** marks an application HR created by
  matching an existing candidate to another job (§ 6). It is a source, not a
  status: the new application starts at `APPLIED` like any other.

## 3. Candidate-visible projection

Candidates never see the raw internal status enum value or history reasons.
The candidate API maps internal status to a small, stable public vocabulary:

| Internal status | Candidate sees |
|---|---|
| APPLIED, UNDER_REVIEW | "Application Submitted" |
| SCREENING | "Under Review" |
| ASSESSMENT_INVITED, ASSESSMENT_STARTED | "Assessment Pending" |
| ASSESSMENT_COMPLETED | "Assessment Completed" |
| SHORTLISTED | "Shortlisted" |
| INTERVIEW | "Interview" |
| SELECTED | "Selected" |
| REJECTED | "Not Selected" |
| HIRED | "Hired" |

This mapping lives in the candidate-facing schema/serializer, not in the
workflow module itself — the workflow module's vocabulary is the operational
truth; the candidate projection is a presentation concern.

**Not implemented yet** — there is no candidate portal API (§ 5), so no
status is shown to candidates after they apply, and the table above has no
row for `AI_SCREENED_OUT` yet. After applying (§ 6) the candidate sees only a
neutral "Application Submitted" confirmation, identical whatever the AI
decided; the apply response carries no screening outcome at all. The only
place the screening result reaches the candidate is the welcome email, whose
variant depends on it. Neither the screen nor the email ever names the
internal status, the AI, its score or its reasoning.

## 4. Normal recruitment flow

```
Job created → requirements defined → sourcing/candidate application →
resume upload → resume parsing (Phase 5) → AI-assisted screening (Phase 6) →
recruiter review → assessment (optional) → interview → decision → reports
```

Sourcing is not a distinct backend entity in the initial phases — it is a
recruiter-portal view over candidates/applications filtered by `source` and
by "not yet applied" status, plus manual candidate add. If sourcing grows
dedicated needs (outreach sequences, external sourcing integrations), it
becomes its own domain at that point rather than being speculatively modeled
now.

## 5. Candidate portal flow

```
Browse public jobs → job details → Apply → register/login → complete profile →
upload resume → submit application → view status → (assessment if invited) →
interview → final decision
```

The "Apply" action from a public job detail page, if the visitor isn't
authenticated, routes through candidate register/login and returns to
complete the application — the Job the candidate was applying to is
preserved across that redirect (client-side state), not silently dropped.

## 6. Candidate intake (self-service application) — implemented

What a candidate actually does today, with no account: apply from the public
careers site, or from a campus drive link (`docs/campus-hiring.md` § 3).
Both entry points run the same code, `app/services/public_application_service.py`,
so the identity rules below are identical for both.

```
early "already applied?" check (email / mobile, before any code) →
verify email (one-time code) → fill the full form + resume → identity check
(one profile per person) → Candidate + Application + resume COMMITTED →
AI screening against the job → MATCH / AI unavailable: APPLIED
                              → NOT_MATCH: AI_SCREENED_OUT (kept, HR can override)
→ welcome email (both outcomes)
```

**1. Email verification (required).** The candidate asks for a 6-digit code
and then confirms it. Confirming it returns a short-lived verification token,
which the application form must send back. The browser never holds a
"verified" flag of its own. Everything is enforced server-side in
`app/services/email_verification_service.py`, and its state is stored in the
tenant-scoped `email_verifications` table. In the form, a valid code moves
the candidate straight on to the resume upload step (there is no separate
"verified" screen); a wrong or expired code keeps them on the verification
step with the server's message:

- the code is stored only as an HMAC keyed with the server secret, and the
  token only as its SHA-256. Neither is ever stored in plain text;
- a code expires (`EMAIL_OTP_TTL_MINUTES`) and allows a limited number of
  wrong guesses (`EMAIL_OTP_MAX_ATTEMPTS`), after which a new code is needed;
- resending has a cooldown (`EMAIL_OTP_RESEND_COOLDOWN_SECONDS`), and each
  email address has an hourly send cap (`EMAIL_OTP_MAX_SENDS_PER_HOUR`);
- the token expires (`EMAIL_VERIFICATION_TOKEN_TTL_MINUTES`) and is bound to
  one organization and one email address. It is used up by a successful
  submission, and also by a submission blocked as a duplicate, so it can't
  be replayed to test other mobile numbers;
- per-IP budgets apply in front of all of this
  (`PUBLIC_OTP_REQUEST_LIMIT_PER_WINDOW`, `PUBLIC_OTP_VERIFY_LIMIT_PER_WINDOW`,
  `PUBLIC_APPLY_LIMIT_PER_WINDOW`, per 10-minute window, per instance).

**0. Early "already applied" check.** As soon as the candidate has typed a
plausible email or mobile number, and has paused typing (400 ms debounce),
the form calls `POST .../applicant-status` (`/campus-drive/{token}/applicant-status`
for a drive link). The body is `{email?, phone?, job_id?}`, and the response
is only `{"already_applied": true|false}`
(`public_application_service.has_already_applied`). It's `true` when the
profile that email or mobile number belongs to:

- has an earlier application and no reapply permission: still inside the
  reapply window, with no HR grant (§ 3a, unchanged), for **any** job;
- already applied for this very job (one application per job); or
- was removed.

In that case the form shows "You have already applied." next to a disabled
Continue, and the candidate never reaches the code, resume or submit steps.
The backend enforces the same rule on its own. A code request for a blocked
email gets 409 `already_applied` with the same message, and no code is sent.
Verification, the precheck and the submission keep all their own checks. A
caller who hasn't verified the email learns nothing beyond that yes/no:
there's no name, date, job or reason. A returning candidate who *may* apply
gets their stored profile only in the verify response, after the code
checks out. The form uses it to fill in fields left blank, and never
overwrites what they typed. The check shares the
`PUBLIC_OTP_VERIFY_LIMIT_PER_WINDOW` per-IP budget.

Once the code is confirmed, the caller has proven they own the address, so a
remaining conflict is told at that point (for example 409 `already_registered`)
rather than after filling in the form.

**2. The form.** All of these are mandatory: full name (no length limit),
email, mobile number, date of birth (at least 16 years old), place of birth,
languages known (1–15), candidate type, current and preferred location,
qualification, and a resume. Optional: LinkedIn and GitHub URLs (validated
when given), 10th marks (percentage) and a 10th marks card (PDF/PNG/JPG, up
to 5 MB) — the card is candidate profile data, stored through the resume
storage abstraction and referenced from `candidates.tenth_marks_card_*`.
If the email already blocks a new application, the early check's "You have
already applied." appears directly below the Email field; nothing about the
existing profile (mobile number included) is shown before the one-time
code is verified, and the verified prefill then fills the stored mobile
number and marks into any field left blank. The database enforces the identity
part: `ck_candidates_verified_identity_complete` means a candidate with
`email_verified_at` set always has a phone, date of birth, place of birth and
at least one language. Recruiter-created candidates don't go through
verification, so for them these fields stay optional.

**3. Identity: one profile per person, one self-service application.**
Within an organization, a person is identified by email and by mobile
number. Mobile numbers are normalized to E.164 with `phonenumbers`
(`app/core/phone.py`). A number typed without a country code is read in
`DEFAULT_PHONE_REGION`, so "98765 43210", "09876543210" and "+91 98765-43210"
all count as the same person. The unique index `uq_candidates_org_phone` on
`(organization_id, phone)` enforces this. If either detail matches an
existing candidate, the submission is refused with a message that doesn't
say which detail matched and points to the organization's careers contact.
Nothing is created, and a `DUPLICATE_APPLICATION_BLOCKED` activity is
recorded on the existing profile for HR. Further roles also come from HR
(step 6).

**3a. The reapply window.** A candidate who has applied before may
self-apply again once `CANDIDATE_REAPPLY_COOLDOWN_MONTHS` (default 3)
calendar months have passed since their previous *self-service*
application. The rule lives in `app/services/reapply_service.py`:

- it reuses the same Candidate profile — no duplicate is created, and every
  previous application and its screening history is preserved;
- only self-service applications count. A candidate HR created, imported or
  matched to a role has no self-apply history and is never locked out by it;
- every self-service application counts whatever its status
  (`AI_SCREENED_OUT` included) and even if archived: the window is about
  when the person last applied, not how it went;
- inside the window the submission is refused with 409 `reapply_locked`,
  carrying `last_applied_at` and `eligible_from`. The candidate is told
  plainly when they can apply again. This is only ever raised to someone who
  has just verified their own email address, so naming their dates is safe;
- a `DUPLICATE_APPLICATION_BLOCKED` activity is recorded for HR, and the
  email verification token is used up, so it can't be replayed.

**HR override ("Grant Reapply Permission").** On the Candidates page, HR
(`candidate.reapply.grant`: ORG_ADMIN, RECRUITER) finds the candidate by
name, email or mobile (`GET /api/v1/recruiter/candidates/reapply-search`) and,
after a confirmation, calls `POST /api/v1/recruiter/candidates/{id}/grant-reapply`
(also offered on the candidate detail page). Only a candidate still inside
the window can be granted (409 `reapply_not_restricted` otherwise). The grant
permanently clears the candidate's **self-service** applications (careers
site and campus drive link) so the same email and mobile start over:
verification, resume upload, AI screening and a new reapply window.

- Deleted, per self-service application: the application; through ON DELETE
  CASCADE its status history, AI screening runs, assessment invitations
  (answers, monitoring events, invitation notifications) and notes on it;
  its resume rows (their embeddings cascade); every stored resume file no
  remaining resume row still points at; and the candidate's derived resume
  profile if it was built from one of those resumes (recomputed on demand).
- Kept: the Candidate profile; HR-created and HR-matched applications and
  any resume file they share (a shared file's embeddings are moved onto the
  surviving row); calendar events and match results (their application link
  is set to NULL); reapply grants; and the entire Activity log, plus a new
  `CANDIDATE_REAPPLY_PERMISSION_GRANTED` entry naming who granted it.
- One transaction: every delete and the activity are committed together,
  and stored files are removed only after that commit succeeds, so a
  failure part-way changes nothing. A file that can't be removed afterwards
  is logged, not surfaced (the rows are gone and the candidate can apply).

Single-use `CandidateReapplyGrant` rows written by the earlier "Allow
Reapply" action are no longer issued, but any still open are honoured: the
next self-service application spends one (`used_at`,
`used_by_application_id`, `CANDIDATE_REAPPLY_GRANT_USED`). `GET
.../reapply-status` reports the window and any open grant.

**4. Commit first, then screen.** The candidate, application and resume are
committed *before* the AI call and before any email. A slow or failed model,
or a failed email, can never lose an application.

**5. Submission-time AI screening.** The resume is screened against the
job's description by the configured `LLMProvider` (`docs/ai-screening.md`).
The `ScreeningRun` records `decision` (`MATCH` / `NOT_MATCH`) and the
`matched_requirements` / `missing_requirements` lists.

- `MATCH`: the application stays `APPLIED` for recruiter review, and the
  candidate gets the standard welcome email.
- `NOT_MATCH`: the system moves it to `AI_SCREENED_OUT` (§ 2). It is kept
  and visible to HR, and the candidate gets the "not eligible for this
  particular position" version of the welcome email.
- Either way the portal shows the same "Application Submitted" confirmation;
  the decision is never shown on screen.
- AI unconfigured or failing: the application stays `APPLIED` and an
  `AI_SCREENING_FAILED` activity is recorded. A failure never screens anyone
  out; a person decides.

In every case the candidate gets a welcome email. The screened-out version
says the profile isn't eligible for this particular role and is retained for
others. Neither version mentions AI, a score or reasons. Both use the
organization's `careers_contact_email` as reply-to and contact address,
when one is set. A failed email is recorded as an activity and never undoes
the application.

**6. HR decisions.**

- *Override:* `POST /api/v1/recruiter/applications/{id}/ai-override`
  (`application.status.change`, reason required) moves an `AI_SCREENED_OUT`
  application to `UNDER_REVIEW`. HR can instead confirm it as `REJECTED`.
- *Match to another job:* `POST /api/v1/recruiter/candidates/{id}/job-matches`
  (`application.create`) creates a *new* `HR_MATCH` application for the same
  candidate. The candidate's latest resume is attached to it, and the job
  must be `OPEN`, `DRAFT` or `ON_HOLD`. The original application is left
  untouched, and matching the same job twice returns 409.
- *History:* `GET /api/v1/recruiter/candidates/{id}/history` (needs both
  `candidate.read` and `screening.read`) returns every application, flagging
  the original one, with its screening runs and an activity timeline.
- *Add a resume + applying role:* `POST /api/v1/recruiter/candidates/{id}/applications`
  (`application.create`, multipart: `job_id`, `resume`, `run_screening`)
  creates a `RECRUITER_ADDED` application and runs the same AI screening
  gate a self-service submission gets — MATCH stays `APPLIED` for review,
  NOT_MATCH becomes `AI_SCREENED_OUT`, an AI failure leaves it `APPLIED`.
  It composes the existing pieces (`application_service.create_application`,
  `resume_service.save_resume`, `public_application_service.screen_new_application`)
  rather than duplicating them. If the candidate already has a resume-less
  application for that role, the resume is filled in there instead of a
  second one being created. No candidate OTP is involved: HR is
  authenticated staff acting within their own organization, and because the
  application is not self-service it never touches the reapply window.

All of these endpoints are tenant-scoped. An id from another organization
returns 404, exactly like an id that doesn't exist.

**7. Careers contact.** `organizations.careers_contact_email` is the
recruitment team's public contact address. Only a SUPER_ADMIN sets it
(`PATCH /api/v1/admin/organizations/{id}`; `null` un-publishes it). It is
published through `GET /api/v1/public/organizations/{slug}`, and it is never
hardcoded.
