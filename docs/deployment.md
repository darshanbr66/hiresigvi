# Deployment (Render + Vercel)

**Status: preparation only.** This document describes how to deploy the
SIGVITAS recruitment platform — backend to Render, frontend to Vercel,
database to Render PostgreSQL. Nothing described here has been executed
against a production environment as part of writing this document; no
Render/Vercel resources, secrets, or DNS records have been created.

## 0. Architecture recap

- **Backend**: FastAPI (`backend/`), Python 3.12, SQLAlchemy 2 (async,
  psycopg3), Alembic migrations. Fully configured through environment
  variables (`app/core/config.py`) — no hardcoded hosts, ports, or
  credentials anywhere in the app.
- **Frontend**: React 19 + Vite (`frontend/`), a single-page app using
  `react-router-dom`'s `BrowserRouter`.
- **Database**: PostgreSQL. The schema uses native enums, Postgres
  Row-Level Security, and pgvector (`resume_chunks.embedding`, see § 2).

---

## 1. Create the Render PostgreSQL database

1. Render dashboard → **New** → **PostgreSQL**.
2. Choose a plan/region. Note the database name, user, and the
   **Internal Database URL** (for the Render web service) and **External
   Database URL** (for running Alembic from your own machine, e.g. the
   first `alembic upgrade head`).
3. Render's connection string looks like:
   `postgres://<user>:<password>@<host>/<database>`

   **This app requires the `postgresql+psycopg://` scheme** (SQLAlchemy's
   async psycopg3 driver). Rewrite it before use:

   ```
   postgres://user:pass@host/db   →   postgresql+psycopg://user:pass@host/db
   ```

   This is the single most common way to break the deploy — the app will
   fail to start (or Alembic will fail to connect) if the scheme isn't
   rewritten. `app/core/config.py` and `.env.example` both call this out.

## 2. Enable the pgvector extension

pgvector is **required**. Migration `c4d5e6f7a8b9` (internal AI matching
engine) runs `CREATE EXTENSION IF NOT EXISTS vector` and creates
`resume_chunks.embedding` as `vector(768)` (Gemini `gemini-embedding-001`
dimensions); the ORM model (`app/models/resume_chunk.py`) maps the same
column. `alembic upgrade head` fails on a server where the extension is not
available. No HNSW/IVFFlat index exists yet.

Render's managed Postgres ships pgvector, and the database owner Render
creates can enable it, so the migration enables it itself — no manual step
is needed. To confirm before migrating (with `psql` over the External
Database URL):

```sql
SELECT default_version FROM pg_available_extensions WHERE name = 'vector';
```

After migrating, `SELECT extversion FROM pg_extension WHERE extname =
'vector';` should return a version. Minor version differences between
environments (e.g. 0.8.6 locally, 0.8.7 on Render) are compatible with this
schema.

## 3. Configure Render environment variables

