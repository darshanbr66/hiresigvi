# API Architecture

Status: Implemented for the domains that exist (auth, admin/organizations,
recruiter jobs/candidates/applications/reports/screening/assessments/
campus-drives/notes, public career site + apply + assessment-taking). No
`/api/v1/candidate/*` audience exists yet — there is no candidate portal
login (see `docs/recruitment-workflow.md`'s implementation-status note).
The versioning/audience-partitioning convention below is exactly what's
implemented; the endpoint list further down predates several of the
domains actually built and was not kept in lockstep with every new route —
`GET /api/v1/*/openapi.json` (or `/docs`) is the authoritative live list.

## 1. Versioning & audience partitioning

All routes are under `/api/v1/`, further partitioned by audience so the
authorization requirement is visible from the URL:

```
/api/v1/public/...      anonymous
/api/v1/candidate/...   candidate JWT (audience="candidate")
/api/v1/assessment/...  invitation bearer token (not a login session)
/api/v1/recruiter/...   staff JWT (audience="user") + role/permission check
/api/v1/admin/...       staff JWT, SUPER_ADMIN only
```

A route handler never has to guess its own trust level — the prefix is the
contract, enforced by a FastAPI dependency attached at the router level for
each prefix (not per-endpoint, to avoid one endpoint being forgotten).

## 2. Representative endpoint surface (illustrative — finalized per-phase)

### Public
```
GET  /api/v1/public/organizations/{org_slug}                                -- name + careers_contact_email
POST /api/v1/public/organizations/{org_slug}/applicant-status              -- {email?, phone?, job_id?} -> {already_applied}
POST /api/v1/public/organizations/{org_slug}/email-verification/request     -- {email, job_id?} -> 202, code emailed; 409 already_applied
POST /api/v1/public/organizations/{org_slug}/email-verification/verify      -- {email, code, job_id?} -> {verification_token, existing_profile, ...}
GET  /api/v1/public/organizations/{org_slug}/jobs
GET  /api/v1/public/organizations/{org_slug}/jobs/{job_id}
POST /api/v1/public/organizations/{org_slug}/jobs/{job_id}/apply             (multipart)
GET  /api/v1/public/organizations/{org_slug}/programs                       -- published recruitment programs
GET  /api/v1/public/programs/{program_id}                                   -- approved disclosures, eligibility, terms
POST /api/v1/public/programs/{program_id}/applicant-status | email-verification/request | email-verification/verify
POST /api/v1/public/programs/{program_id}/apply/precheck | apply             (multipart: careers form + education declaration + terms)
```
Programs: `docs/programs.md` § 3.
`apply` needs `email_verification_token` (from `verify`), `full_name`,
`email`, `phone`, `date_of_birth`, `place_of_birth`, `languages` (1–15),
`candidate_type` (`FRESHER`|`EXPERIENCED`), `current_location`,
`preferred_location`, `qualification` and a `resume`. `full_name` has no
length limit. `years_experience`, `notice_period_days`, `immediate_joiner`,
`current_title` and `current_company` are optional. `EXPERIENCED` requires
`years_experience` and either `notice_period_days` or `immediate_joiner`; a
`FRESHER` is stored with 0 years and no notice period. `linkedin_url` and
`github_url` are optional, but when supplied must be http(s) on linkedin.com
/ github.com; `phone` is normalized to E.164.

Optional education fields: `tenth_marks_percentage` (0–100, at most two
decimals) and `tenth_marks_card` (file: PDF, PNG or JPG, checked by
extension *and* file signature, at most `MAX_MARKS_CARD_SIZE_MB`, default
5 MB; 400 `invalid_file_type` / `file_too_large` / `invalid_file`
otherwise, before the verification token is spent). The card is stored
through the resume storage abstraction (GridFS in production) and only
referenced from the candidate row; its location is never returned. A
returning candidate's new card replaces the old one (the old file is
deleted after the commit); applying without one keeps the stored card. A
submission that fails before it is committed deletes the card it stored.
The verified-email prefill (`existing_profile`) includes
`tenth_marks_percentage` but nothing about the stored card.

