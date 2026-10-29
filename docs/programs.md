# Recruitment Programs (MTech/PhD Campus Hiring, Paid Training & Company Funding)

**Implementation status:** implemented — configuration, publication,
candidate application with institution verification, the selection
workflow (test → interviews with recorded outcomes → selection), company
funding proposals with separate approval, the candidate's fee obligation and
payable amount, manual fee-payment and funding-applied records, full-time
training with milestones, agreement/bond tracking with frozen terms, the
employment review → offer → joining progression (linked to the employee
record), and a per-program report. Code: `app/models/program.py`,
`app/models/interview.py`, `app/services/program_service.py`,
`app/services/program_finance.py`, `app/services/interview_service.py`,
`app/services/public_program_service.py`, `app/workflows/program_workflow.py`,
`app/api/v1/recruiter/programs.py`, `app/api/v1/recruiter/interviews.py`,
`app/api/v1/public/programs.py`; frontend `features/recruiter/programs/` and
`features/careers/ProgramApplyPage.tsx`. Migrations `3c4d5e6f7081`,
`5e6f70819203`.

The intended sequence is: campus identification and verification →
application → assessment → result → interview invitation for qualifying
candidates → interviews → final selection → funding eligibility and
approval → training fee and funding arrangement → agreement and bond →
full-time training → training completion → employment review → offer →
joining. **Every step is an explicit, audited HR action; nothing advances on
its own.**

**Not implemented (by design):** a payment gateway, any automatic money
movement, generated agreement/bond wording, electronic signatures, and any
automated decision. Fee payments and applied funding are *records* that a
finance user enters after the fact (§ 5b).

## 1. Business rules encoded — and the ones deliberately left open

Encoded, because the business approved them:

- Target population: second-year MTech and PhD students (configured per
  program through eligible education levels and study years).
- Training is **paid by the candidate**, **full-time**, and leads towards
  full-time employment — it is not an internship (the carrier job has no
  employment type, see `docs/programs.md` § 2).
- Training duration options are only **6 and/or 12 months** (DB check
  constraint + schema validation).
- Funding **eligibility ≠ proposal ≠ approval ≠ funding applied** — separate
  fields/records (§ 5a). Passing the test or the interview, or being
  selected, never approves funding.
- Company funding may be a **fixed amount** or a **percentage of the
  training fee**; the candidate pays the rest.
- A bond (commitment) applies **only** to participants with approved
  company funding; the program default is **36 months** and HR may change it
  per program. It is never set on an unfunded participant.
- Nothing guarantees employment: passing the test, receiving funding or
  completing training never moves an application on by itself.

Left configurable (nullable) because they are **not finalized** — the
platform has **no default** for any of them:

| Value | Column | Until finalized |
|---|---|---|
| Training fee & currency | `training_fee_amount`, `training_fee_currency` | HR enters approved wording in `fee_disclosure`; funding can't be proposed and fees can't be fixed until the fee is set |
| Fee payment terms / due dates | `fee_payment_terms` (program), `fee_due_date` (participant) | omitted; no payment is ever "overdue" without a due date |
| Standard company funding basis (fixed or %) and value | `company_funding_type`, `company_funding_value` | empty; each proposal states its own basis |
| Funding range (min/max contribution) & currency | `scholarship_amount_min/max`, `scholarship_currency` | no range check; HR enters approved wording in `scholarship_disclosure` |
| Funding eligibility/approval policy | `scholarship_eligibility_rules` (text) | must be written before publishing |
| Whether one person may propose *and* approve | `funding_requires_independent_approval` | defaults to **on** (two people) |
| When the bond starts | `bond_start_rule` (program) / `bond_start_date` (person) | HR records the actual start date per person |
| Agreement wording and legal terms | stored as an approved document/reference, never generated | HR/legal approval required |
| Eligible specializations / institutions / study years / completion window | `eligible_*`, `expected_completion_*` | an empty criterion accepts anything |

## 2. Program configuration and publication

