# Architecture

Status: **Largely implemented as a local development demo**, not the
original phase-by-phase order below. This document describes the target
architecture; the decisions in § 4 and the tech stack in § 3 (mirrored in
`CLAUDE.md`) are exactly what's built. The domain/phase breakdown further
down is the *original* plan — actual delivery order diverged (AI
screening, assessments, campus drives, and reports were built together
rather than strictly sequenced, and each was intentionally simplified from
its full spec; see the "Implementation status" note at the top of
`docs/ai-screening.md`, `docs/assessment.md`, and `docs/campus-hiring.md`
for specifics). No candidate portal (self-service login) exists yet.

## 1. System shape

A modular monolith:

- **`backend/`** — a single FastAPI application, internally partitioned by
  domain (identity, candidates, jobs, applications, screening, assessments,
  campus hiring, notes, reporting, audit). Domains talk to each other through
  service-layer calls, not through separate deployable services. This keeps
  transactional integrity simple (e.g. "create Application" and "write audit
  log entry" in one DB transaction) while the codebase stays organized enough
  to split out a service later (e.g. the AI screening pipeline) if load
  demands it.
- **`frontend/`** — a single React SPA serving three logical surfaces (public
  career site, candidate portal, recruiter portal) behind one Vite build,
  route-guarded by auth state. Not three separate apps, to avoid duplicating
  shared UI (job cards, layout) — but organized so surfaces don't leak
  concerns into each other (see § Frontend architecture).
- **PostgreSQL** is the single system of record, extended with `pgvector` for
  embeddings. No separate document store or search engine in the initial
  build — full-text search uses Postgres (`tsvector`) and can graduate to a
  dedicated search service later if needed.
- **Object/local storage** for resume files and any future uploaded documents,
  behind a storage abstraction — never in Postgres as blobs.

Why a monolith and not microservices: at this stage there is one team, one
deploy target, and most domain flows (application → screening → assessment)
are tightly sequential and transactional. Splitting into services now would
add network/consistency complexity with no scaling benefit yet. The module
boundaries below are drawn so that a future split (most likely candidate:
the AI screening/embedding pipeline, which is CPU/GPU- and queue-shaped
differently from the CRUD API) doesn't require a rewrite.

## 2. Domain modules

| Module | Owns | Depends on |
|---|---|---|
| **Identity & Tenancy** | Organization, User, Role, Permission, RefreshToken (staff) | — |
| **Candidate Identity** | Candidate (org-scoped person + portal login), CandidateRefreshToken | Identity & Tenancy (organization) |
| **Jobs** | Job, JobRequirement | Identity & Tenancy |
| **Resumes** | Resume (metadata), ResumeDocument (extracted text), ResumeChunk (+ embedding) | Candidate Identity, Storage integration |
| **Applications** | Application, ApplicationStatusHistory | Candidate Identity, Jobs, Campus Hiring (optional link) |
| **Screening / AI** | ScreeningRun, AIRun, RequirementEvaluation, EvaluationEvidence | Applications, Resumes, Jobs, AI integration |
| **Assessments** | Assessment, Question, QuestionOption, AssessmentInvitation, CandidateAttempt, CandidateAnswer, AssessmentEvaluation, AssessmentResult | Applications (invitation targets an Application), Notification |
| **Campus Hiring** | College, CampusDrive | Jobs, Assessments (default assessment link), Applications |
| **Notes** | Note | Candidate Identity, Applications, Identity (author) |
| **Reporting** | (read-only aggregation over the above; no owned writable tables initially) | all of the above |
| **Audit** | AuditLog | all of the above (write-only sink) |
| **Notification** | EmailMessage (log), templates | Identity/Candidate (recipients), Storage of templates |
| **Internal messaging** | AdminConversation, AdminMessage ("Talk to Admin") | Identity & Tenancy (both sides are staff users) |

Cross-cutting infrastructure (not domain modules): **Storage abstraction**,
**AI provider integrations** (embeddings/LLM/document extraction),
**Email provider integrations**.

**Talk to Admin** (`app/services/admin_messaging_service.py`) is internal
staff-to-admin messaging, not a candidate-facing channel: one conversation
per staff member with their organization's admins as a *role*, so any
ORG_ADMIN can read and reply and each message records which admin sent it.
`admin_message.send` reaches only the caller's own thread (no conversation
id is accepted anywhere on that route); `admin_message.manage` sees the
organization's inbox. Both sides are tenant-scoped in the query layer and
by RLS, like every other tenant-owned table. It is plain REST, polled by
the client — deliberately no WebSockets, which this architecture does not
otherwise use. It is distinct from the `DIRECT_MESSAGE` notification, which
is a one-off, reply-less note from one user to another.