A person gets one profile and one self-service application per
organization, matched by email or normalized mobile number. A duplicate is
refused with 409 `already_registered` and nothing is created; an existing
profile is never updated or merged by an anonymous submission. The response
is `{id, job_title, candidate_email, confirmation_email_sent,
careers_contact_email, submitted_at}`, and it is the same whatever the AI
screening decided. It never includes a screening outcome, the internal
status, AI decision, score, skills or reasoning; any decision reaches the
candidate by email only. Errors: 422 `email_not_verified`, `otp_invalid`,
`otp_expired`, `otp_incorrect`; 429 `otp_locked`, `otp_resend_throttled`,
`otp_send_limit`, `rate_limited`; 409 `already_registered`. The full flow is
in `docs/recruitment-workflow.md` § 6. Campus drive links have the same three
endpoints under `/api/v1/public/campus-drive/{token}` (`docs/campus-hiring.md`
§ 3).

Only published (`status=OPEN`) jobs, minimal fields (title, department,
location, employment_type, description, requirements summary).

```
POST /api/v1/public/ai/chat        (JSON)   -- Sigvi, the public AI assistant
```
Anonymous, rate-limited, stateless: `{message, conversation_id?, history?,
organization_slug?}` -> `{conversation_id, message, sources[], jobs[]}`. Full
contract, limits and error codes in `docs/sigvi.md` § 3.

### Candidate
```
POST /api/v1/candidate/auth/register
POST /api/v1/candidate/auth/login
POST /api/v1/candidate/auth/refresh
POST /api/v1/candidate/auth/logout
GET  /api/v1/candidate/me
PATCH /api/v1/candidate/me
POST /api/v1/candidate/me/resume
GET  /api/v1/candidate/applications
POST /api/v1/candidate/jobs/{job_id}/apply
GET  /api/v1/candidate/applications/{application_id}
```
Application detail exposes a candidate-safe status view (current stage,
timestamps) — never raw internal `ApplicationStatusHistory.reason`,
recruiter notes, or AI evaluation evidence.

### Assessment (token-bearer, not session-based)
```
GET  /api/v1/assessment/invitations/{token}
POST /api/v1/assessment/invitations/{token}/start
POST /api/v1/assessment/invitations/{token}/answers
POST /api/v1/assessment/invitations/{token}/submit
```
`{token}` is the raw invitation token; the server looks it up by
`sha256(token)` against `assessment_invitations.token_hash`. See
`docs/assessment.md` and `docs/security.md` for the full token lifecycle.

