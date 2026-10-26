# Campus / Mass Hiring

**Implementation status:** `CampusDrive` (`app/models/campus_drive.py`)
composes an existing `Job` (or creates one inline), and participation is
exactly `Application.campus_drive_id` with no separate membership table,
per § 2 below. Each drive has its own public, no-login application link
(§ 3). Campus reporting (§ 5) is real, computed from live data — both the
per-drive funnel endpoint and the reports overview breakdown. Every drive
belongs to an institution from the organization's institution registry
(§ 1a), and a drive can require applicants' affiliation to be verified
(§ 3a); the cross-drive campus report (§ 5) breaks results down by drive
and institution.

Not implemented: `eligibility_criteria` (jsonb filtering), `candidate_limit`
enforcement, and recruiter-side bulk/CSV candidate import — candidates
currently enter a drive only via its public application link, the general
career portal (if attached to the same job), or the one-at-a-time
candidate/application creation endpoints.

## 1. Model

```
CampusDrive
  organization_id
  job_id                  -- the opportunity being hired for (existing job,
                             or one created inline at drive-creation time —
                             see "job source" below)
  institution_id          -- the drive's institution (§ 1a), NOT NULL
  college_name            -- display snapshot of the institution's name
  affiliation_verification_method -- NONE / EMAIL_DOMAIN / STUDENT_LIST /
                             MANUAL (§ 3a), default NONE
  description
  batch_year / start_date / end_date / registration_deadline
  default_assessment_id   -- optional; if set, a candidate who applies is
                             automatically invited to it
  link_token_hash         -- SHA-256 hash of the opaque public application
                             token; the raw token is only ever returned once,
                             by create/regenerate-link (same pattern as
                             assessment invitations, docs/assessment.md § 4)
  status                  -- DRAFT / ACTIVE / PAUSED / CLOSED
```

A `CampusDrive` does not duplicate anything `Job` or `Assessment` already
model — it composes them with campus-specific scheduling metadata.

**Job source.** Creating a drive takes either an existing `job_id`, or
`new_job_title` + `new_job_description` to create the job inline in the
same request (`app/services/campus_drive_service.py::_resolve_job_id`) —
never both, never neither. This avoids the recruiter having to leave the
campus-drive flow to first create a job, without ever creating a duplicate
job behind the scenes.

**Institution source.** Creating a drive takes exactly one of
`institution_id` (an active institution from the registry),
`new_institution` (registers it in the same request), or — for older
clients — `college_name`, which is matched to (or registered as) an
institution by normalized name. A drive's institution can be changed only
while it has no applications; after that, a new drive is needed.
EMAIL_DOMAIN verification can only be chosen for an institution that has an
official email domain on record.

**Status lifecycle** (`CAMPUS_DRIVE_TRANSITIONS` in
`app/models/campus_drive.py`, enforced server-side, not just hidden in the
UI):

```
DRAFT   --Activate-->  ACTIVE
ACTIVE  --Pause-->     PAUSED
ACTIVE  --Close-->     CLOSED
PAUSED  --Resume-->    ACTIVE
PAUSED  --Close-->     CLOSED
CLOSED  --Reopen-->    ACTIVE
```

A drive is never deleted by a status change — `Close` and `Reopen` are
just further transitions, so closed drives stay in the recruiter's history
and their applications remain intact. Only `ACTIVE` drives accept new
public applications (§ 3); `DRAFT` drives 404 on their public link so an
unfinished drive can't be applied to before it's ready.

## 1a. Institution registry

`Institution` (`app/models/institution.py`, `app/services/institution_service.py`,
`/api/v1/recruiter/institutions`, Institutions page) is the organization's
list of colleges/universities — campus drives, program eligibility
(`docs/programs.md`) and candidate education declarations all point at it
instead of repeating free-text college names. Fields: name (unique per
organization after case/whitespace normalization), optional type
(UNIVERSITY, COLLEGE, INSTITUTE, OTHER), city/state/country, website,
official student email domain(s) — **optional; many institutions have
none** — placement contact name/email/phone, notes, active flag.
Institutions are deactivated, never deleted, so history stays intact.

