# Test / Demo Credentials (Local Development Only)

These accounts exist in the **local development database** only
(`ai_recruitment_v2` on `localhost:5432`, per `backend/.env`). They are for
frontend/API manual testing. Never use these emails/passwords in a shared or
production environment.

Verified working (`POST /api/v1/recruiter/auth/login` → `200`) on 2026-09-16.

## Platform super admin

Used for `/api/v1/admin/*` (organization bootstrap/management). Not tied to
any tenant — `organization_id` is `NULL`.

| Field | Value |
|---|---|
| Email | `super.admin@platform.dev` |
| Password | `SuperAdmin@2026Dev` |
| Role | `SUPER_ADMIN` |
| Organization | — (platform-level) |
| Purpose | Bootstrap/manage organizations via the admin API |

## Organization admin — Acme Corp

| Field | Value |
|---|---|
| Email | `admin@acme-corp.dev` |
| Password | `OrgAdmin@2026Dev` |
| Role | `ORG_ADMIN` |
| Organization | Acme Corp (`acme-corp`) |
| Purpose | Full recruiter-portal access + user management (`user.create`/`user.read`/`user.update`, plus `user.manage_admins` for Org Admin accounts) for the tenant |

## Recruiter — Acme Corp

| Field | Value |
|---|---|
| Email | `recruiter@acme-corp.dev` |
| Password | `Recruiter@2026Dev` |
| Role | `RECRUITER` |
| Organization | Acme Corp (`acme-corp`) |
| Purpose | Standard recruiter-portal (HR) testing: jobs, candidates, applications, and Team/department management. It has no Org Admin powers: it can't create, promote, demote or deactivate an Org Admin, delete activities, or change org settings. Use it to verify role boundaries against `admin@acme-corp.dev`. |

## Organization admin — Sigvitas Pvt. Ltd.

Realistic demo organization (see backend/scripts or the seeding note below)
with 15 real-looking job postings across engineering, design, data, and HR —
used to demo the platform as more than a toy/empty instance.

| Field | Value |
|---|---|
| Email | `admin@sigvitas.com` |
| Password | `SigvitasAdmin@2026` |
| Role | `ORG_ADMIN` |
| Organization | Sigvitas Pvt. Ltd. (`sigvitas`) |
| Purpose | Full recruiter-portal access for the Sigvitas demo tenant |

## Recruiter — Sigvitas Pvt. Ltd.

| Field | Value |
|---|---|
| Email | `recruiter@sigvitas.com` |
| Password | `SigvitasRecruit@2026` |
| Role | `RECRUITER` |
| Organization | Sigvitas Pvt. Ltd. (`sigvitas`) |
| Purpose | Standard recruiter-portal testing under the Sigvitas tenant |

Public career site: `/org/sigvitas` (12 OPEN roles; also has 1 DRAFT, 1
ON_HOLD, 1 CLOSED job to demonstrate the full status lifecycle).

## Notes

- No candidate-portal test accounts exist yet (candidate auth ships in a
  later phase per `docs/architecture.md`).
- Login endpoint: `POST /api/v1/recruiter/auth/login` with `{"email": ..., "password": ...}`.
- If you reseed or reset these accounts, update this file in the same
  change so it keeps matching the local database.
