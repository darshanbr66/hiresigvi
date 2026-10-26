# Developer Guide

The starting point for an engineer new to this repository: how the pieces
fit, where code belongs, and how to run and change things safely. Domain
detail lives in the linked documents; the file-by-file map is
[`component-inventory.md`](component-inventory.md). Binding project rules
are in [`../CLAUDE.md`](../CLAUDE.md).

## 1. Architecture at a glance

A **modular monolith**: one FastAPI backend, one React SPA, one PostgreSQL
database (with pgvector). Full design rationale: [`architecture.md`](architecture.md).

```
Browser (React 19 SPA, Vite)
  ├─ public career site + candidate flows      /, /org/:slug, /campus-drive/:token, /programs/:id, /assessment/:token
  ├─ recruiter portal                           /recruiter/*
  └─ platform admin                             /admin/*
        │  JSON over HTTPS, frontend/src/lib/apiClient.ts
        ▼
FastAPI  backend/app/main.py
  api/v1/{public,recruiter,admin}/*.py   thin route handlers: auth, permissions, (de)serialization
  services/*.py                          business logic + transactions
  workflows/*.py                         legal state transitions (application, program)
  models/*.py  schemas/*.py              SQLAlchemy entities / Pydantic request-response shapes
  integrations/{ai,email,storage,documents}   providers behind interfaces
        │
        ▼
PostgreSQL (+ RLS)   ·   resume/document storage (local disk or MongoDB GridFS)
```

## 2. Where code belongs

| Concern | Put it in | Never in |
|---|---|---|
| HTTP shape, permission dependency, reading the upload | `app/api/v1/<audience>/<domain>.py` | — |
| Rules, validation beyond field types, multi-step changes | `app/services/<domain>_service.py` | route handlers |
| "Can X move from state A to B?" | `app/workflows/` transition tables | scattered `if`s |
| Field types/limits, request/response contracts | `app/schemas/` (Pydantic v2) | models |
| Tables, columns, constraints | `app/models/` **and** an Alembic migration | `create_all()` |
| Calls to email/AI/storage/file parsing | `app/integrations/` behind an interface | services/routes directly |
| Data fetching, cache keys | `frontend/src/features/<feature>/api.ts` + TanStack Query | components calling `fetch` |
| Hiding a button a role can't use | the page component (UX only) | — the server must enforce it anyway |

Frontend checks are presentation; **every** permission, tenant and
workflow rule is enforced by the backend ([`security.md`](security.md) § 2).

## 3. Authentication, authorization, tenancy

- **Staff** sign in at `/recruiter/login`: a short-lived JWT access token
  (15 min) held in memory, plus an httpOnly `refresh_token` cookie
  (30 days). `frontend/src/features/auth/AuthContext.tsx` owns the session.
- **Candidates** never log in. They prove control of an email with a
  one-time code (`email_verification_service`) and take tests with an
  opaque invitation token stored only as a hash ([`security.md`](security.md) § 5, § 5a).
- **Authorization** is permission-based: `require_permission("x.y")` in
  `app/api/deps.py`, resolved through roles (SUPER_ADMIN, ORG_ADMIN,
  RECRUITER, HIRING_MANAGER, INTERVIEWER). Default grants ship as data
  migrations; per-domain tables are in [`programs.md`](programs.md) § 7,
  [`assessment.md`](assessment.md) § 6/§ 10 and [`campus-hiring.md`](campus-hiring.md) § 4.
- **Tenant isolation** is enforced twice: every service query filters by the
  caller's `organization_id`, and PostgreSQL row-level security policies
  (`app/db/rls.py`) back it up. Another organization's id behaves like an
  unknown id (404). Public endpoints resolve the tenant from the slug, drive
  token or program id, then set the tenant context ([`security.md`](security.md) § 3).

## 4. Main workflows (summaries — follow the links for rules)

- **Candidate application & reapply** — verified email → one profile per
  person (email or normalized mobile) → one application per job →
  reapply window / HR grant → AI screening → pipeline.
  [`recruitment-workflow.md`](recruitment-workflow.md) § 2–6.
- **Campus drives & institutions** — a drive belongs to an institution
  from the registry; its public link decides the drive (never the form);
  optional affiliation verification by email domain, student list or HR
  review; campus report by drive and institution.
  [`campus-hiring.md`](campus-hiring.md).
- **Question bank & import** — versioned questions with private answer
  keys; bulk import from CSV/XLSX/PDF/DOCX with review before save; tests
  copy exact versions. [`assessment.md`](assessment.md) § 10, § 10a.
- **Assessments & grading** — draft/publish, token invitations, attempts,
  automatic grading of objective answers, manual grading of
  descriptive/coding answers, pass/fail only when grading is final.
  [`assessment.md`](assessment.md) § 2–8.
- **Scholarship & paid-training programs** — eligibility → test → interview
  → selection → scholarship eligibility → approval → training → employment,
  with manual fee/disbursement records and agreement/bond tracking; no
  invented amounts. [`programs.md`](programs.md).