Migration `1a2b3c4d5e6f` registered one institution per distinct existing
`college_name` and linked every existing drive to it; `college_name` was
left unchanged.

## 2. Candidates in a drive

There is deliberately **no** separate "drive membership" or
"drive candidate" table. A candidate's participation in a drive is an
`Application` row:

```
Application {
  candidate_id
  job_id = campus_drive.job_id
  campus_drive_id = campus_drive.id
  source = CAMPUS_IMPORT | PORTAL | RECRUITER_ADDED
  status = APPLIED (initial, regardless of how the candidate entered) —
           or fast-tracked straight to ASSESSMENT_INVITED if the drive has
           a default_assessment_id (see § 3)
}
```

Reasons this is not a separate entity: `Application` already models
"this candidate, this opportunity, current workflow state" — introducing a
parallel `DriveCandidate` table would mean every downstream step (screening,
assessment invitation, status reporting) would have to reconcile two
records for the same relationship. Per `CLAUDE.md` § 2, Candidate/Application
modeling rules apply identically inside campus hiring — no special case.

## 3. The public application link (no candidate login)

Each drive gets one shareable link, `https://<frontend>/campus-drive/<token>`
(`app/api/v1/public/campus_drives.py`, `app/services/public_campus_drive_service.py`).
Same opaque-bearer-token pattern as assessment invitations
(`docs/architecture.md` § 4): the raw token is shown to the recruiter once
(on create, or again via "Regenerate link," which invalidates the previous
one), only its SHA-256 hash is persisted.

```
GET  /api/v1/public/campus-drive/{token}                            -- drive/job/college details, no auth
POST /api/v1/public/campus-drive/{token}/email-verification/request -- email a one-time code
POST /api/v1/public/campus-drive/{token}/email-verification/verify  -- confirm it, get a verification token
POST /api/v1/public/campus-drive/{token}/apply                      -- verified token + full form + resume — no account
```

`apply` is the careers site's self-service flow
(`docs/recruitment-workflow.md` § 6), applied to the drive's job, with the
same identity rules: a verified email, the full mandatory form (including
date of birth, place of birth and languages), one profile per person by
email or normalized mobile number, and one self-service application per
person *across the careers site and every drive*. A duplicate is refused,
never merged or upserted. The `Application` is created with
`source=CAMPUS_IMPORT` and tied to the drive, the resume is saved, the
application is AI-screened against the drive's job, and a welcome email is
sent.

Only after that comes the campus-specific step. If the drive has a
`default_assessment_id` **and** the screening didn't screen the candidate out,
the application is fast-tracked through `UNDER_REVIEW → SCREENING` and an
`AssessmentInvitation` is created immediately. Its link comes back in the
response, so the candidate can take the assessment straight after applying.
An `AI_SCREENED_OUT` application is kept but never fast-tracked; HR decides
(override or reject). If the drive has no assessment attached, the
application stays at `APPLIED` for a recruiter to review.

**The drive comes from the link, never from the form.** The backend
resolves the drive (and therefore its institution) from the hashed link
token only. No apply request field can set or change the drive or the
institution — a tampered `campus_drive_id` / `institution_id` in the form is
ignored — so the `Application.campus_drive_id` and the affiliation record
always describe the drive the link belongs to. The same person can apply
through several permitted drives with one candidate profile, subject to the
usual one-application-per-job and reapply rules.

**Import/add never sends an assessment invitation** unless the drive was
explicitly configured with one — this is a hard requirement, not a
default-off setting: a recruiter adding a candidate one at a time never
gets a surprise auto-invite.

## 3a. Affiliation verification

Opening a campus link proves nothing about whether the applicant actually
studies at the institution. A drive's `affiliation_verification_method`
decides what happens (`app/services/campus_affiliation_service.py`):

| Method | On apply | Auto-verified when |
|---|---|---|
| NONE | nothing recorded (verification not required — **not** "verified") | — |
| EMAIL_DOMAIN | `CampusAffiliationVerification` PENDING | the candidate's *verified* email is on one of the institution's domains (exact or subdomain; `notcollege.edu` ≠ `college.edu`) |
| STUDENT_LIST | PENDING | the verified email is on the drive's college-provided student list (`campus_drive_roster_entries`); adding the list later verifies pending matches |
| MANUAL | PENDING | never — HR decides |