`RecruitmentProgram` (tenant-scoped) holds the configuration listed in § 1
plus: title/description, eligible education levels (`EducationLevel`:
DIPLOMA, BACHELORS, MASTERS, MTECH, PHD, OTHER), eligible study years,
specializations, eligible institutions (`recruitment_program_institutions` →
the institution registry, `docs/campus-hiring.md` § 1a), the applicant
institution-verification method (§ 3a), application open/close times, the
selection assessment (`assessment_id`, any published assessment from the
shared assessment domain), interview stages, required documents, candidate
disclosures with a `terms_version` label, and four switches for the
optional stages: `tracks_training`, `tracks_fee_payments`,
`tracks_employment`, `requires_agreement`.

The funding configuration is validated on every save
(`program_service.funding_config_problems`): type and value together or
neither, a percentage in (0, 100], a fixed amount no larger than a
configured fee, and the resulting contribution inside the configured range.

Each program owns a **carrier `Job`** (created with it, kept in DRAFT so it
never appears in the careers-site job list). Program applications are
ordinary `Application`s on that job, which is how every existing rule —
identity, one application per job, reapply window, AI screening, the
application pipeline, notes — applies unchanged.

Status lifecycle (`PROGRAM_STATUS_TRANSITIONS`):

```
DRAFT --publish--> PUBLISHED --pause--> PAUSED --resume--> PUBLISHED
DRAFT --archive--> ARCHIVED         PUBLISHED/PAUSED --close--> CLOSED
CLOSED --reopen--> PUBLISHED        CLOSED --archive--> ARCHIVED
```

A **draft may be incomplete.** Publishing (`program_service.publication_problems`)
requires: title and description; at least one education level; a training
duration; the finalized fee **or** approved fee wording; the finalized
funding amount/range **or** approved funding wording; funding eligibility
and approval rules; the employment pathway; bond duration and an approved
summary of its terms; candidate disclosures and a terms version; a
published (not deleted) selection assessment if one is attached; and a
close time after the open time. Changing any disclosed value (fee, funding
basis, bond…) after someone applied needs a new terms version.

## 3. Candidate application

Public, no login — `GET /api/v1/public/organizations/{slug}/programs` and
`/api/v1/public/programs/{id}` (detail, email one-time code, applicant
status, precheck, apply). The candidate page shows everything approved for
disclosure **before** the form: eligibility, duration and the fact that
training is paid, fee and funding (amounts/basis only if finalized,
otherwise the approved wording, always stated as "only if approved"), the
commitment, the employment pathway, the no-guarantee statement, and the
full terms text.

Applying *is* the careers-site self-service flow
(`docs/recruitment-workflow.md` § 6) on the program's job. On top of it the
candidate declares their current education (level, study year,
specialization, institution — picked from the registry list when the
program restricts institutions — and expected completion date) and accepts
the terms. `ProgramEnrollment` is created in the same transaction as the
application and stores the declaration, the accepted terms version and
`disclosure_snapshot` — the exact text the candidate agreed to.

### 3a. Institution (campus affiliation) verification

A program's `affiliation_verification_method` is NONE (not required),
EMAIL_DOMAIN or MANUAL (the default for new programs; programs created
before migration `5e6f70819203` keep NONE, i.e. unchanged behaviour). When
required, each application gets one `campus_affiliation_verifications` row
(`program_id` set — the same table campus drives use):

- **Never automatic from what the candidate typed.** EMAIL_DOMAIN verifies
  automatically only when the candidate's *verified* email is on one of the
  chosen registry institution's email domains; otherwise the record waits
  for HR. A typed (non-registry) institution name can only be verified
  manually.
- A manual decision (`POST …/enrollments/{id}/affiliation`) records the
  evidence checked — `STUDENT_ID_DOCUMENT`, `COORDINATOR_CONFIRMATION` or
  `MANUAL` — plus reviewer and time; REJECTED needs a note; PENDING reopens a
  decided record when the candidate supplies new evidence (resubmission).
- **No self-verification:** a reviewer whose account email is the
  candidate's email gets 403.
- The candidate can't be marked eligible until the membership is VERIFIED
  (`affiliation_verification_required`).