## 3. Multi-tenancy strategy

`Organization` is the tenant root. Every tenant-owned table carries a
non-null `organization_id` foreign key.

Enforcement is layered, not single-point:

1. **Application layer**: every service method that reads/writes tenant data
   takes the current principal's `organization_id` explicitly (from the
   authenticated request context) and every repository/query includes it in
   the `WHERE` clause. There is no "trusted" code path that queries tenant
   tables without it, except platform-admin (`SUPER_ADMIN`) tooling, which is
   its own explicitly audited path.
2. **Database layer**: Postgres **Row-Level Security** policies on every
   tenant-owned table, keyed off a session variable
   (`SET LOCAL app.current_org_id = '<uuid>'`) set once per request inside the
   DB session dependency. This is a backstop against an application-layer bug
   (a missed filter), not the primary mechanism — it does not by itself know
   which rows a given *role* should see, only which *tenant*.
3. **Candidates are tenant-scoped**, not global — see decision record in
   `CLAUDE.md` § 4.2. This is the one place the "same person, multiple
   tenants" question could have gone the other way; we chose isolation over
   a shared global identity because the spec explicitly lists candidates as
   tenant-isolated data, and a shared identity would require careful
   thought about cross-tenant data leakage (e.g. resume reuse) that isn't
   justified yet.
4. **SUPER_ADMIN** is the only role allowed a null `organization_id` on
   `User`, and only for platform administration endpoints
   (`/api/v1/admin/*`), which are structurally separate from
   `/api/v1/recruiter/*` and carry their own audit trail.

RLS bypass is required for: Alembic migrations (runs as the table owner,
which bypasses RLS by default — must NOT be `FORCE ROW LEVEL SECURITY` for
the migration role), and any legitimate cross-tenant admin query (explicit
`SET LOCAL app.bypass_rls = true` guarded by `SUPER_ADMIN` role check in code,
never client-controlled).

## 4. Identity & authorization model

Two distinct principal types, never mixed in one table or one token type:

- **User** (staff): belongs to an Organization (except `SUPER_ADMIN`, which
  is platform-level). Roles: `SUPER_ADMIN`, `ORG_ADMIN`, `RECRUITER`,
  `HIRING_MANAGER`, `INTERVIEWER`.
- **Candidate**: belongs to an Organization (see § 3.3), authenticates
  separately, and can never obtain a staff-scoped token.

RBAC schema (built for extensibility, not over-built):

- `Role(id, organization_id NULL, name, is_system)` — system roles
  (`organization_id IS NULL`) are the five staff roles above, seeded once and
  available to every org. `organization_id` is kept nullable-but-present now
  so a future "custom org-defined role" doesn't require a schema change.
- `Permission(id, code, description)` — fine-grained action codes (e.g.
  `job.create`, `application.status.change`, `report.view`).
- `RolePermission(role_id, permission_id)` — many-to-many.
- `UserRole(user_id, role_id)` — many-to-many (a user can hold more than one
  role, e.g. `RECRUITER` + `INTERVIEWER`), even though the initial UI only
  assigns one.