### Recruiter
```
GET/POST      /api/v1/recruiter/jobs
GET/PATCH     /api/v1/recruiter/jobs/{job_id}
GET/POST      /api/v1/recruiter/candidates
GET/PATCH     /api/v1/recruiter/candidates/{candidate_id}
GET           /api/v1/recruiter/candidates/{candidate_id}/history      (candidate.read + screening.read; every application,
                                                                         original flagged, screenings, timeline; 404 across tenants)
POST          /api/v1/recruiter/candidates/{candidate_id}/job-matches  (application.create; {job_id, reason?} -> 201 new HR_MATCH
                                                                         application; 409 if already matched; 404 across tenants)
POST          /api/v1/recruiter/candidates/{candidate_id}/applications (application.create; multipart: job_id, resume,
                                                                         run_screening=true -> 201 RECRUITER_ADDED application
                                                                         + AI screening run; fills the resume in on an existing
                                                                         resume-less application for that role instead of
                                                                         duplicating it)
GET           /api/v1/recruiter/candidates/{candidate_id}/reapply-status (candidate.read; when the candidate may self-apply
                                                                         again, plus any open HR grant)
GET           /api/v1/recruiter/candidates/reapply-search?q= (candidate.reapply.grant; active candidates whose name or
                                                                         email contains q, or whose mobile contains q's digits
                                                                         (max 20), each with its reapply-window standing)
POST          /api/v1/recruiter/candidates/{candidate_id}/grant-reapply (candidate.reapply.grant; clears the candidate's
                                                                         self-service applications and their data so the same
                                                                         email + mobile can apply afresh -> 200
                                                                         {candidate_id, full_name, email,
                                                                         cleared_application_count}; 404 unknown/other tenant/
                                                                         deleted; 409 `reapply_not_restricted` if nothing to lift)
GET           /api/v1/recruiter/applications                  (application.read; searched, filtered, sorted and paged
                                                                in the database — see "Applications list" below)
GET/PATCH     /api/v1/recruiter/applications/{application_id}
POST          /api/v1/recruiter/applications/{application_id}/status
POST          /api/v1/recruiter/applications/{application_id}/ai-override  (application.status.change; {reason} required;
                                                                             AI_SCREENED_OUT -> UNDER_REVIEW, else 409)
POST          /api/v1/recruiter/applications/{application_id}/screening-runs
GET           /api/v1/recruiter/applications/{application_id}/screening-runs
POST          /api/v1/recruiter/applications/{application_id}/notes
GET/POST      /api/v1/recruiter/assessments
GET/POST      /api/v1/recruiter/assessments/{assessment_id}/questions
POST          /api/v1/recruiter/applications/{application_id}/assessment-invitations
POST          /api/v1/recruiter/assessment-invitations/{invitation_id}/resend
GET/POST      /api/v1/recruiter/campus-drives
POST          /api/v1/recruiter/campus-drives/{drive_id}/candidates
POST          /api/v1/recruiter/campus-drives/{drive_id}/assessment-invitations:bulk
GET           /api/v1/recruiter/reports/funnel
GET           /api/v1/recruiter/reports/campus/{drive_id}
GET           /api/v1/recruiter/admin-messages/unread-count  (Talk to Admin; counts whichever side the caller is on)
GET/POST      /api/v1/recruiter/admin-messages/mine           (admin_message.send; the caller's own thread with the
                                                                organization's admins — no conversation id is accepted)
POST          /api/v1/recruiter/admin-messages/mine/read
GET           /api/v1/recruiter/admin-messages/conversations  (admin_message.manage; the admin inbox, own organization only)
GET           /api/v1/recruiter/admin-messages/conversations/{conversation_id}
POST          /api/v1/recruiter/admin-messages/conversations/{conversation_id}/messages
POST          /api/v1/recruiter/admin-messages/conversations/{conversation_id}/read
GET/POST      /api/v1/recruiter/users
DELETE        /api/v1/recruiter/users/{user_id}               (user.manage_admins, ORG_ADMIN; 204. Deactivated accounts
                                                                only: 409 user_active if active, 409 user_has_history if
                                                                the account authored jobs/assessments/campus drives/
                                                                employee records/notes/calendar events (data.authored
                                                                has the counts), 403 cannot_delete_self / last_org_admin,
                                                                404 for another tenant's or an unknown user)
GET           /api/v1/recruiter/auth/me                       (includes organization_name)
GET           /api/v1/recruiter/activities                    (activity.read, ORG_ADMIN)
GET           /api/v1/recruiter/activities/count              (activity.read, ORG_ADMIN; the org's total)
DELETE        /api/v1/recruiter/activities/{activity_id}      (activity.delete, ORG_ADMIN; 404 across tenants)
DELETE        /api/v1/recruiter/activities/bulk               (activity.delete; body {"activity_ids": [...]} max 500;
                                                                returns {"deleted": n} — only rows of the caller's org)
DELETE        /api/v1/recruiter/activities/all                (activity.delete; body {"confirm": true} required;
                                                                deletes every entry of the caller's own org)
GET           /api/v1/recruiter/notifications                 (any signed-in org user; the caller's own UNREAD in-app
                                                                notifications, newest first, `limit` 1-50 default 20;
                                                                SUPER_ADMIN -> 403)
POST          /api/v1/recruiter/notifications/read            (body {"ids": [...]} max 100; acknowledges only the
                                                                caller's own unread rows, returns {"updated": n};
                                                                idempotent, foreign/unknown ids are ignored)
GET           /api/v1/recruiter/email-templates               (application.email.send; each template lists `fields`
                                                                for an application email and `general_fields` — null
                                                                when it needs an application — for a general email)
POST          /api/v1/recruiter/email/compose                 (general email, no application; nothing sent)
POST          /api/v1/recruiter/email/preview                 (draft + to/cc/bcc; nothing sent)
POST          /api/v1/recruiter/email/send                    (general email to typed addresses: 1-10 in each of
                                                                to/cc/bcc, 20 total, validated & de-duplicated;
                                                                same 422/503/502 errors as application send)
POST          /api/v1/recruiter/applications/{application_id}/email/compose   (load a template; nothing sent)
POST          /api/v1/recruiter/applications/{application_id}/email/preview   (render the draft; nothing sent)
POST          /api/v1/recruiter/applications/{application_id}/email/send      (the only way a candidate is emailed;
                                                                                422 unresolved_placeholders /
                                                                                503 email_not_configured / 502 email_delivery_failed)
```
### Applications list (`GET /api/v1/recruiter/applications`)

One endpoint serves the Applications page, a Campus Drive's "Candidates in this
drive" (`campus_drive_id`), a candidate's own applications (`candidate_id`) and
reports. Everything is decided server-side; paging is applied **after**
filtering. The body is a plain list; the number of matches ignoring paging is
in the **`X-Total-Count`** response header (exposed through CORS).

