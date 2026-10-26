# Recruitment Programs (MTech/PhD Scholarship & Paid Training)

**Implementation status:** implemented — configuration, publication,
candidate application, the selection workflow, scholarship / training /
employment / agreement / bond tracking, manual fee-payment and disbursement
records, and a per-program report. Code: `app/models/program.py`,
`app/services/program_service.py`, `app/services/public_program_service.py`,
`app/workflows/program_workflow.py`, `app/api/v1/recruiter/programs.py`,
`app/api/v1/public/programs.py`; frontend `features/recruiter/programs/`
and `features/careers/ProgramApplyPage.tsx`. Migration `3c4d5e6f7081`.

A program is a dedicated recruitment track, not a job posting: candidates
apply, are reviewed for eligibility, take a selection test, are
interviewed and selected, may then be considered for a scholarship, pay for
and attend training, and may be offered full-time employment.

**Not implemented (by design):** a payment gateway, any automatic money
movement, generated agreement/bond wording, and any automated decision.
Fee payments and scholarship disbursements are *records* that an HR user
enters after the fact (§ 5).

## 1. Business rules encoded — and the ones deliberately left open

Encoded, because the business approved them:

- Training is **paid by the candidate** (the candidate page says so).
- Training duration options are only **6 and/or 12 months** (DB check
  constraint + schema validation).
- Scholarship **eligibility ≠ approval ≠ disbursement** — three separate
  fields/records (§ 4, § 5).
- Scholarship recipients agree to a commitment (bond); the default
  duration is **36 months** and HR may change it per program.
- Nothing guarantees employment: passing the test, receiving a scholarship
  or completing training never moves an application on by itself. Every
  state change is an explicit HR action. The candidate page and the terms
  text both state this.

Left configurable (nullable) because they are **not finalized**:

| Value | Column | Until finalized |
|---|---|---|
| Training fee & currency | `training_fee_amount`, `training_fee_currency` | HR enters approved wording in `fee_disclosure` |
| Fee payment terms | `fee_payment_terms` | omitted |
| Scholarship amount/range & currency | `scholarship_amount_min/max`, `scholarship_currency` | HR enters approved wording in `scholarship_disclosure` |
| Disbursement timing | `scholarship_disbursement_timing` | omitted |
| When the bond starts | `bond_start_rule` (program) / `bond_start_date` (person) | HR records the actual start date per person |
| Eligible specializations / institutions / study years / completion window | `eligible_*`, `expected_completion_*` | an empty criterion accepts anything |

The platform never fills any of these in, and never shows an amount the
business didn't enter.

## 2. Program configuration and publication

`RecruitmentProgram` (tenant-scoped) holds the configuration listed in § 1
plus: title/description, eligible education levels (`EducationLevel`:
DIPLOMA, BACHELORS, MASTERS, MTECH, PHD, OTHER — the intended target is
MTech and PhD, set per program), eligible study years, specializations,
eligible institutions (`recruitment_program_institutions` → the
institution registry, `docs/campus-hiring.md` § 1a), application open/close
times, the selection assessment (`assessment_id`, any published assessment
from the shared assessment domain), interview stages, required documents,
candidate disclosures with a `terms_version` label, and four switches for
the optional stages: `tracks_training`, `tracks_fee_payments`,
`tracks_employment`, `requires_agreement`.

Each program owns a **carrier `Job`** (created with it, kept in DRAFT so it
never appears in the careers-site job list). Program applications are
ordinary `Application`s on that job, which is how every existing rule —
identity, one application per job, reapply window, AI screening, the
application pipeline, notes, interviews — applies unchanged.

Status lifecycle (`PROGRAM_STATUS_TRANSITIONS`):

```
DRAFT --publish--> PUBLISHED --pause--> PAUSED --resume--> PUBLISHED
DRAFT --archive--> ARCHIVED         PUBLISHED/PAUSED --close--> CLOSED
CLOSED --reopen--> PUBLISHED        CLOSED --archive--> ARCHIVED
```

A **draft may be incomplete.** Publishing (`program_service.publication_problems`)
requires: title and description; at least one education level; a training
duration; the finalized fee **or** approved fee wording; the finalized
scholarship amount **or** approved scholarship wording; scholarship
eligibility/approval rules; the employment pathway; bond duration and an
approved summary of its terms; candidate disclosures and a terms version;
a published (not deleted) selection assessment if one is attached; and a
close time after the open time. The detail page lists outstanding items,
and a refused publish returns them in `error.data.problems`.

## 3. Candidate application

Public, no login — `GET /api/v1/public/organizations/{slug}/programs`
(published programs on the careers page) and `/api/v1/public/programs/{id}`
(detail, email one-time code, applicant status, precheck, apply). The
candidate page (`/programs/:programId`) shows everything approved for
disclosure **before** the form: eligibility, duration and the fact that
training is paid, fee and scholarship (amounts only if finalized, otherwise
the approved wording), that eligibility is not an award, the commitment,
the employment pathway, the no-guarantee statement, and the full terms text.

Applying *is* the careers-site self-service flow
(`docs/recruitment-workflow.md` § 6) on the program's job: verified email,
unique email and mobile, returning-candidate detection, one application per
job, reapply window, AI screening, welcome email. On top of it the
candidate declares their current education (level, study year,
specialization, institution — picked from the program's list when it has
one — and expected completion date) and accepts the terms. Before anything
is stored the server checks: the program is accepting applications, the
accepted `terms_version` is still current, and the declaration meets the
configured criteria. The declaration is also saved to the candidate profile
(`candidates.education_level`, `study_year`, `specialization`,
`institution_id`/`institution_name`, `expected_completion_date`).

