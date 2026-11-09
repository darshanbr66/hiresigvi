-- Zphirex — one-time initialization of a FRESH production database on the
-- VPS (PostgreSQL 16 + pgvector). Run as the PostgreSQL administrator:
--
--     sudo -u postgres psql -f deploy/vps/postgres/init-database.sql
--
-- It creates nothing else and drops nothing. It stops at the first error
-- (e.g. if the role or database already exists) rather than altering an
-- existing setup. The application schema itself is created afterwards by
-- `alembic upgrade head`, run as zphirex_app (docs/deployment-vps.md § 4).

\set ON_ERROR_STOP on

-- 1. Application role: owns the application tables (Alembic creates them as
--    this role), but is NOT a superuser and does NOT have BYPASSRLS. Either
--    of those would silently skip the Row-Level Security policies that back
--    up tenant isolation (docs/security.md). FORCE ROW LEVEL SECURITY on
--    every tenant table makes the policies apply to this owner too.
CREATE ROLE zphirex_app
    LOGIN
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 30;

-- Prompts for the password without echoing it and sends only its
-- SCRAM-SHA-256 hash — the password never appears in this file, the shell
-- history or the server log. Use the same value (URL-encoded) in
-- DATABASE_URL in backend/.env.
\echo 'Set the password for zphirex_app:'
\password zphirex_app

-- 2. Database owned by the application role. On PostgreSQL 15+ the `public`
--    schema belongs to the database owner, so zphirex_app can create its
--    tables there without any extra grant.
CREATE DATABASE zphirex
    OWNER zphirex_app
    ENCODING 'UTF8'
    TEMPLATE template0;

REVOKE ALL ON DATABASE zphirex FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE zphirex TO zphirex_app;

-- 3. pgvector, created by the administrator: creating it needs privileges
--    the application role deliberately lacks. Migration c4d5e6f7a8b9 runs
--    `CREATE EXTENSION IF NOT EXISTS vector`, which is then a no-op.
--    gen_random_uuid() (used for every primary key) is built into
--    PostgreSQL 13+, so pgcrypto is not needed.
\connect zphirex
CREATE EXTENSION IF NOT EXISTS vector;

-- 4. Verify.
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles WHERE rolname = 'zphirex_app';
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