Authorization check shape: `require_permission("application.status.change")`
as a FastAPI dependency, resolved from the caller's roles → permissions,
**and** a tenant-ownership check on the specific resource (does this
Application belong to the caller's organization). Both must pass.

Authentication:

- Argon2id password hashing (`argon2-cffi`), tuned parameters documented in
  `docs/security.md`.
- JWT access tokens (short-lived, ~15 min), separate signing audience for
  `user` vs `candidate` tokens so a candidate token can never be replayed
  against a recruiter endpoint (and vice versa) even if a validation bug
  dropped the role check.
- Refresh tokens: opaque, stored server-side only as a hash
  (`UserRefreshToken` / `CandidateRefreshToken`), delivered via `httpOnly`,
  `Secure` cookie — `SameSite=None` in production (the SPA on Vercel and the
  API on Render are different sites, and a `Strict`/`Lax` cookie is never sent
  on that cross-site refresh call), `SameSite=Lax` in local development.
  Rotation on every use; reuse of a revoked token revokes the whole token
  family (breach detection) — which is why the frontend funnels every refresh
  through one in-flight request (`frontend/src/features/auth/AuthContext.tsx`).
- Assessment invitation access is a **third, separate mechanism** — a bearer
  token in the invitation URL, not a login session. See `docs/assessment.md`.

## 5. API boundary

Versioned, and partitioned by audience so an endpoint's authorization
requirement is evident from its path:

```
/api/v1/public/...      no auth — job listing, search, job detail
/api/v1/candidate/...   candidate JWT — profile, resume, applications, apply
/api/v1/assessment/...  invitation-token bearer — take/submit an assessment
/api/v1/recruiter/...   user JWT + role/permission checks, tenant-scoped
/api/v1/admin/...       user JWT, SUPER_ADMIN only, platform-level
```

Rules:

- `public` responses include only what an anonymous applicant needs (job
  title, description, requirements summary, org display name/location) —
  never internal fields (recruiter notes, screening scores, other
  candidates).
- `candidate` responses never include recruiter-only fields (interview
  notes, AI evaluation evidence, other candidates' data, screening scores
  unless explicitly designed to be candidate-visible).
- `recruiter` endpoints always resolve `organization_id` from the
  authenticated principal, never from a client-supplied parameter.
- Consistent envelope for errors (`code`, `message`, `request_id`) and for
  paginated list responses. No raw stack traces ever reach a client response
  body (see `docs/security.md`).

## 6. Frontend architecture

Feature-oriented, one Vite/React app, three route trees:

```
frontend/src/
  app/            routing, root providers (query client, auth context), layout shells
  features/
    auth/
    public/            job listing, job details (anonymous)
    candidate/          profile, resume, applications, apply flow, assessment-taking
    recruiter/
      sourcing/ applications/ screening/ notes/ assessments/
      reports/ jobs/ campus-drives/ candidates/ users/ settings/
  shared/          cross-feature UI components, hooks
  lib/             api client (per-audience base paths), query client, auth context
  types/           shared TS types (kept in sync with backend schemas)
```

- API access goes through a thin service layer per feature (`lib/api/*`),
  never `fetch`/`axios` calls inline in components.
- Route guards enforce "logged in as the right principal type" client-side
  for UX only — the backend is the actual authority.
- TanStack Query owns server-state caching; component state stays local/UI
  only.
- Candidate and recruiter route trees do not import from each other's
  `features/*` subtree, to keep the "candidate must never see recruiter
  data" boundary visible in the code, not just enforced by the API.

## 7. AI / RAG pipeline (architecture now, most of it built in Phases 5–6)

```
Resume file → DocumentExtractor → ResumeDocument (plain text)
            → chunking service → ResumeChunk[] → EmbeddingProvider → pgvector column
Job → JobRequirement[] (each optionally embedded for retrieval)
ScreeningRun (triggered manually or on submission)
  → AIRun (one per requirement or one per run, provider+model+prompt version recorded)
    → retrieval: top-k ResumeChunks per JobRequirement via pgvector similarity
    → LLMProvider evaluates requirement against retrieved evidence
    → RequirementEvaluation (score/verdict) + EvaluationEvidence (chunk refs, similarity, excerpt)
  → aggregate score attached to the ScreeningRun (not to Candidate/Application directly)
```

Key properties:

- `ScreeningRun` is append-only history against an `Application`. Re-running
  screening creates a new `ScreeningRun`; nothing is overwritten. The
  "current" score is "latest completed run," derivable, not a mutated field.
- Every `AIRun` records `provider`, `model`, `prompt_version`, `input_hash`,
  `started_at`/`completed_at`, and status, so a result is explainable and
  reproducible in principle.
- `integrations/ai/` defines `EmbeddingProvider`, `LLMProvider`,
  `DocumentExtractor` as abstract interfaces. Concrete adapters (e.g. an
  OpenAI-backed or local-model-backed implementation) are selected by
  configuration, never imported directly by domain services.
- **Sigvi (built ahead of the screening phases)**: the public assistant is the
  first consumer of this layer — a `ChatProvider` capability interface
  (`integrations/ai/`, Gemini adapter) and a swappable `KnowledgeRetriever`
  (`app/knowledge/`, keyword matching today; embeddings + pgvector are its
  intended replacement). See `docs/sigvi.md`.
- No orchestration framework (LangChain/LangGraph) is assumed. If added
  later, it lives entirely inside `integrations/ai/`; `services/screening/`
  continues to depend only on the interfaces above.

## 8. Assessment platform

See `docs/assessment.md` for full detail. Summary: `Assessment` is an
org-owned, reusable definition (questions + options), independent of how a
candidate ends up invited to it (normal pipeline or campus drive).
`AssessmentInvitation` is the secure, single-purpose bearer-token link to one
`Application`; `CandidateAttempt` records one attempt at answering;
`AssessmentEvaluation` records how it was scored (auto now, AI-assisted
later); `AssessmentResult` is the durable scored outcome. Questions can be
reused through the organization's versioned question bank; a test holds an
immutable copy of each bank version it uses, and objective answers are
graded automatically while descriptive/coding answers are graded by staff.

## 8a. Recruitment programs

See `docs/programs.md`. A `RecruitmentProgram` (e.g. the MTech/PhD
Scholarship & Paid Training Program) owns a carrier `Job`, so program
applications are ordinary `Application`s with every existing identity and
pipeline rule; `ProgramEnrollment` carries the program-specific state
(eligibility, scholarship, training, offer, agreement, bond) under the
transition tables in `app/workflows/program_workflow.py`.

## 9. Campus hiring

See `docs/campus-hiring.md`. Summary: `CampusDrive` wraps a `Job` with
college/batch/eligibility/date/assessment configuration. Adding or importing
a candidate into a drive creates an `Application`
(`source = CAMPUS_IMPORT`, `campus_drive_id` set) directly — no separate
"drive membership" table, since Application already models "this candidate,
this opportunity, this status." Sending an assessment invitation is always an
explicit recruiter action (bulk-capable), never a side effect of import.
Each drive belongs to an `Institution` from the organization's registry and
can require applicants' affiliation to be verified (email domain, student
list or manual review).