Whatever the method, HR (`campus_drive.manage`) can verify or reject any
record with a note; each record keeps status (PENDING / VERIFIED /
REJECTED), method, reviewer and time, and every change is written to the
activity log. The institution on the record is always the drive's.
Verification status is visible on the drive's Verification panel, in the
drive funnel and in the campus report. It does not block the application
pipeline by itself — HR decides what to do with a rejected record.

Not implemented, deliberately: uploading a student-ID or other proof
document. The three methods above avoid collecting identity documents; a
document method would need an approved retention policy first.

## 4. Recruiter-side management

```
POST   /api/v1/recruiter/campus-drives                    -- create (§ 1 job source)
GET    /api/v1/recruiter/campus-drives                    -- list (org-scoped)
GET    /api/v1/recruiter/campus-drives/{id}                -- detail
PATCH  /api/v1/recruiter/campus-drives/{id}                -- edit fields and/or status
GET    /api/v1/recruiter/campus-drives/{id}/funnel          -- real counts, see § 5
POST   /api/v1/recruiter/campus-drives/{id}/regenerate-link -- invalidates the old link
GET    /api/v1/recruiter/campus-drives/report[?institution_id=] -- cross-drive report, § 5
GET    /api/v1/recruiter/campus-drives/{id}/verifications  -- affiliation records, § 3a
POST   /api/v1/recruiter/campus-drives/{id}/verifications/{verification_id}/review -- verify/reject
GET    /api/v1/recruiter/campus-drives/{id}/roster         -- student list
POST   /api/v1/recruiter/campus-drives/{id}/roster         -- add students (email, roll no., name)
DELETE /api/v1/recruiter/campus-drives/{id}/roster/{entry_id}
GET    /api/v1/recruiter/campus-drives?institution_id=     -- list filtered by institution

GET    /api/v1/recruiter/institutions[?q=&active_only=]    -- registry, § 1a
POST   /api/v1/recruiter/institutions
GET    /api/v1/recruiter/institutions/{id}
PATCH  /api/v1/recruiter/institutions/{id}                 -- edit / deactivate
```

Reading needs `campus_drive.read`; every change needs `campus_drive.manage`.

## 5. Campus reporting

**Candidates in this drive** (drive detail page) reads the ordinary Applications
list with `campus_drive_id` — there is no drive-specific candidates API. It
supports server-side search (name, email, phone), a status filter,
qualification, and an applied-from/to range, with pagination, and the state is
kept in the page URL. The drive's own lifecycle (DRAFT / ACTIVE / PAUSED /
CLOSED) only governs the *public* apply link; a recruiter can always search a
drive's candidates, whatever its state.

Because campus participation is just `Application.campus_drive_id`, two
real (never hardcoded) views are available:

- **Per-drive funnel** (`GET /campus-drives/{id}/funnel`,
  `campus_drive_service.get_funnel_counts`): a `GROUP BY status` count over
  that drive's applications, keyed to the same `ApplicationStatus` values
  used everywhere else in the product — registered, screening,
  assessment_invited, assessment_completed, shortlisted, interview,
  selected, rejected, hired.
- **Cross-drive comparison** (`GET /api/v1/recruiter/reports/overview`,
  `campus_drives` field): total application count per drive (with its
  institution), for the Reports page's "Campus drives" chart.
- **Campus report** (`GET /campus-drives/report`, optionally
  `?institution_id=`; table on the Campus Drives page): one row per drive —
  institution, job, applications, verification pending/verified/rejected,
  assessment invited/completed/passed/failed/awaiting grading, interview,
  selected, hired, rejected. All live counts.
- The per-drive funnel also carries the verification counts.

The Applications list can be filtered by campus drive and by institution
(`campus_drive_id`, `institution_id` query parameters; both are filters in
the Applications page's Filters panel).

Downstream of `apply`, campus applications flow through the exact same
application workflow (`docs/recruitment-workflow.md`) as any other
application — there is no separate "campus status" vocabulary.