| Parameter | Meaning |
|---|---|
| `q` | Up to 5 words (max 100 chars); **every** word must match the candidate's name, email or phone (digits-only comparison, so `98765 43210` finds `+91 98765-43210`) **or** the job title. `%` and `_` are literal. |
| `job_id`, `status`, `source`, `campus_drive_id`, `candidate_id` | Exact matches. `status` and `source` are enums (`source`: `PORTAL`, `RECRUITER_ADDED`, `CAMPUS_IMPORT`, `REFERRAL`, `OTHER`, `HR_MATCH`; `status` includes `AI_SCREENED_OUT`); unknown values are a 422. |
| `candidate_type` | `FRESHER` / `EXPERIENCED`. |
| `current_title`, `current_company`, `location` (current), `preferred_location`, `qualification` | Case-insensitive "contains" (max 255). |
| `min_experience`, `max_experience` | Years, 0–80, inclusive. Candidates with no experience recorded never match a bound. `min > max` is a 422. |
| `max_notice_period_days` | "Up to N days", 0–365. Candidates with no notice period recorded don't match. |
| `immediate_joiner` | `true` / `false`. |
| `applied_from`, `applied_to` | `YYYY-MM-DD`, whole days in **UTC**, both ends inclusive. `from > to` is a 422. |
| `sort_by`, `sort_dir` | `created_at` (default) / `applied_at` / `candidate_name` / `job_title` / `status` (pipeline order, not alphabetical); `asc` / `desc` (default `desc`). Ties are broken by id, so pages never repeat or skip rows. |
| `limit`, `offset` | `limit` 1–100; **omitted = every match** (internal callers such as reports rely on it). `offset` ≥ 0. |

All filters combine with AND. Deleted applications are never returned. Rows
also carry `candidate_phone`. Results are always scoped to the caller's
organization (query + RLS).

Added with campus institutions, the question bank and programs (details
in the linked docs):

```
/api/v1/recruiter/institutions[/{id}]                         -- docs/campus-hiring.md § 1a
/api/v1/recruiter/campus-drives/report, /{id}/verifications, /{id}/roster   -- § 3a, § 5
/api/v1/recruiter/applications?campus_drive_id=&institution_id=
/api/v1/recruiter/question-bank/{categories,tags,questions}   -- docs/assessment.md § 10
/api/v1/recruiter/assessments/{id}/status | /{id}/preview | /pending-review
/api/v1/recruiter/assessments/attempts/{invitation_id}/review | /grades     -- § 6
/api/v1/recruiter/programs[/{id}] , /{id}/status, /{id}/report, /{id}/enrollments[/{eid}/<action>]  -- docs/programs.md
    <action>: eligibility, affiliation, invite-assessment, interview-completed, scholarship-eligibility,
              funding-proposals[/{rev}/decision|/withdraw], funding-acceptance, fee-obligation,
              fee-payments[/{id}/void], disbursements[/{id}], training, milestones[/{id}],
              employment-review, offer, joining, agreement[/document], bond
/api/v1/recruiter/applications/{id}/interviews[/{interview_id}/outcome|/close]   -- docs/recruitment-workflow.md § 2a
```

All resolve `organization_id` from the authenticated principal — never from
a path/query/body parameter — and enforce the specific permission for the
action via a dependency (e.g. `require_permission("application.status.change")`).

### Admin
```
GET/POST /api/v1/admin/organizations
PATCH    /api/v1/admin/organizations/{org_id}      (SUPER_ADMIN; {careers_contact_email} — null or "" un-publishes it)
```

## 3. Conventions

- **Errors**: consistent envelope —
  `{"error": {"code": "...", "message": "...", "request_id": "..."}}` with
  the correct HTTP status. No internal exception messages or stack traces in
  the body; those go to structured logs keyed by `request_id`.
- **Pagination**: cursor or offset/limit (finalized in Phase 3 when the first
  list endpoints ship), consistent shape:
  `{"items": [...], "total": N, "next_cursor": "..."}`.
- **Validation**: every request/response is a Pydantic model; no raw `dict`
  bodies.
- **HTTP status codes**: `201` on resource creation, `204` on
  no-content-success (e.g. delete), `409` on conflict (e.g. duplicate
  application), `422` on validation error (FastAPI default), `403` vs `404`
  chosen deliberately for cross-tenant access attempts (see
  `docs/security.md` — default to `404` for resources outside the caller's
  tenant, to avoid confirming existence).
- **Idempotency**: bulk operations (e.g. bulk assessment invitation send)
  accept a client-supplied idempotency key to make retries safe.