No student documents are uploaded to the platform for this; HR records what
they checked. Collecting ID documents would need a separate, approved
privacy decision.

## 4. Selection workflow

All transition tables and gates live in `app/workflows/program_workflow.py`.
`check_application_transition` adds the program's gates to every status
change of a program application:

| To application status | Requires |
|---|---|
| SCREENING, ASSESSMENT_INVITED, SHORTLISTED, INTERVIEW, SELECTED, HIRED | eligibility review = ELIGIBLE (which itself needs a verified institution, § 3a) |
| SHORTLISTED, INTERVIEW (program has a test) | latest attempt's result is FINAL and passed |
| SELECTED | the interview process recorded as complete (§ 4a) |
| HIRED (joined) | offer ACCEPTED |
| REJECTED | always allowed |

Test results are judged against the pass mark fixed on the result at
submission (`assessment_results.pass_score_applied`), and an assessment's
pass mark can't change once it has been sent (`docs/assessment.md` § 6).

### 4a. Interviews

Interview rounds are the platform-wide `application_interviews`
(`docs/recruitment-workflow.md` § 2a): stage, schedule, interviewer,
status (SCHEDULED / COMPLETED / CANCELLED / NO_SHOW), outcome (PASS / FAIL /
ON_HOLD), 1–5 rating and staff-only feedback. Scheduling the first
interview of a SHORTLISTED application moves it to INTERVIEW through the
pipeline (so the passed-test gate applies). `POST …/interview-completed`
succeeds only when nothing is still scheduled, at least one interview has a
recorded outcome, and every stage in the program's `interview_stages` has a
completed interview. **An outcome never selects or rejects anyone** —
selection stays an explicit application status change. The candidate is
invited with the existing Interview Invitation email template.

Stage labels (`derive_stage`) cover: eligibility review, eligible/not
eligible, assessment pending, test submitted, test passed — awaiting
interview, interview scheduled/completed, selected, funding eligible /
awaiting approval / approved / declined, training states, employment review
/ approved, offer, joined, not selected, not proceeding.

## 5. Money, training, agreement and employment

### 5a. Company funding (scholarship)

1. **Eligibility** (`scholarship-eligibility`, `program.manage`) — only for a
   SELECTED (or HIRED) candidate. Eligible → `scholarship_status = PENDING`
   ("awaiting proposal"). Not a promise of funding.
2. **Proposal** (`funding-proposals`, `program.manage`) — FIXED_AMOUNT or
   PERCENTAGE plus value. The participant's fee is fixed (§ 5b) and the
   contribution/payable amounts are computed by
   `app/services/program_finance.py` and stored on a
   `program_funding_revisions` row (status PROPOSED; one open proposal at a
   time, DB-enforced). Status → PROPOSED ("awaiting approval").
3. **Decision** (`funding-proposals/{id}/decision`,
   `program.finance.manage`) — APPROVED or REJECTED (reason required). With
   `funding_requires_independent_approval` the approver can't be the
   proposer; nobody can decide funding on their own application. Approval
   copies the contribution/payable onto the participant, starts agreement
   tracking (PENDING) when the program requires one, and records **why the
   bond applies** (`bond_applies_reason`). A proposal can also be withdrawn.
4. **Changes.** Before the candidate accepted the terms a new proposal is a
   REVISION; after acceptance it is an AMENDMENT and needs a reason. The
   approved terms stay in force until the new revision is approved, which
   marks the old one SUPERSEDED; an approved amendment clears the
   candidate's acceptance (renewed acceptance required). History is never
   edited.
5. **Funding applied** (`disbursements`, finance) — records of the
   contribution actually applied/paid; never more than the approved
   contribution, and a new revision can't approve less than has already
   been applied.
6. **Approvals recorded before migration `5e6f70819203`** (the old one-step
   decision) have an approved amount but no revision row. They keep
   working: the agreement snapshot records that approved amount
   (`funding_revision: null`), signing is allowed while no newer revision
   has been approved, and later proposals are revisions of it.

Money decisions on one participant (approval/withdrawal, payments, funding
applied) take a row lock, so concurrent requests can't both pass a check
that only one should.