Create these on the Render **web service** (not the database). See
`backend/.env.example` for the authoritative list with inline explanations.
`render.yaml` (repo root) lists every key the Blueprint expects; anything
marked `sync: false` there must be filled in by hand in the dashboard —
nothing sensitive is stored in the Blueprint file itself.

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | Yes | Render's connection string, rewritten to `postgresql+psycopg://` (§ 1). Internal vs External form and the required `sslmode=require&channel_binding=disable` parameters: § 3.1. |
| `JWT_SECRET_KEY` | Yes | Generate with `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Never reuse the local dev value. |
| `JWT_ALGORITHM` | No (default `HS256`) | |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No (default `15`) | |
| `REFRESH_TOKEN_EXPIRE_DAYS` | No (default `30`) | |
| `ENVIRONMENT` | **Yes, for login persistence** | Set to `production`. Gates `Settings.is_production`, which makes the refresh-token cookie `Secure; SameSite=None` (see `app/api/v1/recruiter/auth.py`). Without it the cookie is `SameSite=Lax` and is not sent on the Vercel → Render refresh call, so users are logged out on every page reload. |
| `DEBUG` | Recommended | Set to `false`. |
| `LOG_LEVEL` | No (default `INFO`) | |
| `CORS_ALLOW_ORIGINS` | Yes | JSON array, e.g. `["https://<your-vercel-domain>"]`. See § 5. |
| `FRONTEND_BASE_URL` | Yes | The same Vercel origin — used to build assessment/campus-drive links shared with candidates. |
| `RESUME_STORAGE_PROVIDER` | Recommended | Set to `mongodb_gridfs` in production. See § 11. |
| `RESUME_STORAGE_DIR` | No (default `uploads/resumes`) | Only used when `RESUME_STORAGE_PROVIDER=local`. Ephemeral on Render's default filesystem — see § 11. |
| `MAX_RESUME_SIZE_MB` | No (default `10`) | |
| `MONGODB_URI` | Yes, if `RESUME_STORAGE_PROVIDER=mongodb_gridfs` | Connection string for the MongoDB deployment storing resume files (e.g. an Atlas cluster). Never commit the real value. |
| `MONGODB_DATABASE` | No (default `ai_recruitment`) | Database name within the MongoDB deployment; the `resumes` GridFS bucket is created inside it. |
| `RESEND_API_KEY` | **For email in production** | **Secret** — a Resend API key (`re_…`); set only in the Render dashboard (`sync: false`). Render's free web services block outbound SMTP (ports 25/465/587), so production email must use Resend's HTTPS API. Never commit it. |
| `EMAIL_FROM` | **For email in production** | The Resend `From:` — an address on a domain you have **verified in Resend**, optionally with a display name: `SIGVITAS <hr@yourdomain.com>`. |
| `SMTP_HOST` | Local development only | SMTP server, e.g. `smtp.gmail.com`. Ignored whenever `RESEND_API_KEY` + `EMAIL_FROM` are both set. |
| `SMTP_PORT` | No (default `587`) | `587` = STARTTLS, `465` = implicit TLS. |
| `SMTP_USERNAME` | For email | SMTP login (also usually the from address). |
| `SMTP_PASSWORD` | For email | **Secret** — set only in the Render dashboard (`sync: false` in `render.yaml`). For Gmail this must be an app password. Never commit it, never put it in `.env.example`. |
| `SMTP_FROM_EMAIL` | For email | The `From:` address. |
| `SMTP_FROM_NAME` | No (default `AI Recruitment Platform`) | Display name on the `From:` header. |
| `SMTP_USE_TLS` | No (default `true`) | STARTTLS on non-465 ports. |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | No | Optional, free/local AI screening — not reachable from Render, only useful if you run Ollama somewhere Render can reach it. |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | No | Optional paid AI screening providers. Do not set unless you intend to enable AI screening. |
| `GEMINI_API_KEY` | **For the Sigvi assistant** | **Secret** — a Google Gemini API key (free tier: <https://aistudio.google.com/apikey>); set only in the Render dashboard (`sync: false`). Unset ⇒ Sigvi answers "temporarily unavailable"; nothing else is affected. Never commit it. |
| `GEMINI_MODEL` | No (default `gemini-3.5-flash-lite`) | The Gemini model Sigvi and the internal AI use. Does not affect resume screening. |
| `GEMINI_SCREENING_MODEL` / `GEMINI_SCREENING_THINKING_BUDGET` | No (defaults `gemini-2.5-flash` / `0`) | The Gemini model used for AI resume screening (when Gemini is the screening provider), and its thinking budget (`0` = thinking off; empty = send no `thinkingConfig`). See `docs/ai-screening.md`. |
| `GROQ_API_KEY` | For Groq-primary screening | **Secret**: a Groq API key (<https://console.groq.com/keys>). Set it only in the Render dashboard (`sync: false`). When set, Groq is the primary resume-screening provider, and Gemini (if `GEMINI_API_KEY` is set) gets one fallback attempt when Groq fails. Never commit it. |
| `GROQ_SCREENING_MODEL` / `GROQ_SCREENING_REASONING_EFFORT` / `GROQ_REQUEST_TIMEOUT_SECONDS` / `AI_SCREENING_PROVIDER` | No (defaults `openai/gpt-oss-120b` / `low` / `30` / `groq`) | The Groq screening model, which must support strict structured outputs. The others are its reasoning effort (empty = send no reasoning parameters), its timeout, and which of `groq`/`gemini` is tried first. See `docs/ai-screening.md`. |
| `SIGVI_*` | No | `SIGVI_REQUEST_TIMEOUT_SECONDS`, `SIGVI_MAX_OUTPUT_TOKENS`, `SIGVI_ORGANIZATION_SLUG`, `SIGVI_RATE_LIMIT_PER_MINUTE`, `SIGVI_GLOBAL_RATE_LIMIT_PER_MINUTE` — see `docs/sigvi.md` § 9. |
| `DEFAULT_PHONE_REGION`, `EMAIL_OTP_*`, `EMAIL_VERIFICATION_TOKEN_TTL_MINUTES`, `PUBLIC_*_LIMIT_PER_WINDOW` | No (safe defaults) | Candidate intake tuning. Defaults and meanings are in `backend/.env.example`; the flow is in `docs/recruitment-workflow.md` § 6. |

Email is optional for startup: with neither Resend nor SMTP configured, the
manual send action returns a `503 email_not_configured` error rather than
failing silently or faking success. **But the self-service apply flow depends
on it.** Candidates must verify their email with a one-time code before
applying. Without email, requesting that code returns `503
email_not_configured`, so no one can apply through the careers site or a
campus drive link.

None of the optional integrations block startup if left unset. This is
enforced in code (`app/integrations/email`, `app/integrations/ai`), not just
a deployment convention.

### 3.1 `DATABASE_URL` and `MONGODB_URI` — connection settings

Both values are secrets: set them only in the Render dashboard (`sync:
false` in `render.yaml`) or a local, gitignored `.env`. Never paste them into
`render.yaml`, docs, commit messages, or logs.

**`DATABASE_URL`** — one of two forms, depending on where the web service
runs relative to the database:

- **Same Render region as the database**: use the **Internal Database
  URL** (private network, no public hop), rewritten to the app's scheme:

  ```
  postgresql+psycopg://<user>:<password>@<internal-host>/<database>
  ```

- **Different region, or connecting from your own machine** (Alembic, psql):
  use the **External Database URL** with TLS required and channel binding
  disabled:

  ```
  postgresql+psycopg://<user>:<password>@<host>.<region>-postgres.render.com:5432/<database>?sslmode=require&channel_binding=disable
  ```

  `channel_binding=disable` is needed for Render's external endpoint:
  libpq's default (`prefer`) fails there with `SCRAM channel binding check
  failed`, because the endpoint terminates TLS in front of Postgres. TLS
  itself stays mandatory (`sslmode=require`). The query parameters are
  passed through SQLAlchemy to psycopg unchanged, so the same URL works for
  the app (`app/db/session.py`) and for Alembic (`alembic/env.py`).

The region is part of the external host name (e.g. `…-a.singapore-postgres.render.com`).
An Internal URL only works from a service in the same region. `render.yaml`
does not pin a `region`, and Render's default region is Oregon. A web service
in Oregon talking to a database in Singapore must use the External URL form
and pays cross-region latency on every query. Check the service's region in
the dashboard before choosing a form.

URL-encode reserved characters in the password (`@` → `%40`, `:` → `%3A`,
`/` → `%2F`, `%` → `%25`). `alembic/env.py` already escapes `%` for
configparser.

**`MONGODB_URI`** — the resume file bytes (GridFS bucket `resumes`) are not
in Postgres (§ 11). Postgres `resumes.storage_path` holds only GridFS
ObjectIds, so after switching `DATABASE_URL` to a database that was copied
from another environment, `MONGODB_URI` and `MONGODB_DATABASE` must point at
the **same** MongoDB deployment and database that wrote those files, or every
resume download for the copied rows fails. Use the Atlas SRV or
replica-set string with TLS on (`mongodb+srv://…` or `…?ssl=true`), and allow
network access from Render in Atlas.