`ProgramEnrollment` is created in the same transaction as the application
(one per application, unique) and stores the declaration, the accepted
terms version and timestamp, and `disclosure_snapshot` — the exact terms
text the candidate agreed to.

## 4. Selection workflow

All transition tables and gates live in `app/workflows/program_workflow.py`.
The application itself still moves through the platform pipeline
(`docs/recruitment-workflow.md` § 2); `check_application_transition` adds
the program's gates to every status change of a program application:

| To application status | Requires |
|---|---|
| SCREENING, ASSESSMENT_INVITED, SHORTLISTED, INTERVIEW, SELECTED, HIRED | eligibility review = ELIGIBLE |
| SHORTLISTED, INTERVIEW (program has a test) | latest attempt's result is FINAL and passed |
| SELECTED | interview recorded as completed |
| HIRED (joined) | offer ACCEPTED |
| REJECTED | always allowed |

The stage list in the brief maps as follows:

| Stage | Where it lives |
|---|---|
| Application received | `Application.status = APPLIED` |
| Eligibility review | `ProgramEnrollment.eligibility_status` PENDING → ELIGIBLE / NOT_ELIGIBLE (+ note, reviewer, time) |
| Test pending / passed / failed | assessment invitation + `AssessmentResult` (`passed` is NULL while answers await manual grading) |
| Interview scheduled / completed | `Application.status = INTERVIEW` (calendar for scheduling) / `interview_completed_at` |
| Selected / not selected | `Application.status` SELECTED / REJECTED |
| Scholarship eligibility | `scholarship_eligibility` NOT_DETERMINED → ELIGIBLE / NOT_ELIGIBLE — only for a SELECTED (or HIRED) candidate |
| Scholarship decision | `scholarship_status` NOT_STARTED → PENDING → APPROVED / DECLINED — only when eligible; approval needs an amount (within the program's range and currency, if set) |
| Training fee/payment | `program_fee_payments` (if `tracks_fee_payments`) |
| Training | `training_status` NOT_ENROLLED → ENROLLED → IN_PROGRESS → COMPLETED / DISCONTINUED, duration from the program's options |
| Offer and joining | `offer_status` NOT_OFFERED → OFFERED → ACCEPTED / DECLINED / WITHDRAWN, `joined_on` |
| Agreement / bond | `agreement_status` NOT_REQUIRED → PENDING → SENT → SIGNED / DECLINED / VOID; bond dates |

Training, fee-payment and employment actions are hidden in the UI and
refused by the server when the program's switch for them is off. Agreement
tracking starts when a scholarship is approved on a program with
`requires_agreement`: the agreement moves to PENDING and the program's
bond duration is copied onto the enrollment.

Candidates only ever see the platform's existing candidate-facing status
projection (`docs/recruitment-workflow.md` § 3) — never eligibility notes,
scores, answer keys or HR comments.

## 5. Scholarship, training, payment and agreement records

- **Approved amount** is on the enrollment (`approved_scholarship_amount`,
  `_currency`, decided by/at, note). APPROVED is final.
- **Disbursements** (`scholarship_disbursements`): SCHEDULED / PAID /
  CANCELLED rows with date and finance reference. Allowed only for an
  approved scholarship, and the total can never exceed the approved amount.
- **Fee payments** (`program_fee_payments`): append-only manual records —
  amount, currency, paid on, method (BANK_TRANSFER, UPI, CHEQUE, CASH,
  OTHER), receipt reference. A mistake is **voided with a reason**, never
  edited or deleted. No card numbers, bank accounts or credentials are
  collected or stored — the form asks for a receipt reference only.
- **Agreement**: the platform never generates agreement or bond wording. HR
  records the company-approved agreement's reference and/or attaches the
  signed copy (stored through the resume storage abstraction; Postgres keeps
  metadata only; download is permission-checked). SIGNED needs a signing
  date and a reference or document.
- **Bond**: recorded only for an approved scholarship recipient with a
  signed agreement. HR enters the actual start date (the business rule for
  when it starts is not finalized); the end date follows from the duration.

Every decision and record writes an activity-log entry.

## 6. Notifications

Two templates were added to the manual email composer
(`app/email_templates/templates.py`): **Scholarship Update** and **Training
Enrollment**. Like every other template they are sent manually by HR from
the application page and carry no amounts or terms of their own. Program
application receipt, assessment invitation, interview and selection emails
reuse the existing templates and flows — no duplicate sends were added. No
email contains answer keys, grading notes or internal comments.

## 7. Permissions

| Permission | Default roles | Grants |
|---|---|---|
| `program.read` | ORG_ADMIN, RECRUITER, HIRING_MANAGER | view programs, participants, report |
| `program.manage` | ORG_ADMIN, RECRUITER | create/edit/publish; eligibility, test invite, interview completed, scholarship *eligibility*, training, offer, joining |
| `program.finance.manage` | ORG_ADMIN | scholarship *decision*, disbursements, fee payments, agreement, agreement document, bond |

Finance records are omitted from enrollment responses for users without
`program.finance.manage`. The UI hides controls a role can't use; the
server enforces every one.

## 8. Report

`GET /api/v1/recruiter/programs/{id}/report` — live counts over the
program's enrollments: applications, eligibility outcomes, test
invited/passed/failed/awaiting grading, interview, selected/not selected,
scholarship eligible/pending/approved/declined, training
enrolled/in progress/completed, offers made/accepted, joined, agreements
signed.

## 9. Decisions still needed from the business

- Training fee amount, currency and payment/refund terms.
- Scholarship amount or range, currency, and disbursement timing.
- When the bond starts (selection, training start, joining…), and its
  final legal wording (stored as an approved document, never generated).
- Whether specific specializations or institutions are restricted.
- Whether training-fee payments will ever be collected online (a gateway
  would be a new, separately approved integration).