Arithmetic (`program_finance.split_fee`, mirrored for previews in
`frontend/.../programs/finance.ts`): money is 2-decimal, percentages are of
the participant's fixed fee and rounded ROUND_HALF_UP, and
`candidate payable = fee − company contribution`, so the parts always add
up exactly. Example only (not policy): fee ₹100,000 at 40% → company
₹40,000, candidate ₹60,000.

### 5b. Fee obligation and payments

- The participant's **fee obligation** (`fee_amount`, `fee_currency`) is a
  snapshot of the program fee, fixed the first time it is needed (training
  enrollment when the program tracks fees, a funding proposal, or
  explicitly via `fee-obligation`, which also sets `fee_due_date`). Later
  program fee edits never change it.
- **Payments** (`program_fee_payments`, finance only): append-only manual
  records — amount, currency (must match the obligation), paid on, method
  (BANK_TRANSFER, UPI, CHEQUE, CASH, OTHER) and a **required** receipt /
  transaction reference as the confirmation. The total can't exceed the
  candidate's payable amount. A mistake is **voided with a reason**, never
  edited or deleted. No card numbers, bank accounts or credentials are
  collected.
- **Payment status** is derived, never stored: NOT_APPLICABLE (no
  obligation), FULLY_FUNDED (payable 0), UNPAID, PARTIALLY_PAID, PAID,
  OVERDUE (balance outstanding after the due date). *Waived* and *refunded*
  are not implemented (no approved policy; a waiver is expressed as 100%
  funding through the approval workflow).

### 5c. Training

`training_status`: NOT_ENROLLED → ENROLLED → IN_PROGRESS → COMPLETED, with
WITHDRAWN (candidate left) and DISCONTINUED (terminated by the company) from
ENROLLED/IN_PROGRESS (reason required). Enrolling needs one of the
program's durations (6/12), records `training_enrolled_at` and — once the
program's fee is finalized — fixes the fee obligation (while only approved
fee wording exists, enrollment still proceeds and finance fixes the
obligation later); the planned end defaults to start + duration; IN_PROGRESS needs
a start date; COMPLETED needs `training_completed_on` (not before the start).
Training can't change while a funding proposal is awaiting approval.

**Milestones** (`training_milestones`, `program.manage`) — title, optional
description and due date, PENDING / COMPLETED (with date) / MISSED, while
training is enrolled or in progress. *Overdue* = PENDING past its due date
(derived). The participants list shows overdue counts and the next due
date, with quick views for active trainees, overdue milestones, overdue
payments and completed training.

### 5d. Agreement and bond