## 4. Run Alembic migrations against the production database

As of `render.yaml`'s current `startCommand`
(`alembic upgrade head && uvicorn app.main:app ...`), this now runs
**automatically on every deploy and every cold start**, using the same
`DATABASE_URL` the web service already has configured — no Render Shell
access needed (the free plan doesn't offer one), no second database, no
hardcoded credentials. `&&` means `uvicorn` only starts if the migration
step exits 0, so a broken migration fails the deploy loudly (Render keeps
the previous successful deploy live) instead of serving against a stale or
half-migrated schema. It is safe to run unconditionally on every startup
because `alembic upgrade head` is idempotent — Alembic tracks the applied
revision in the `alembic_version` table, so re-running it when already at
head is a fast no-op (one query). On a free-tier dyno that spins down when
idle, this means the check simply repeats on every subsequent cold start.

The first initialization of a brand-new, empty production database still
works exactly the same way — the same command just has more migrations to
apply the first time it runs.

If you ever need to run it by hand instead (e.g. to test against a copy of
production before deploying, or if you disable the automatic step) — from
your own machine, with the **External Database URL** (rewritten per § 1)
in your environment:

```bash
cd backend
DATABASE_URL="postgresql+psycopg://...<external-url>..." alembic upgrade head
```

This remains the **first and only** way the schema should ever be created —
`Base.metadata.create_all()` is never used anywhere in this codebase.
`alembic upgrade head` on a brand-new, empty database runs every migration
in order (identity/tenancy → recruitment core → screening/assessments/
campus/notes → campus-drive redesign → activity/soft-delete → assessment
retests → team-management/soft-delete columns → the WITHDRAWN→REJECTED
enum rebuild → job description visibility → assessment monitoring events →
team hierarchy/departments/employees → resume storage provider → the
`activity.delete` permission → nullable candidate profile columns for the
public application form), seeding system roles and permissions
as it goes. This full chain was verified locally (`alembic downgrade base`
→ `alembic upgrade head` → `alembic downgrade base` → `alembic upgrade
head`) during the SIGVITAS platform work — see git history — with no
manual intervention required.

**In-app notifications (`b2c3d4e5f6a7`).** Adds the `notifications` table and
`notification_type` enum (tenant RLS, unique `(assessment_invitation_id, type)`);
no existing table is altered. Run it **before** the matching backend goes live:
the notification insert is failure-tolerant (a missing table is logged and the
candidate's assessment still works), but the recruiter portal's poll of
`GET /api/v1/recruiter/notifications` would return 500 until it exists.
Verified locally with an `upgrade` / `downgrade -1` / `upgrade` round trip.

**Employee display order (`a7b8c9d0e1f2`, added after that verification).**
Adds `employees.display_order`, the hidden position an employee holds within
their (organization, department) — the org chart sorts by it and org admins
rearrange it via `PATCH /api/v1/recruiter/employees/reorder`. Existing
employees are numbered in creation order (`created_at`, then `id`), not
alphabetically. Its upgrade/downgrade/upgrade round trip and backfill are
covered by `backend/tests/test_employee_display_order_migration.py`. The column
is `NOT NULL` with no default, so **run this migration before the matching
backend code goes live**: code that selects the column fails every employee
query with `UndefinedColumn` on a database that doesn't have it yet, whereas
migrating first only makes employee *creation* by the previous code version
fail until the deploy finishes.

**Program funding, training, interviews (`5e6f70819203`).** Additive:
new columns on `recruitment_programs` / `program_enrollments` (all nullable
or with defaults), three new tenant tables with RLS
(`program_funding_revisions`, `training_milestones`,
`application_interviews`), program support on
`campus_affiliation_verifications` (two columns become nullable, guarded by
check constraints that every existing row already satisfies),
`assessment_results.pass_score_applied` (backfilled from each result's
assessment), permissions `interview.read`, `interview.manage`,
`program.employment.approve` granted to the **system** roles, and new enum
values (scholarship PROPOSED, training WITHDRAWN, agreement
EXPIRED/TERMINATED, two affiliation evidence methods, notification
INTERVIEW_ASSIGNED). No new environment variables.

- Enum values are added in an `autocommit_block` (PostgreSQL requires a new
  value to be committed before use). When several migrations are pending in
  one `alembic upgrade head`, the ones before this point are committed at
  that moment; a failure later in this migration leaves the database at
  `4d5e6f708192` plus the (harmless, unused) new enum values, and re-running
  the deploy completes it.
- Run it **before** the matching backend goes live (the start command
  already does): the new code selects the new columns.
- Existing programs keep `affiliation_verification_method = NONE`, so
  in-flight applicants are not suddenly blocked on verification. Existing
  approvals made with the old one-step scholarship endpoint keep working
  (agreements can still be sent and signed; applied funding is respected by
  later revisions).
- Behaviour that is now stricter for participants already in the pipeline:
  selection needs recorded interview outcomes for every configured stage,
  an offer needs an approved employment review, fee payments need a fixed
  fee obligation (which needs the finalized fee), and a signed agreement can
  no longer be voided (expire/terminate instead).
- After deploying: organization-specific custom roles (if any) don't get the
  new permissions automatically — grant them where wanted; and assign
  `program.finance.manage` / `program.employment.approve` holders before HR
  uses funding approval and employment decisions.
- Verified locally: `upgrade` / `downgrade -1` / `upgrade` (twice), and a
  two-migration run (`downgrade 3c4d5e6f7081` → `upgrade head`) with
  existing job/result data unchanged; `alembic check` reports no drift.
  Downgrade deletes program affiliation records and INTERVIEW_ASSIGNED
  notifications (the old schema/code has no place for them) and maps new
  enum values back to the closest old one; the values themselves stay on the
  types (PostgreSQL can't drop enum values).

**Candidate intake (`b4c5d6e7f8a9`).** Changes the following:

- adds `AI_SCREENED_OUT` / `HR_MATCH` to the application enums;
- adds `organizations.careers_contact_email`;
- adds the candidate identity columns (`date_of_birth`, `place_of_birth`,
  `languages`, `email_verified_at`);
- adds the `screening_runs` decision columns;
- adds the RLS-protected `email_verifications` table;
- normalizes existing candidate phones to E.164 (using
  `DEFAULT_PHONE_REGION`, default `IN`), then adds the unique
  `(organization_id, phone)` index.

**Before deploying it, check production for duplicate mobile numbers.** If
two candidates in one organization have the same number in different formats
("98765 43210" vs "+919876543210"), the migration rewrites nothing and fails
with the affected candidate ids. Because DDL is transactional, the whole
upgrade rolls back and `uvicorn` doesn't start. Render keeps the previous
deploy live, so nothing breaks, but the new version won't ship until the
duplicates are fixed by hand (edit or clear the phone on the duplicate
records). To find out in advance, run `alembic upgrade head` against a copy
of the production database.

Its **downgrade refuses to run** while any application or status-history row
uses `AI_SCREENED_OUT` or `HR_MATCH` (`docs/database.md` § 3.5). The
upgrade → `downgrade -1` → upgrade round trip was verified locally, and the
data guards are covered by `backend/tests/test_candidate_intake_migration.py`.

After migrating, create the platform super admin (needed to bootstrap the
first organization via `/api/v1/admin/organizations`):

```bash
DATABASE_URL="...same external url..." python -m app.cli create-superadmin \
  --email you@yourcompany.com --password "<a strong password>" --full-name "Your Name"
```

**Do not** run `alembic upgrade head` against the local development
database as part of this preparation task — only against the new Render
database, and only when you're actually ready to initialize it.

## 5. Create the Render Web Service

- **Root directory**: `backend`
- **Runtime**: Python (native, not Docker — no Dockerfile is needed; see
  § "Why no Docker" below)
- **Python version**: `3.12.10`, pinned via `backend/.python-version` (Render
  reads this file automatically) and mirrored in `render.yaml`'s
  `PYTHON_VERSION` env var as a second, explicit pin.
- **Build command**: `pip install --upgrade pip && pip install .`
  (installs from `pyproject.toml` — there is no separate `requirements.txt`
  to keep dependency declarations in one place)
- **Start command**: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  (see § 4 — migrations run automatically, before the app starts serving)
- **Health check path**: `/healthz`

If you use the Blueprint (`render.yaml` at the repo root), most of this is
already filled in — Render will still prompt for every `sync: false`
variable.

### Why no Docker

The backend has no native-extension dependency that needs a custom system
image — `psycopg[binary]` ships a self-contained wheel, and
`uvicorn[standard]`'s extras (`uvloop`, `httptools`) have prebuilt Linux
wheels. Render's native Python buildpack handles `pip install .` directly,
so a Dockerfile would add a maintenance surface with no corresponding
benefit right now.

### Why `uvicorn` directly, not Gunicorn

A single `uvicorn` process is sufficient for this deployment's current
scale and keeps the process model simple. If concurrency ever becomes a
bottleneck, moving to `gunicorn -k uvicorn.workers.UvicornWorker` is a
one-line start-command change — not introduced now, per the project's "no
dependency without a stated reason" rule (`gunicorn` isn't in
`pyproject.toml` today).

### Windows-specific code does not affect production

`backend/run.py`'s Windows event-loop shim
(`app/core/asyncio_compat.py::configure_event_loop_policy`) is a documented
no-op on every platform except Windows. Render's Linux containers are
unaffected; the start command above (`uvicorn app.main:app` directly, not
through `run.py`) is exactly what the code's own docstring recommends for
non-Windows environments.

## 6. Verify `/healthz`

Once deployed:

```bash
curl https://<your-render-service>.onrender.com/healthz
# {"status":"ok"}
```

This endpoint (`app/api/system.py`) does not touch the database and
requires no authentication — safe for Render's health-check probe. A
second endpoint, `/readyz`, does check database connectivity
(`SELECT 1`) and returns HTTP 503 if the database is unreachable; use it
for post-deploy verification, not as the Render health-check path (a
transient DB blip shouldn't cause Render to restart the whole service).

## 7. Deploy the Vercel frontend

- **Root directory**: `frontend`
- **Framework preset**: Vite
- **Install command**: `npm install` (default)
- **Build command**: `npm run build` (runs `tsc -b && vite build`)
- **Output directory**: `dist`

## 8. Configure the Vercel API URL

Set in the Vercel project's environment variables:

| Variable | Value |
|---|---|
| `VITE_API_BASE_URL` | `https://<your-render-service>.onrender.com` |

This is the **only** environment variable the frontend needs. Every
`VITE_*`-prefixed variable is bundled into client-side JS and is publicly
readable — never put a backend secret in one (there currently are none
here to worry about; the frontend has no secrets at all).

## 9. Update Render CORS with the real Vercel URL

Once Vercel assigns your production domain, go back to the Render web
service and set:

```
CORS_ALLOW_ORIGINS=["https://<your-vercel-domain>"]
FRONTEND_BASE_URL=https://<your-vercel-domain>
```

(Add a second entry to the `CORS_ALLOW_ORIGINS` array if you also keep a
Vercel preview-deployment domain you want to exercise against the same
backend.) Redeploy the Render service after changing env vars so
`get_settings()` — an `lru_cache`d singleton — picks up the new value on
the fresh process.

## 10. Test the production application

1. `GET /healthz` → `{"status":"ok"}`.
2. `GET /readyz` → `{"status":"ok","database":"ok","mongodb":"ok"}` (the
   `mongodb` key is only present when `RESUME_STORAGE_PROVIDER=mongodb_gridfs`).
3. Log in as the super admin created in § 4, create the SIGVITAS
   organization + admin via `/api/v1/admin/organizations`.
4. Log in as that org admin from the deployed frontend; confirm CORS
   works (no browser console CORS errors) and the refresh-token cookie is
   set (check DevTools → Application → Cookies — should show `Secure`,
   `HttpOnly`, `SameSite=None`), then reload the page and confirm you stay
   signed in (the silent refresh depends on that cookie being sent
   cross-site).
5. Create a job, publish it, open the public career site
   (`https://<vercel-domain>/org/<slug>`), submit a test application,
   confirm the resume upload succeeds and is retrievable — see § 11 for
   the `RESUME_STORAGE_PROVIDER=mongodb_gridfs` variables this depends on
   in production.
6. Refresh the browser on a deep link (e.g. `/recruiter/applications`) to
   confirm Vercel's SPA rewrite (`frontend/vercel.json`) is working — it
   should render the app, not a 404.

---

## 11. File / resume storage

Resume file *bytes* live behind the `ResumeStorage` abstract interface
(`app/integrations/storage/base.py`) that every route/service depends on —
no route or service touches a filesystem or database driver directly.
Two implementations exist, selected by `RESUME_STORAGE_PROVIDER`:

- **`local`** (`app/integrations/storage/local.py`) — writes to local disk
  under `RESUME_STORAGE_DIR`. Development default. **Render's default
  web-service filesystem is ephemeral**: files written to it do not
  survive a redeploy/restart and are not shared across instances — never
  use this in production.
- **`mongodb_gridfs`** (`app/integrations/storage/mongo_gridfs.py`) —
  **the production setting.** Stores resume bytes in a MongoDB GridFS
  bucket named `resumes`, addressed by the bucket file's ObjectId. Postgres
  remains the system of record for everything else (`resumes.storage_path`
  holds only that opaque ObjectId string; `resumes.storage_provider`
  records which backend wrote it, so resumes uploaded before this setting
  changed keep resolving against `local` correctly — CLAUDE.md § 2:
  "Resume storage != DB blob").

**Required Render environment variables for production**:

```
RESUME_STORAGE_PROVIDER=mongodb_gridfs
MONGODB_URI=<your MongoDB connection string — set in the Render dashboard, never in Git>
MONGODB_DATABASE=ai_recruitment
```

Any MongoDB deployment reachable from Render works (e.g. a free-tier
[MongoDB Atlas](https://www.mongodb.com/atlas) cluster). Create the
cluster, allow network access from Render (or use Atlas's "allow from
anywhere" for a first deploy, then tighten it), and paste its connection
string into `MONGODB_URI` in the Render dashboard as a secret.

The MongoDB client is created once at process startup (`app/db/mongo.py`,
wired into `app.main`'s lifespan) — never per request — and pinged during
startup and by `/readyz`. `/readyz` only checks MongoDB when
`RESUME_STORAGE_PROVIDER=mongodb_gridfs`; it never checks it while
`local` is selected, since that configuration has no MongoDB dependency
to be ready.

**Before going live with real candidate applications, confirm
`RESUME_STORAGE_PROVIDER=mongodb_gridfs` and `MONGODB_URI` are both set —
do not deploy the default local-disk configuration and assume resumes are
safe.**

## 12. Email — production behavior

Outbound email goes through `app/integrations/email/get_email_provider()`,
which picks, in this order:

1. **Resend** (HTTPS, `api.resend.com`) when `RESEND_API_KEY` and `EMAIL_FROM`
   are both set — the production provider.
2. **SMTP** when `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD` and
   `SMTP_FROM_EMAIL` are all set — local development.
3. Otherwise an always-raising "not configured" provider (`503`, never a fake
   success).

**Why production uses Resend.** Render's free web services block outbound
traffic to SMTP ports 25, 465 and 587
([changelog](https://render.com/changelog/free-web-services-will-no-longer-allow-outbound-traffic-to-smtp-ports)),
so Gmail SMTP fails there with `SMTP send failed` / `error_type: OSError` (a
`502 email_delivery_failed`) even though it works locally. Resend is a plain
HTTPS POST, which is not blocked, and keeps the free tier free (Resend's free
plan: 100 emails/day, 3,000/month, up to 3 verified domains). Resend is
preferred whenever both are configured, so leaving the old `SMTP_*` variables
on Render is harmless; the SMTP provider is kept for local development.

**Required Render variables** (Environment tab, then **Save and deploy** —
"Save only" does not restart the service, and settings are read at startup):

| Variable | Value |
|---|---|
| `RESEND_API_KEY` | An API key from the Resend dashboard (API Keys). A "sending access" key is enough. |
| `EMAIL_FROM` | `SIGVITAS <hr@yourdomain.com>` — the domain must be verified in Resend (Domains -> add the DNS records it shows, wait for "Verified"). |

**Sender-domain requirement.** Until a domain is verified, Resend only lets an
account send test mail *to its own address* and rejects anything else with a
`403 validation_error`, which the app reports as "The email service refused the
sender or recipient address." A free-mail address (e.g. `@gmail.com`) cannot be
verified, so it cannot be the `EMAIL_FROM`.

**Diagnosing a failure.** Failed Resend sends log
`"message": "Resend send failed"` with `provider`, `status_code` and Resend's
`error_name` (or `error_type` for a network failure) — never the API key, the
recipients or the message body.

- **Manual only.** Candidate email is sent only by an explicit recruiter/admin
  action on the application page: **Send Email** (any template) or **Send
  Assessment Invitation**. Nothing sends automatically — not on applying
  (job or campus drive), not on assigning/retesting an assessment, not on any
  status change (selection and rejection included). The API is
  `POST /api/v1/recruiter/applications/{id}/email/{compose,preview,send}`
  (gated by the `application.email.send` permission, ORG_ADMIN and RECRUITER
  only; tenant-scoped, so another organization's application is a 404) plus
  `GET /api/v1/recruiter/email-templates`.
- **Templates.** Six predefined templates (application received, interview
  invitation, assessment invitation, next steps, rejection, general) live in
  code (`app/email_templates/`); editing an email never changes them. The
  composer fills the candidate, job, company and recruiter details, lets the
  recruiter edit subject/body, and previews the exact HTML that will be sent.
  Optional details left empty (e.g. no meeting link) drop their line; a
  required detail that is still empty, or any unknown `{{placeholder}}`,
  blocks preview/send with a `422 unresolved_placeholders` error, so a raw
  placeholder can never reach a candidate.
- **HTML.** One shared table-based layout (company header, content, optional
  button, footer with recruiter contact), inline CSS only — no images,
  scripts or external resources — with a plain-text alternative. The message
  is sent from the configured sender address (`EMAIL_FROM` on Resend,
  `SMTP_FROM_EMAIL` on SMTP) with the sending recruiter as `Reply-To`.
- **General email.** The **Email** page (`/recruiter/email`, visible to
  ORG_ADMIN and RECRUITER) sends the same templates to addresses the sender
  types, with no application: `POST /api/v1/recruiter/email/{compose,preview,send}`
  under the same `application.email.send` permission. Recipients (To, and
  optional Cc/Bcc — at most 10 each, 20 in total) are validated as bare email
  addresses, de-duplicated across the three fields, and never accepted from
  anywhere but the request body; the company name always comes from the signed-in
  user's organization and the sending account is never client-selectable. The
  Assessment invitation template needs an application (it carries a personal
  link) and is unavailable there; templates that mention a role ask for it, and
  the recipient defaults to "Sir or Madam" when no name is given. Sends are
  audited as `GENERAL_EMAIL_SENT` / `GENERAL_EMAIL_FAILED` (recipients and
  subject, not the body). No configuration change: it uses whichever provider
  is configured above.
- **Honest failures.** Not configured -> `503` with code
  `email_not_configured`; the provider (Resend or the SMTP server) rejecting the
  credentials or the message, or being unreachable -> `502` with code
  `email_delivery_failed`. Both attempts are
  recorded in Activities (`CANDIDATE_EMAIL_FAILED` / `CANDIDATE_EMAIL_SENT`).
  Nothing is ever reported as sent when it was not.
- **No credentials in code.** `RESEND_API_KEY` and `SMTP_PASSWORD` are read
  from the environment into `SecretStr`s, are never logged, never returned by
  any endpoint, and are absent from `.env.example`. Set them only in the
  Render dashboard.

## 13. AI provider — production behavior

Optional by construction (`app/integrations/ai/__init__.py::get_llm_provider`):
if none of `OLLAMA_BASE_URL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GROQ_API_KEY` or `GEMINI_API_KEY` is set, AI-assisted screening returns a clear "not
configured" error rather than failing startup or fabricating a result. A
self-service application is then kept at `APPLIED` for manual review; it is
never screened out. **Do not set any AI provider key as part of this
deployment** unless you've decided to enable AI screening. The platform is
fully usable without it.

**Note:** when none of the first three is set, `GROQ_API_KEY` and
`GEMINI_API_KEY` (below) enable screening. Groq is the primary provider and
Gemini gets one fallback attempt when Groq fails; either key also works on
its own. Setting it for Sigvi therefore
also turns on automatic submission-time screening
(`docs/ai-screening.md`).

### Sigvi (the public AI assistant)

Sigvi uses Google Gemini, and the same key serves as the screening fallback
described above. To enable it on
Render: **Environment tab → add `GEMINI_API_KEY` (a key from Google AI Studio)
→ Save and deploy.** That is the only required change; `GEMINI_MODEL` defaults
to `gemini-3.5-flash-lite` and the `SIGVI_*` limits have safe defaults. No
database migration is needed and no Vercel change is needed (the browser calls
the existing API base URL). Without the key the assistant shows "temporarily
unavailable" and the site works as before. After deploying, ask Sigvi a
question on the home page to confirm; a `403`/"API key" upstream message in the
logs means the key is wrong, a `429` means the free-tier quota. See
`docs/sigvi.md`.

## 14. Security posture at deployment time

Audited as part of this preparation task:

- No `.env` file has ever been committed to this repository's git history
  (checked with `git log --all --diff-filter=A --name-only`) — no secret
  rotation is required before deploying.
- No hardcoded passwords, API keys, or database credentials exist in
  application code. The only credentials in the repository are the
  documented local-dev-only demo accounts in `docs/TEST_CREDENTIALS.md`
  and test fixtures — do not reuse any of those emails/passwords for a
  real production account.
- `JWT_SECRET_KEY` and `DATABASE_URL` have no default — the app refuses to
  start without them (`app/core/config.py::Settings`), which is the
  correct fail-closed behavior.
- Refresh-token cookies are `HttpOnly` and, when `ENVIRONMENT=production`,
  `Secure` + `SameSite=None` (`app/api/v1/recruiter/auth.py`) — required
  because the Vercel frontend and the Render API are different sites; a
  `Strict` cookie is never sent on that cross-site refresh call. Outside
  production they are `SameSite=Lax`. `ENVIRONMENT=production` must therefore
  be set on Render (it is in `render.yaml`).
- Structured JSON logs redact `password`, `hashed_password`, `token`,
  `authorization`, and `secret` keys unconditionally
  (`app/core/logging.py::_SENSITIVE_KEYS`) — independent of log level.
- The generic exception handler never leaks stack traces, SQL text, or
  file paths to a client response — only to server-side logs, keyed by a
  request ID (`app/core/exceptions.py`).
- Swagger/OpenAPI (`/docs`, `/redoc`, `/openapi.json`) is left enabled —
  no route in this API exposes a secret in its schema or example values,
  and there's no existing requirement in this codebase to disable it. If
  you'd prefer it disabled in production, that's a one-line change
  (`docs_url=None, redoc_url=None` in `create_app()`), not made here since
  it wasn't a stated requirement.
- Found no committed real API keys, private keys, or AWS-style access
  keys anywhere in the repository (pattern search across `.py`, `.ts`,
  `.tsx`, `.md`, `.json`, `.ini`, `.yaml`/`.yml`).

## 15. Debug / production-mode checklist

Set on the Render web service before considering the deploy "production":

- `DEBUG=false` (disables FastAPI's debug tracebacks and flips cookies to
  `Secure`)
- `ENVIRONMENT=production`
- `LOG_LEVEL=INFO` (or `WARNING` if you want quieter logs — never
  `DEBUG`, which would also turn on verbose SQLAlchemy engine logging if
  that were ever wired to the SQL logger; today `echo=False` is hardcoded
  in `app/db/session.py` regardless of `LOG_LEVEL`, so this isn't
  currently a real risk, just good practice)

There are no development-only endpoints or test-authentication bypasses
anywhere in the route tree to worry about disabling — none exist in this
codebase.