## 10. Reporting

No pre-aggregated tables in the initial build — reports are computed by
tenant-scoped SQL aggregation services reading `Application`,
`ApplicationStatusHistory`, `AssessmentResult`, `ScreeningRun`, and
`CampusDrive`. If/when volume makes live aggregation too slow, introduce a
materialized view or a `ReportSnapshot` cache table — deferred until there's
a real performance number to justify it.

## 11. Observability

- Structured (JSON) logging from day one, with a request-correlation ID
  generated per request and threaded through service/log calls.
- `/healthz` (process is up) and `/readyz` (DB reachable) endpoints from
  Phase 1.
- Business-event logging (job created, application submitted, invitation
  sent, screening run completed, etc.) doubles as input to `AuditLog` writes
  — not a separate logging pipeline.
- No metrics/tracing stack (Prometheus/OpenTelemetry) in the initial build —
  noted as a Phase 12 hardening candidate, not built prematurely.

## 12. Background processing

**Decision (confirmed at Phase 1 kickoff): Arq, backed by Redis.**

Long-running work — resume text extraction, embedding generation, AI
screening runs, bulk assessment-invitation sending — must not block the
request/response cycle. Options considered:

| Option | Verdict |
|---|---|
| FastAPI `BackgroundTasks` | Rejected as the general mechanism: runs in-process, is lost on a crash/restart, and gives no retry/visibility — unacceptable for "send 300 campus invitation emails" or an AI screening run that takes real time. Still fine for genuinely fire-and-forget, sub-second work if any turns up. |
| Celery (+ Redis/RabbitMQ) | Mature and battle-tested, but heavier operationally (broker + result backend + beat, if scheduling is needed) and its async support is a layer on top of a fundamentally sync worker model — a poor fit for a codebase that is async-first end to end (FastAPI + SQLAlchemy 2 async). |
| **Arq (chosen)** | Asyncio-native (jobs are `async def` functions, no sync/async bridging), backed by Redis, minimal operational footprint (one dependency, one broker), safe for the job shapes this project needs (fire off a job, track its status, retry on failure). Redis is also a natural fit for future needs (rate limiting, caching) if those arise. |

Not scaffolded in Phase 1: there is nothing to run yet (no resumes, no
assessments, no invitations exist as domain concepts until later phases).
Adding the `arq` dependency, a worker settings module, and a Redis
connection now, with zero jobs to register, would be exactly the
"unnecessary dependency" / "placeholder implementation" CLAUDE.md § 5 rules
out. It is added in Phase 5 (embedding generation — the first real
background job) alongside the code that actually needs it, and reused as-is
by Phase 7 (assessment evaluation, invitation emails) and Phase 8 (bulk
campus invitations).