- **Email & notifications** — recruiter emails are manual (composer +
  templates, `app/email_templates/`), sent through `EmailProvider`
  (Resend or SMTP, `app/integrations/email/`); the only automatic emails
  are the verification code and the application welcome. In-app
  notifications: `in_app_notification_service`. README § Running locally
  lists the configuration.
- **Files** — resumes, 10th-marks cards and signed program agreements go
  through `ResumeStorage` (`app/integrations/storage/`: local disk or
  MongoDB GridFS, chosen by `RESUME_STORAGE_PROVIDER`); Postgres stores
  metadata only and storage paths are never returned to clients. Question
  import files are parsed in memory and never stored.

## 5. Error handling

**Backend.** Services raise `AppError` subclasses (`app/core/exceptions.py`:
`NotFoundError` 404, `ConflictError` 409, `UnprocessableError` 422,
`ForbiddenError` 403, `UnauthorizedError` 401, `TooManyRequestsError` 429, …)
with a sentence written for the end user and a stable `code`. Every error
response has the same envelope:

```json
{ "error": { "code": "user_active", "message": "…", "request_id": "…", "details": [], "data": {} } }
```

Pydantic validation failures become `validation_error` with per-field
`details`. Unexpected exceptions are logged with the request id and
returned as a generic `internal_error` — never a stack trace or SQL.

**Frontend.** `frontend/src/lib/apiClient.ts` is the only place a response
becomes an error. It throws `ApiError` whose `message` has already been
normalized by `frontend/src/lib/apiErrors.ts::friendlyMessage`:

| Situation | Shown to the user |
|---|---|
| Authored business message (409/422/403/404 from `AppError`) | kept as written |
| Codes with product wording (`CODE_MESSAGES`, e.g. `user_active`) | that wording |
| `validation_error` with field details | "Question 3, option 2, label: This field is required." |
| 401 (not a failed sign-in) | "Your session has expired. Please sign in again." |
| Framework defaults ("Not Found", "Forbidden"), 5xx, non-JSON bodies | generic, actionable text |
| Browser couldn't connect | "We couldn't reach the server. Check your connection and try again." |
| Anything that looks internal (exception names, SQL, file paths) | generic text |

`ApiError` also carries `kind` (validation, auth, permission, not_found,
conflict, rate_limit, network, server), `fieldIssues` (plain-language
per-field messages for forms), the original `serverMessage` and the
`requestId` for support. Pages show errors with `<Alert>`
(`role="alert"`, announced by screen readers) inline next to the action,
or a toast for background actions — not both for the same failure.

## 6. Theme and design tokens

All colour, radius, shadow, type and motion values are CSS custom
properties in `frontend/src/styles/tokens.css`. Components use tokens, not
raw colours. Light is the default palette; dark is a separate, designed
palette (neutral graphite surfaces, hairline borders, brand blue only for
actions/links, desaturated status colours), applied when
`data-theme="dark"` is set, or when the OS prefers dark and the user hasn't
chosen. `features/theme/ThemeContext.tsx` stores an explicit choice in
`localStorage` and stamps `data-theme`. Form controls use
`--color-input-border` / `--color-input-bg` (3:1 against their surface).
`prefers-reduced-motion` disables animation globally (end of `tokens.css`).
Visual language: [`design-system.md`](design-system.md).

## 7. Local development

Prerequisites, environment variables and first-time setup: README §
Running locally. Day to day:

```bash
# backend (from backend/)
./.venv/Scripts/python run.py                          # API on http://127.0.0.1:8000
./.venv/Scripts/python -m pytest -q                    # full suite (uses the local DB inside rolled-back transactions)
./.venv/Scripts/python -m pytest -q tests/test_question_bank_import.py   # one file
./.venv/Scripts/python -m ruff check app tests alembic

# frontend (from frontend/)
npm run dev                                            # http://localhost:5173
npm test -- --run                                      # Vitest, 50% of CPU threads (vite.config.ts)
npm run typecheck
npm run lint                                           # oxlint
npm run build
```

Backend tests run against the database in `backend/.env`, each test inside
an outer transaction that is rolled back, so they leave no rows behind
(`tests/conftest.py`). They never send real email or call real AI providers
(conftest blanks those settings).

## 8. Database changes

1. Change the model in `app/models/`.
2. `./.venv/Scripts/python -m alembic revision --autogenerate -m "…"`,
   then **read and edit** the generated file: data backfills, RLS
   (`enable_tenant_rls`), permission seeds and a working `downgrade()` are
   never generated for you.
3. `./.venv/Scripts/python -m alembic upgrade head`, run the tests.
4. `./.venv/Scripts/python -m alembic check` must report "No new upgrade
   operations detected" — models and migrations agree.
5. Keep one head: `./.venv/Scripts/python -m alembic heads`.

Rules: never edit an applied migration, never `create_all()`, no destructive
step without a guard ([`database.md`](database.md) § 6), and don't run
migrations against a database you didn't intend to (check the host in
`backend/.env`). Current head and recent revisions: [`database.md`](database.md) § 6.