`agreement_status`: NOT_REQUIRED → PENDING (preparing) → SENT (awaiting the
candidate's acceptance) → SIGNED (accepted) / DECLINED; VOID cancels an
unsigned agreement; a signed one ends as EXPIRED (only after its recorded
end date) or TERMINATED (reason required). DECLINED/VOID/TERMINATED need a
note.

- **SENT** needs approved funding and the approved agreement *version*
  label, and freezes `agreement_terms_snapshot` (fee, basis, contribution,
  payable, bond duration, funding revision, terms version).
- **SIGNED** needs the signing date and a reference or the attached signed
  copy, and is refused if the approved funding changed after sending
  (`agreement_terms_outdated`). Signing records the candidate's acceptance
  of that funding revision. This is a **record of a signature made
  elsewhere — not an electronic signature**.
- **Bond** (`bond`): only for approved funding with a signed agreement; HR
  enters the actual start date; the end date (and the agreement's
  effective/expiry dates) follow from the duration.
- If funding is amended after signing, the response flags
  `agreement_terms_current = false` and the snapshot still shows what was
  signed.

The platform never generates agreement or bond wording and never invents
penalties, repayment obligations or enforceability terms.

### 5e. Employment progression

`employment_review_status`: NOT_STARTED → IN_REVIEW → APPROVED /
NOT_PROCEEDING (reason required), NOT_PROCEEDING → IN_REVIEW to reconsider.

- IN_REVIEW (`program.manage`) needs a selected candidate and — when the
  program tracks training — COMPLETED training.
- APPROVED / NOT_PROCEEDING need `program.employment.approve`; approval needs
  the intended role and proposed start date (department optional, from the
  team hierarchy).
- An offer (`offer`: OFFERED → ACCEPTED / DECLINED / WITHDRAWN) needs an
  APPROVED review. Joining needs an accepted offer, moves the application to
  HIRED, and **links** the participant to the team-hierarchy `Employee` with
  the same email (or creates one with the approved role/department) — never
  a duplicate and never before joining.

## 6. Notifications

Program application receipt, assessment invitation, interview invitation
and selection emails reuse the existing templates and the manual composer
(`app/email_templates/templates.py`); **Scholarship Update** and **Training
Enrollment** templates cover funding and training updates. All are sent
manually by HR; no email is sent automatically by any program action, and
none contains answer keys, grading notes, interview feedback, internal
comments or amounts beyond what HR writes.

## 7. Permissions

| Permission | Default roles | Grants |
|---|---|---|
| `program.read` | ORG_ADMIN, RECRUITER, HIRING_MANAGER | view programs, participants, report |
| `program.manage` | ORG_ADMIN, RECRUITER | configure/publish; affiliation verification, eligibility, test invite, interview process complete, funding *eligibility* and *proposals*, training and milestones, starting the employment review, offer, joining |
| `program.finance.manage` | ORG_ADMIN | funding *approval/rejection*, fee obligation, payments, funding applied, terms acceptance, agreement, agreement document, bond |
| `program.employment.approve` | ORG_ADMIN, HIRING_MANAGER | approve / decline employment progression |
| `interview.read` | ORG_ADMIN, RECRUITER, HIRING_MANAGER, INTERVIEWER | list interviews; an interviewer records outcomes and sees feedback only for interviews assigned to them |
| `interview.manage` | ORG_ADMIN, RECRUITER, HIRING_MANAGER | schedule/cancel interviews, record any outcome, read all feedback |

Finance fields (fee, contribution, payable, balance, payment status,
payment and funding-applied records, terms snapshot) are omitted from
responses for users without `program.finance.manage`. Each participant
response carries the viewer's relevant permission codes
(`viewer_permissions`) so the UI only offers actions the server will
accept; the server re-checks every one. Separation of duties: no
self-verification of affiliation, no self-approval of one's own funding
proposal (when configured) or of funding on one's own application.

Every decision and record writes an activity-log entry
(`PROGRAM_*`, `INTERVIEW_*`, `CAMPUS_AFFILIATION_*`); interview feedback text
is kept on the interview, not in the organization-wide log.

## 8. Report

`GET /api/v1/recruiter/programs/{id}/report` — live counts over the
program's enrollments: applications, verification pending, eligibility
outcomes, test invited/passed/failed/awaiting grading, interview,
selected/not selected, funding eligible/awaiting approval/approved/declined,
fees settled, payments overdue, training enrolled/in progress/completed/
withdrawn/terminated, milestones overdue, employment review/approved/not
proceeding, offers made/accepted, joined, agreements signed — and, when all
fixed fees share one currency, money totals (fees, company contribution,
candidate payable, paid).

## 9. Decisions still needed from the business (HR / finance / legal)

- **Training fee amount, currency, payment schedule/due dates, and refund
  policy** (refunds and fee waivers are not implemented until a policy
  exists).
- **Company funding amount or percentage**, any range/cap, and which
  selected candidates qualify (the eligibility/approval policy text).
- Whether funding may be proposed and approved by the same person (the
  platform defaults to requiring two people).
- **When the bond starts** (selection, training start, joining…) and the
  **agreement and bond legal wording**, including any repayment or penalty
  terms — must be reviewed by legal; the platform stores the approved
  document/reference only.
- Whether student ID documents should be collected in-platform for
  affiliation verification (currently HR records what they checked).
- Whether specific specializations or institutions are restricted.
- Whether training-fee payments will ever be collected online (a gateway
  would be a new, separately approved integration).