## 13. Implementation phases

Matches the product owner's phase list; each phase ends with tests passing,
type/import checks clean, and a report of exactly what changed before moving
on.

0. Architecture and technical design *(this document)*.
1. Repository foundation — backend/frontend scaffolding, config, logging, DB
   connection, Alembic, health/readiness, test harness.
2. Identity & tenancy — Organization, User, Role/Permission, auth,
   authorization, RLS.
3. Recruitment core — Jobs, Candidates, Resumes (upload/metadata only),
   Applications.
4. Candidate portal — public jobs, candidate auth, profile, resume upload,
   apply, application status.
5. Resume intelligence — extraction, chunking, embeddings, pgvector,
   retrieval foundation.
6. AI screening — ScreeningRun/AIRun/RequirementEvaluation/Evidence, scoring.
7. Assessment platform — assessments, questions, invitations, secure links,
   attempts, answers, evaluation, results, email integration.
8. Campus mass hiring — drives, colleges, import, eligibility, bulk
   invitations, campus reporting.
9. Recruiter workflow — notes, status management, dashboards.
10. Reports — funnel, assessment, campus, source analytics.
11. Frontend completion — full recruiter/candidate/public UX.
12. Hardening — security/perf/testing/authz/tenant-isolation review,
    documentation, deployment readiness.

## 14. Major risks

- **RLS + async SQLAlchemy session-variable plumbing** is easy to get subtly
  wrong (e.g. connection pooling reusing a session var from a prior request).
  Mitigation: set `app.current_org_id` inside a request-scoped transaction
  every time, add a dedicated isolation test suite (Phase 2) that fails loudly
  if a query ever returns cross-tenant rows.
- **Candidate tenant-scoping** means no cross-org candidate profile reuse.
  This is a deliberate tradeoff (see § 3.3); revisit only with explicit
  product direction, since reversing it later is a data-model migration, not
  a config change.
- **Assessment invitation security** depends entirely on tokens never being
  logged, never appearing in referrer-leaking contexts, and expiring
  correctly. Needs explicit tests, not just code review.
- **AI provider cost/availability** is unknown at this stage (no provider
  chosen yet). The interface-based design limits the blast radius of a
  provider change but does not eliminate prompt/behavior differences between
  providers.
- **Scope size**: this is a large platform. Phases are ordered so each one is
  independently useful and testable; resist the temptation to build ahead of
  the current phase.

## 15. Major technical decisions

Confirmed by the product owner at Phase 1 kickoff (superseding the "pending
reviewer confirmation" status these carried after Phase 0):

1. **UUID v4 primary keys everywhere** — confirmed. No integer PKs anywhere
   in the schema. See `docs/database.md` § 1 for the tradeoff analysis
   against UUIDv7 and bigint+alias.
2. **Candidates are tenant-scoped entities** — confirmed (no cross-org
   shared candidate identity).
3. **Postgres RLS as a mandatory second enforcement layer** — confirmed and
   implemented in the Phase 1 identity/tenancy migration (see
   `app/db/rls.py`); verified with a dedicated tenant-isolation test suite
   (`backend/tests/test_tenant_isolation.py`) that inserts two organizations
   and asserts each can only ever see its own rows, with a fail-closed
   default (no tenant context set → zero rows, not all rows).
4. **Single React SPA for all three surfaces** — confirmed.
5. **Background worker/queue: Arq + Redis** — confirmed; see § 12. Not yet
   scaffolded (nothing to run until Phase 5).
6. **Resume file bytes: MongoDB GridFS in production, local disk in
   development** — confirmed. Selected per-request behind the existing
   `ResumeStorage` interface (`app/integrations/storage/`) via
   `RESUME_STORAGE_PROVIDER`; see `docs/deployment.md` § 11. Postgres
   remains the sole system of record for candidate/application/resume
   *metadata* — this decision only moves where raw file bytes live, never
   any relational data (CLAUDE.md § 1-2). Uses PyMongo's async
   `AsyncMongoClient` (not Motor), one process-wide client managed by
   `app.main`'s lifespan (`app/db/mongo.py`).
