# Security Model

Status: Implemented — Argon2id password hashing, JWT access tokens, hashed
opaque refresh tokens with rotation/reuse detection, two-layer tenant
isolation (application-scoped queries + Postgres RLS, `app/db/rls.py`),
and hashed opaque tokens for assessment invitations (same pattern as
refresh tokens) all exist in code today, not just as a plan. This document
was the checklist that build was measured against; it has not been audited
end-to-end for a production/external deployment (see CLAUDE.md's "local
development demo" framing) — treat it as accurate for what's implemented,
not as a completed hardening sign-off.

## 1. Authentication

- **Password hashing: Argon2id** (`argon2-cffi`), never bcrypt for this
  project. Parameters (memory cost, iterations, parallelism) chosen per
  OWASP current guidance and set in `core/security.py`, not hardcoded inline
  at each call site.
- Two independent principal types — **User** (staff) and **Candidate** —
  each with their own login endpoint, JWT signing audience, and refresh
  token table. A token issued for one can never authenticate as the other,
  even if a route-level check were accidentally omitted, because the JWT
  `aud` claim is verified centrally in the auth dependency.
- Access tokens: short-lived JWT (~15 min), signed with a server-held secret
  (or asymmetric key — finalized in Phase 2), carrying `sub`, `aud`, `org_id`
  (for users; candidates carry their own `org_id` too, per the tenant-scoped
  candidate decision), and role claims.
- Refresh tokens: opaque random value, only the hash is stored
  (`user_refresh_tokens.token_hash` / `candidate_refresh_tokens.token_hash`),
  delivered as `httpOnly; Secure` cookies (`SameSite=None` in production
  because frontend and API are on different sites, `Lax` locally) — never
  accessible to frontend JS, never put in `localStorage`. Because
  `SameSite=None` cookies are sent on cross-site requests, the CORS allow-list
  (`CORS_ALLOW_ORIGINS`) must name the exact frontend origin, and browsers that
  block third-party cookies outright (e.g. Safari) will still break the silent
  refresh unless the API is served from the same site as the frontend. Rotated on every refresh;
  reuse of an already-rotated token revokes the entire token family
  (indicates token theft).
- **Assessment invitation tokens are a third mechanism**, deliberately not a
  login session — see § 5.

## 2. Authorization

- Every recruiter-facing endpoint requires both: (a) a valid permission for
  the action (`require_permission("...")`, resolved via the RBAC tables in
  `docs/database.md` § 3.1), and (b) that the specific resource being
  touched belongs to the caller's `organization_id`. Missing either is a
  failure, not a warning.
- Candidate endpoints only ever operate on `current_candidate.id`-owned
  resources — there is no "candidate views another candidate's application"
  code path, not even behind a permission check.
- Cross-tenant access to an existing resource returns **404**, not 403 —
  this avoids confirming to an unauthorized caller that a resource with that
  ID exists in another tenant.
- Frontend route guards and conditional rendering are UX only. Every
  decision they make is re-checked server-side; this is treated as a
  correctness requirement, not a suggestion (see `CLAUDE.md` § 2).

## 3. Tenant isolation

Defense in depth, per `docs/architecture.md` § 3:

1. Application-layer: `organization_id` required in every tenant-scoped
   service/repository call, sourced only from the authenticated principal.
2. Database-layer: Postgres Row-Level Security on every tenant-owned table,
   keyed to a per-request `SET LOCAL app.current_org_id`.
3. Explicit isolation tests from Phase 2 onward: for every tenant-owned
   resource type, a test asserting "Tenant A's token cannot read/write
   Tenant B's row," run as part of the standard test suite, not a one-off
   manual check.

## 4. Secrets & configuration

- All secrets (DB credentials, JWT signing key, email provider keys, AI
  provider keys) come from environment variables via `pydantic-settings`,
  never hardcoded, never committed. `.env` is gitignored; `.env.example`
  documents required variables with placeholder values only.
- No secret is ever logged, including in error/debug logs. Log redaction is
  applied to known-sensitive field names.

## 5. Assessment invitation security

- Token is a high-entropy random value (≥ 256 bits), generated server-side,
  shown to the candidate exactly once (in the invitation email link, which is issued when the recruiter sends the invitation).
- The database stores only `sha256(token)` (`token_hash`) — a leaked
  database backup does not expose usable tokens.
- Tokens expire (`expires_at`); expired-token access returns a generic
  "invitation no longer valid" response, not a detailed reason.
- `max_attempts` bounds how many `CandidateAttempt`s a single invitation can
  spawn; exceeding it is rejected.
- Every state transition (`SENT → OPENED → STARTED → SUBMITTED`, plus
  `CANCELLED`/`EXPIRED`) is written to `AuditLog` with actor context.
- Token values themselves are never written to `AuditLog` or application
  logs — only the invitation `id` and resulting status.
- No sequential/predictable ID is ever used as an authorization check —
  knowing an `assessment_invitations.id` (a UUID, and not accepted as a URL
  parameter for the candidate-facing endpoints anyway) is not sufficient;
  only possession of the raw token is.

### 5a. Candidate email verification (anonymous apply flow)

Flow and settings are in `docs/recruitment-workflow.md` § 6. The security
properties, all implemented in `app/services/email_verification_service.py`:

- **Secrets at rest:** a 6-digit code has only a million possible values,
  so a plain hash could be brute-forced offline. The code is therefore stored
  only as an HMAC-SHA256 keyed with the server secret. The verification
  token (an opaque bearer token, like the assessment tokens above) is stored
  only as its SHA-256. Neither is logged.
- **Guessing and flooding:** a code has a limited number of wrong attempts,
  and the counter is committed before the error is returned, so a rolled-back
  request can't reset it. Codes expire. Each email address has a resend
  cooldown and an hourly send cap, stored in the database so they hold across
  instances. Per-IP budgets sit in front of that.
- **Token binding:** a token only proves ownership of one email address at
  one organization. The same email's token from another organization, or a
  token presented with a different email, is rejected (422
  `email_not_verified`). It's single-use, and a submission blocked as a
  duplicate also uses it up, so it can't be replayed to test which mobile
  numbers are registered.
- **No enumeration before proof:** asking for a code never reveals whether
  an address has a profile. "Already registered" is only said once the
  caller has proven they own the address, and even then it never says which
  detail matched.
- **Tenant isolation:** `email_verifications` is tenant-scoped with the
  standard forced RLS policy. The organization is resolved from the public
  slug or drive token, and the tenant context is set before any query.
- **Known limitation:** the per-email hourly send cap stops inbox flooding,
  but it also means a third party can use up a victim's cap and delay that
  person's application by up to an hour. Changing this needs a product
  decision, for example relaxing the cap for a sender who can prove they own
  the address.

## 6. Input & file handling

- All request bodies validated via Pydantic — type, length, and format
  constraints declared on the schema, not checked ad hoc in handlers.
- Resume uploads: allow-listed content types (PDF/DOC/DOCX initially),
  server-side size limit, and content sniffing (not just trusting the
  client-supplied `Content-Type`) before accepting a file. Files are named
  by a generated storage key, never the client-supplied filename, and served
  back only through an authenticated download endpoint — never a raw
  filesystem/public URL.
- SQL injection: SQLAlchemy parameterized queries everywhere; no
  string-built SQL.
- XSS: React escapes by default; any place rendering user-supplied HTML
  (none planned) would require explicit sanitization — avoided by design
  (rich text fields, if ever added, render as sanitized markdown, not raw
  HTML).
- CORS: explicit allow-list of frontend origins per environment, not `*`.
- Rate limiting: applied first to unauthenticated/high-abuse-risk endpoints
  (login, register, assessment token lookup) in Phase 2/7; general API rate
  limiting considered in Phase 12 if needed. The anonymous candidate-intake
  endpoints (code request, code verify, apply) have per-IP budgets
  (`PUBLIC_*_LIMIT_PER_WINDOW`) on top of the database-backed per-email
  limits in § 5a.
- Candidate identity fields: mobile numbers are parsed and normalized to
  E.164 with `phonenumbers` (`app/core/phone.py`), not with hand-written
  rules. Date of birth must be in the past and give an age from 16 to 100.
  Languages must be real names (1–15 of them), and URLs are validated.

## 7. Data exposure boundaries

- Public API: job listing/detail fields only — no candidate data, no
  recruiter data, no counts that reveal pipeline volume.
- Candidate API: own data only; application status is a candidate-safe
  projection of the internal workflow state (see `docs/recruitment-workflow.md`),
  never raw recruiter notes or AI evaluation evidence.
- Recruiter API: tenant-scoped only; role/permission-gated within the
  tenant (e.g. an `INTERVIEWER` role is not expected to see compensation
  fields or other interviewers' private notes once that distinction is
  built — tracked as a future permission granularity, not implemented in
  Phase 2's initial role set).
- Team management (`/api/v1/recruiter/users`, `/departments`, `/employees`):
  `user.read`/`user.create`/`user.update`, `department.manage` and
  `employee.manage` are held by `ORG_ADMIN` and by HR, which is the
  `RECRUITER` role (migration `d7e8f9a0b1c2`). Anything that creates,
  promotes, demotes, deactivates or reactivates an `ORG_ADMIN`, or changes
  one's own role, additionally needs `user.manage_admins`. Only `ORG_ADMIN`
  holds that permission, and `app/services/user_service.py` enforces it, so
  team management can't escalate to admin. `HIRING_MANAGER` reads the
  department hierarchy only, and `INTERVIEWER` has no team access.
- Removing an employee who left (`POST /api/v1/recruiter/employees/{id}/delete`,
  `employee.manage`) is a soft delete: `deleted_at`, `deleted_by_user_id` and
  `deletion_reason` are set and the row is kept for history, but it drops out of
  every employee list, department count and the org chart. The caller can't
  remove the record linked to their own portal account, and a record linked to
  an `ORG_ADMIN` account needs `user.manage_admins`. Another tenant's employee
  is a 404. The linked portal login, if there is one, isn't touched; it's
  deactivated separately under Accounts.
- Deleting a team account (`DELETE /api/v1/recruiter/users/{id}`) needs
  `user.manage_admins` (`ORG_ADMIN` only) and works only on an account that is
  already deactivated. Never one's own account, never the organization's last
  `ORG_ADMIN`, never another tenant's user (404). The row is removed with a
  plain SQL `DELETE`, so the schema's foreign keys decide what happens to the
  account's data. These go with the login (`CASCADE`): role assignments,
  refresh tokens (every session ends; an old access token gets 401 because
  the user no longer exists), the account's own notification inbox, its
  calendar attendances, and its "Talk to Admin" thread. These stay, with the
  link cleared (`SET NULL`): activities (they keep the actor's name), status
  history, screening and matching runs, assessment invitations, `deleted_by`
  columns, notifications and messages it sent, and a linked employee record.
  An account that authored jobs, assessments, campus drives, employee
  records, notes or calendar events (`RESTRICT`) is refused with 409
  `user_has_history` and stays deactivated, so that history keeps its
  author. The deletion is recorded in Activities (`USER_DELETED`).
- Error responses never include stack traces, ORM/SQL error text, or
  internal file paths — mapped to safe, generic messages with a
  `request_id` for support/log correlation.

## 8. Audit logging

- Append-only `audit_logs` table (see `docs/database.md` § 3.10). Application
  code has no update/delete path for this table.
- Logged: authentication events, job/candidate/application create & status
  changes, assessment invitation lifecycle, screening runs, AI evaluations,
  campus drive actions, user/role changes.
- As implemented today, the organization-visible trail is the `activities`
  table (`app/models/activity.py`), readable with `activity.read` and — since
  the ORG_ADMIN activity-delete feature — removable with `activity.delete`.
  Both permissions are ORG_ADMIN-only. `DELETE /api/v1/recruiter/activities/{id}`
  filters by the caller's own `organization_id` inside the DELETE statement (and
  RLS backs it up), so another tenant's id is a plain 404; each removal is
  written to the structured application log (actor, activity id, action) but is
  not itself re-recorded as an activity. The bulk endpoints
  (`DELETE .../activities/bulk` for a selection, `.../activities/all` for the
  whole organization, which also needs an explicit `{"confirm": true}`) apply
  the same `organization_id` filter inside one DELETE statement: ids from
  another tenant or already deleted match nothing and are not counted, the
  response reports the number of rows actually removed, and one concise log line
  (counts, actor, organization — no per-row records) is written per request.
  Recruiters and hiring managers get 403 on all of them.
- Outbound email is audited in the same table: `CANDIDATE_EMAIL_SENT/FAILED`
  (application email) and `GENERAL_EMAIL_SENT/FAILED` (general email) record
  who sent which template with which subject to which addresses and whether it
  worked — never the message body and never any SMTP configuration.
- Never logged: passwords, raw tokens (access, refresh, or invitation),
  provider API keys, full request/response bodies of authentication
  endpoints.

## 9. Observability without over-building

Structured logs with request-correlation IDs, `/healthz` and `/readyz`, and
business-event logs feeding the audit table cover the initial build (see
`docs/architecture.md` § 11). A metrics/tracing stack is deferred to Phase 12
and only added if a concrete operational need justifies it.

## 10. Public AI assistant (Sigvi)

`POST /api/v1/public/ai/chat` is anonymous, so its safety comes from what the
model can reach and from input/rate limits rather than from authentication:
the model receives only curated public knowledge and public fields of OPEN
jobs (no tools, no queries, no tenant/candidate/recruiter/admin data); input,
history and output are bounded; requests are rate limited per client and
globally; replies are validated (prompt marker / key-shaped text / configured
key are never returned); provider errors are never forwarded; the Gemini key is
a `SecretStr` sent in a header; conversation text is never logged or stored.
Details and known limits: `docs/sigvi.md` §§ 5, 7, 10.
