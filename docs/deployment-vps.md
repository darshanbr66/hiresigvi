# Deployment — Hostinger VPS (zphirex.com)

**Status: prepared, not yet executed on the VPS.** The files this guide
uses are in [`deploy/vps/`](../deploy/vps). They were tested end to end on
a throwaway Ubuntu 24.04 container with PostgreSQL 16 and a MongoDB 8
container standing in for Atlas: package installation, database
initialization, migrations, API startup, nginx, the systemd unit, backup and
both restores. No production system, Atlas cluster or Render resource was
touched. Section 15 lists what can only be checked on the real VPS.

The Render + Vercel setup ([`deployment.md`](deployment.md), `render.yaml`,
`frontend/vercel.json`) is left unchanged, but this deployment does not use
it. No Render data is imported.

## 0. Three separate environments

| | Local development (your laptop) | Production |
|---|---|---|
| PostgreSQL | `localhost` on the laptop (`ai_recruitment_v2`) | **PostgreSQL 16 on the VPS**, `127.0.0.1:5432`, database `zphirex` |
| File bytes | Atlas, database `ai_recruitment`, development credentials | **MongoDB Atlas M0**, database **`zphirex`**, dedicated production user |
| Settings | `backend/.env` and `frontend/.env.local` on the laptop | `/opt/zphirex/app/backend/.env` on the VPS (mode 600) |

Production shares nothing with the laptop. The laptop can be switched off,
and its database and credentials are never used by the VPS.

## 1. Production architecture

| | |
|---|---|
| Frontend | `https://zphirex.com`: static React build served by nginx from `/var/www/zphirex/html` (uploaded with FileZilla) |
| `www` | `https://www.zphirex.com` → 301 → `https://zphirex.com` |
| API | `https://api.zphirex.com`: nginx → uvicorn on `127.0.0.1:8000`, systemd unit `zphirex-api`, **1 worker**, runs as user `zphirex` |
| PostgreSQL 16 + pgvector | On the VPS, **localhost only**. Holds every application record and file *metadata* (provider, GridFS id, name, size) |
| MongoDB Atlas M0 (GridFS) | **File bytes only**: resumes, 10th marks cards and program agreement documents, in bucket `resumes` (collections `resumes.files` and `resumes.chunks`) |
| TLS | Let's Encrypt (certbot, webroot mode), one certificate for all three names |
| Background jobs | Nothing to install. Calendar reminders run inside the API process. **No Redis or Celery.** |
| Backups | `deploy/vps/scripts/backup.sh`: PostgreSQL **and** MongoDB together, stored on the VPS (§ 10–12) |

Server layout:

```
/opt/zphirex/app/              git checkout (user zphirex; /opt/zphirex is 0750)
/opt/zphirex/app/backend/.env  production settings (0600, zphirex)
/opt/zphirex/venv/             Python 3.12 virtualenv
/var/lib/zphirex/              the API's only writable directory (unused while files live in GridFS)
/var/www/zphirex/html/         frontend build (FileZilla target)
/var/www/certbot/              ACME challenge webroot
/var/backups/zphirex/          backups (0700, root)
```

| File in `deploy/vps/` | Installed to |
|---|---|
| `nginx/zphirex-bootstrap.conf` | `/etc/nginx/sites-available/zphirex.conf` (only until the first certificate is issued) |
| `nginx/zphirex.conf` | `/etc/nginx/sites-available/zphirex.conf` |
| `nginx/snippets/*.conf` | `/etc/nginx/snippets/` |
| `systemd/zphirex-api.service` | `/etc/systemd/system/` |
| `postgres/init-database.sql` | run once with `psql` (§ 4) |
| `env/backend.env.example` | template for `/opt/zphirex/app/backend/.env` |
| `env/frontend.env.production.example` | template for `frontend/.env.production` (build machine) |
| `scripts/backup.sh` | cron, and before every migration |
| `scripts/deploy-backend.sh` | every backend update |

Python package versions are pinned by
[`backend/constraints.txt`](../backend/constraints.txt). On Linux the same
pins install cleanly, plus `uvloop`.

> These files must be on the branch the VPS checks out (`main` below)
> before § 5.

## 2. DNS, server preparation and packages

DNS A records (and AAAA records if you use IPv6) pointing at the VPS:
`zphirex.com`, `www.zphirex.com`, `api.zphirex.com`. Wait until
`dig +short zphirex.com api.zphirex.com www.zphirex.com` returns the VPS
address before § 7.

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install git curl gnupg nginx certbot \
    python3.12 python3.12-venv \
    postgresql-16 postgresql-16-pgvector

# MongoDB Database Tools (mongodump / mongorestore), from MongoDB's own
# repository. Only the tools are installed, never a MongoDB server.
curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc \
    | sudo gpg --dearmor -o /usr/share/keyrings/mongodb-server-8.0.gpg
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] https://repo.mongodb.org/apt/ubuntu noble/mongodb-org/8.0 multiverse" \
    | sudo tee /etc/apt/sources.list.d/mongodb-org-8.0.list
sudo apt update && sudo apt -y install mongodb-database-tools
mongodump --version

# Firewall: SSH and web only. PostgreSQL (5432) and uvicorn (8000) are never opened.
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable

# Service account and directories
sudo adduser --system --group --home /opt/zphirex --shell /usr/sbin/nologin zphirex
sudo install -d -o zphirex -g zphirex -m 0750 /var/lib/zphirex
sudo install -d -m 0755 /var/www/zphirex/html /var/www/certbot
sudo install -d -m 0700 /var/backups/zphirex
sudo chown -R <your-ssh-user>:www-data /var/www/zphirex/html    # FileZilla uploads
```

Tested versions on Ubuntu 24.04: PostgreSQL 16.15, nginx 1.24.0, systemd
255, Python 3.12.3, mongodump 100.20.1. Ubuntu's own pgvector package is
enough, because the app uses only the `vector` type and cosine distance
with no HNSW or IVFFlat index.

## 3. MongoDB Atlas (M0) for production files

Production gets its **own** database and its **own** credentials. Don't
reuse the local development database `ai_recruitment` or the development
user, both of which also exist on the laptop. A separate Atlas project for
Zphirex keeps its access list and users apart from development entirely.

1. **Cluster:** an M0 (Free) cluster. Atlas creates databases on first
   write, so `zphirex` appears with the first uploaded file.
2. **Database user** (*Database Access → Add New Database User*):
   - authentication: password (generate a long random one);
   - privileges: *Specific Privileges* → `readWrite` on database `zphirex`
     only, and nothing else.

   This one user serves both the API and `mongodump`/`mongorestore`. Atlas
   keeps users in `admin`; the app and the backup script both authenticate
   there (§ 11).
3. **Network access** (*Network Access → IP Access List*): add the VPS
   public IPv4 address as `<ip>/32`. Remove `0.0.0.0/0` from this project if
   it's there. If the VPS IP ever changes, update this entry, or the API
   won't start (§ 6).
4. **Connection string** (*Connect → Drivers*):
   `mongodb+srv://<user>:<password>@<cluster-host>.mongodb.net/?retryWrites=true&w=majority&appName=zphirex`.
   Leave **no database name in the path**, because the database comes from
   `MONGODB_DATABASE=zphirex`. URL-encode special characters in the
   password. It goes **only** into the VPS `backend/.env` (§ 5.2): never
   into git, chat, a ticket or a shell command line.

**M0 limits to keep in mind:**

- **Storage is capped at 512 MB** (data and indexes). Resumes are limited
  to 10 MB each but are typically 0.1–1 MB, so expect space for a few
  thousand files. Check usage under *Atlas → cluster → Metrics / Collections*,
  and plan an upgrade well before the cap: at the cap, uploads fail.
- **No Atlas cloud backups on M0.** The `mongodump` in `backup.sh` is the
  only backup of the file bytes (§ 10). Each run reads the whole `zphirex`
  database, so it counts against M0's operation and data-transfer limits.
  Once a day is reasonable at this size.
- Free clusters are shared and can be throttled. The API needs Atlas at
  startup (§ 6).

## 4. PostgreSQL: fresh database on the VPS

```bash
sudo -u postgres psql -tAc "SHOW listen_addresses"      # expected: localhost
sudo ss -ltnp | grep 5432                               # expected: 127.0.0.1 / ::1 only
```

Create the role, the database and pgvector. The `postgres` user can't read
inside `/opt/zphirex` (mode 0750), so pass the script through a temporary
copy:

```bash
sudo install -m 0644 /opt/zphirex/app/deploy/vps/postgres/init-database.sql /tmp/zphirex-init.sql
sudo -u postgres psql -X -f /tmp/zphirex-init.sql      # prompts for the zphirex_app password (hidden)
sudo rm /tmp/zphirex-init.sql
```

The script stops at the first error and never drops anything. A second run
fails instead of changing an existing setup (tested). It:

1. creates `zphirex_app`, **not a superuser and without `BYPASSRLS`**.
   Either would silently skip the Row-Level Security policies behind tenant
   isolation ([`security.md`](security.md)). Every tenant table has
   `FORCE ROW LEVEL SECURITY`, so the policies apply even though
   `zphirex_app` owns the tables;
2. creates database `zphirex`, owned by `zphirex_app`;
3. creates `vector` **as the administrator**. The migration's own
   `CREATE EXTENSION IF NOT EXISTS vector` is then a no-op. `pgcrypto` isn't
   needed, because `gen_random_uuid()` is built into PostgreSQL 13+.

**`DATABASE_URL` format:**
`postgresql+psycopg://zphirex_app:<url-encoded-password>@127.0.0.1:5432/zphirex`.
The `+psycopg` suffix is required.

## 5. Backend

### 5.1 Code and virtualenv

```bash
# private repository: give zphirex a read-only GitHub deploy key
sudo -u zphirex mkdir -p -m 700 /opt/zphirex/.ssh
sudo -u zphirex ssh-keygen -t ed25519 -N "" -f /opt/zphirex/.ssh/id_ed25519 -C "zphirex-vps-deploy"
sudo cat /opt/zphirex/.ssh/id_ed25519.pub      # GitHub → Settings → Deploy keys (no write access)
sudo -u zphirex git clone git@github.com:<owner>/<repo>.git /opt/zphirex/app

sudo -u zphirex python3.12 -m venv /opt/zphirex/venv
cd /opt/zphirex/app/backend
sudo -u zphirex /opt/zphirex/venv/bin/pip install --upgrade pip
sudo -u zphirex /opt/zphirex/venv/bin/pip install -c constraints.txt .
```

### 5.2 Production settings

```bash
sudo -u zphirex cp /opt/zphirex/app/deploy/vps/env/backend.env.example /opt/zphirex/app/backend/.env
sudo chmod 600 /opt/zphirex/app/backend/.env
sudo -u zphirex nano /opt/zphirex/app/backend/.env      # fill in every <...>
```

| Variable | Production value |
|---|---|
| `ENVIRONMENT` | `production` (makes the refresh-token cookie `Secure`) |
| `DEBUG` | `false` |
| `LOG_LEVEL` | `INFO` |
| `DATABASE_URL` | § 4 format |
| `JWT_SECRET_KEY` | new: `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `JWT_ALGORITHM` / `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | `HS256` / `15` / `30` |
| `CORS_ALLOW_ORIGINS` | `["https://zphirex.com","https://www.zphirex.com"]` |
| `FRONTEND_BASE_URL` | `https://zphirex.com` |
| `RESUME_STORAGE_PROVIDER` | `mongodb_gridfs` |
| `MONGODB_URI` | the production Atlas string (§ 3) |
| `MONGODB_DATABASE` | `zphirex` |
| `MAX_RESUME_SIZE_MB` / `MAX_MARKS_CARD_SIZE_MB` | `10` / `5` |
| `DEFAULT_PHONE_REGION` | `IN` (also read by a data migration) |
| Email | `RESEND_API_KEY` + `EMAIL_FROM`, **or** `SMTP_HOST`, `SMTP_PORT=587`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME`, `SMTP_USE_TLS=true` |
| AI (optional) | `GROQ_API_KEY`, `GEMINI_API_KEY`, plus the model settings in the template |
| **Never set** | `OLLAMA_BASE_URL`: it would point at a machine that isn't there |

The API, Alembic, `app.cli` and `backup.sh` all read this one file. The
first three read it themselves from `backend/`; `backup.sh` parses it
without executing it (§ 10). **It's the only place production credentials
exist.**

### 5.3 Schema and first administrator

```bash
cd /opt/zphirex/app/backend
sudo -u zphirex /opt/zphirex/venv/bin/alembic upgrade head
sudo -u zphirex /opt/zphirex/venv/bin/alembic current       # 6f7081920314 (head) as of this writing

# Verify ownership and row-level security (both must print 0):
sudo -u postgres psql -d zphirex -tAc "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner <> 'zphirex_app'"
sudo -u postgres psql -d zphirex -tAc "SELECT count(*) FROM pg_class WHERE relrowsecurity AND NOT relforcerowsecurity"

read -rs -p "Super admin password: " ZPX_PW; echo
sudo -u zphirex /opt/zphirex/venv/bin/python -m app.cli create-superadmin \
    --email <admin-email> --full-name "<Full Name>" --password "$ZPX_PW"
unset ZPX_PW
```

Tested: from an empty PostgreSQL 16 database, migrations create 59 tables,
all owned by `zphirex_app`, with forced row-level security on 57 of them.
The other two are not tenant data: `alembic_version` (migration
bookkeeping) and `permissions` (the global permission catalog).

## 6. systemd service

```bash
sudo install -m 0644 /opt/zphirex/app/deploy/vps/systemd/zphirex-api.service /etc/systemd/system/zphirex-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now zphirex-api
curl -s http://127.0.0.1:8000/readyz      # {"status":"ok","database":"ok","mongodb":"ok"}
journalctl -u zphirex-api -f
```

Use `install -m 0644`, not `cp`: files from a Windows checkout can keep
execute bits, and systemd warns about executable unit files.

- **Command:** `uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
  --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-server-header
  --timeout-graceful-shutdown 30`, from `/opt/zphirex/venv`, as user
  `zphirex`, in `backend/`.
- **One worker**, deliberately: rate limits are counted per process
  (`app/core/rate_limit.py`).
- **Starts at boot** (`enable`, `WantedBy=multi-user.target`).
  PostgreSQL and nginx are enabled by Ubuntu by default.
- **Atlas outages:** in `mongodb_gridfs` mode the API refuses to start
  while Atlas can't be reached (tested). It exits and systemd retries with
  `Restart=on-failure`, backing off from 5 s to at most 2 minutes
  (`RestartSteps`/`RestartMaxDelaySec`), with **no** start-rate limit
  (`StartLimitIntervalSec=0`). An Atlas or network outage, even during boot,
  therefore never leaves the API permanently stopped: it starts within about
  2 minutes of Atlas returning.
- **Migrations never run on restart.** They run only through
  `deploy-backend.sh` (§ 14) or by hand.
- **Hardening:** non-root user, `ProtectSystem=strict`; the only writable
  path is `/var/lib/zphirex`.

**Health endpoints.** `/healthz` reports only that the process is up and
touches nothing else. `/readyz` checks PostgreSQL and, in `mongodb_gridfs`
mode, MongoDB, and returns 503 if either is unreachable. Responses contain
only `ok`, `unreachable` or `unavailable`, never hosts, URIs or error
details (covered by tests in `backend/tests/test_health.py`).

## 7. nginx and TLS

```bash
sudo rm -f /etc/nginx/sites-enabled/default
sudo install -m 0644 /opt/zphirex/app/deploy/vps/nginx/zphirex-bootstrap.conf /etc/nginx/sites-available/zphirex.conf
sudo ln -sf /etc/nginx/sites-available/zphirex.conf /etc/nginx/sites-enabled/zphirex.conf
sudo nginx -t && sudo systemctl reload nginx

sudo certbot certonly --webroot -w /var/www/certbot \
    -d zphirex.com -d www.zphirex.com -d api.zphirex.com \
    --email <ops-email> --agree-tos --no-eff-email \
    --deploy-hook "systemctl reload nginx"

sudo install -m 0644 /opt/zphirex/app/deploy/vps/nginx/snippets/*.conf -t /etc/nginx/snippets/
sudo install -m 0644 /opt/zphirex/app/deploy/vps/nginx/zphirex.conf /etc/nginx/sites-available/zphirex.conf
sudo nginx -t && sudo systemctl reload nginx
sudo certbot renew --dry-run
```

Tested with nginx 1.24 and a stand-in certificate:

- both configs pass `nginx -t`;
- deep links serve `index.html` (refreshing a page works), with
  `Cache-Control: no-cache`;
- hashed `/assets/` files are `immutable`, and a missing asset returns 404;
- `www` → apex and HTTP → HTTPS redirects work, and the ACME path is served
  on HTTP;
- `api.zphirex.com` proxies to the API;
- `Permissions-Policy` allows camera and microphone for the site itself
  (assessment proctoring);
- unknown host names are refused.

The API proxy overwrites `X-Forwarded-For` with the real client address
(the rate limiter trusts its first entry) and allows 12 MB request bodies
and 120 s timeouts.

## 8. Frontend: build and upload

```bash
cd frontend
cp ../deploy/vps/env/frontend.env.production.example .env.production   # once; git-ignored
npm ci && npm run build
grep -l "api.zphirex.com" dist/assets/*.js     # must find a file
grep -l "localhost:8000" dist/assets/*.js      # must find nothing
```

- The variable is **`VITE_API_BASE_URL`**. `.env.production` is required
  because Vite also reads `.env.local` (which points at localhost) during
  builds, and `.env.production` takes priority over it.
- **FileZilla** (SFTP, port 22): upload the *contents* of `frontend/dist/`
  into `/var/www/zphirex/html/`. On updates, upload `assets/` first and
  `index.html` last.

## 9. Where data lives

| Data | Stored in | Backed up by |
|---|---|---|
| Users, candidates, applications, jobs, assessments, notes, audit log… | VPS PostgreSQL `zphirex` | `pg_dump` (§ 10) |
| File metadata (provider, GridFS id, name, type, size) | VPS PostgreSQL | `pg_dump` |
| File bytes: resumes, marks cards, program agreement documents | Atlas `zphirex`, bucket `resumes` (`resumes.files`, `resumes.chunks`) | `mongodump` (§ 10) |
| Role definitions | VPS PostgreSQL cluster | `pg_dumpall --globals-only` |
| Settings and secrets | `/opt/zphirex/app/backend/.env` | **not** in backups (back it up separately, e.g. in a password manager) |

**PostgreSQL and MongoDB must be restored as a pair.** A row in PostgreSQL
holds a GridFS id, and GridFS holds the bytes. Restoring one without the
matching other gives records whose files can't be opened, or files nothing
refers to.

## 10. Backups: `deploy/vps/scripts/backup.sh`

### What it does

Run as root. Each step must pass, or the whole run fails (§ 10.4):

1. **PostgreSQL**: `pg_dump --format=custom` of `zphirex`, run as the local
   `postgres` user over peer authentication, so **no password exists
   anywhere**. It confirms the dump is readable (`pg_restore --list`) and
   records the schema revision. Role definitions are saved too.
2. **MongoDB**: `mongodump --db=<MONGODB_DATABASE> --archive`, compressed
   with gzip. This covers every collection in the database, including the
   GridFS collections `resumes.files` and `resumes.chunks`.
   - *Credentials:* `MONGODB_URI` is read from the API's own `backend/.env`
     (no second configuration). It goes to `mongodump` through a temporary
     **0600 config file** (`--config`) in a private temp directory, deleted
     afterwards. It never appears in a command line, an environment
     variable or the log. Tool output is filtered so any credential it
     echoes is replaced with `<redacted>`, which matters for a malformed
     URI (tested).
   - *Least privilege:* `mongodump` runs as the unprivileged `zphirex` user,
     not root.
   - *authSource:* when the URI has none, the script sets the same one the
     API's driver uses (`admin` for Atlas). Otherwise `mongodump --db` would
     try to authenticate against `zphirex` and fail.
   - *Validation:* the archive must be non-empty, valid gzip and start with
     the mongodump archive header. Its collection list must contain **both**
     `resumes.files` and `resumes.chunks`. If PostgreSQL refers to GridFS
     files but the archive has no GridFS collections (for example a wrong
     `MONGODB_DATABASE`), the backup **fails**. A database with no files yet
     is reported as such.
3. **Local upload files**: archived only if any exist under
   `/var/lib/zphirex/uploads` (none while storage is GridFS).
4. `MANIFEST.txt` (what was included, schema revision, GridFS reference
   count) and `SHA256SUMS`, both verified.
5. The folder is published (renamed from `.partial`) **only after** all of
   the above pass. **Only then** does retention run.
6. Optional off-site copy (§ 12), disabled by default.

PostgreSQL is dumped first. A file is written to GridFS before its row is
committed, so every file a dumped row points at already exists when MongoDB
is dumped next.

### 10.1 Run it manually

```bash
sudo bash /opt/zphirex/app/deploy/vps/scripts/backup.sh
```

The output ends with one of:

- `BACKUP OK: PostgreSQL + MongoDB (zphirex)`: complete (exit 0);
- `BACKUP FAILED during: <step> — …`: nothing new kept, older backups
  untouched (exit 1);
- `OFF-SITE COPY FAILED — the local backup is complete` (exit 2, § 12).

A PostgreSQL-only result is printed as
`BACKUP OK: PostgreSQL only — no MongoDB is configured`, and it can only
happen when the API itself has no MongoDB configured. In `mongodb_gridfs`
mode MongoDB is mandatory.

**Schedule** (daily at 02:30, logged):

```bash
echo '30 2 * * * root /usr/bin/bash /opt/zphirex/app/deploy/vps/scripts/backup.sh >> /var/log/zphirex-backup.log 2>&1' \
    | sudo tee /etc/cron.d/zphirex-backup
```

Check `/var/log/zphirex-backup.log` regularly, or have cron mail failures,
since a failing backup only says so there.

### 10.2 Directory and file layout

```
/var/backups/zphirex/                       0700 root
└── 20261109T023000Z/                       one folder per run (UTC)
    ├── postgres-zphirex-20261109T023000Z.dump
    ├── postgres-globals-20261109T023000Z.sql
    ├── mongodb-zphirex-20261109T023000Z.archive.gz
    ├── MANIFEST.txt
    └── SHA256SUMS                          all files 0600 root
```

An unfinished run lives in `<stamp>.partial/` and is deleted on failure.
`.lock` prevents overlapping runs.

### 10.3 Retention and settings

Environment variables, with defaults:

| Variable | Default | Meaning |
|---|---|---|
| `RETENTION_DAYS` | `14` | backup folders older than this are deleted after a *successful* run |
| `BACKUP_DIR` | `/var/backups/zphirex` | absolute path, never `/` |
| `DB_NAME` | `zphirex` | PostgreSQL database |
| `BACKEND_ENV_FILE` | `/opt/zphirex/app/backend/.env` | where `MONGODB_*` and `RESUME_STORAGE_PROVIDER` are read |
| `OFFSITE_RCLONE_REMOTE` | empty (off) | § 12 |

Example: `sudo RETENTION_DAYS=30 bash …/backup.sh`. Retention only deletes
folders named like `20261109T023000Z` directly inside `BACKUP_DIR`, never
anything else (tested). It never runs after a failed backup, so failures
can't wipe out the last good backups.

### 10.4 Failures

It exits non-zero and keeps nothing for any of these (all tested):

- PostgreSQL unreachable or the dump fails;
- MongoDB unreachable, authentication fails or the URI is malformed;
- `mongodump` not installed;
- an archive that is empty or invalid;
- the GridFS bucket is incomplete, or PostgreSQL refers to GridFS files the
  archive doesn't contain;
- `mongodb_gridfs` mode with an empty `MONGODB_URI`;
- another backup is already running;
- invalid settings.

### 10.5 Verify a backup

The backup directory is root-only, so file names (and `*` patterns) can
only be read from a root shell:

```bash
sudo -i
B=/var/backups/zphirex/<stamp>
(cd $B && sha256sum -c SHA256SUMS)                          # every line: OK
cat $B/MANIFEST.txt                                         # what's inside
pg_restore --list $B/postgres-zphirex-*.dump | head         # PostgreSQL archive readable
gzip -t $B/mongodb-zphirex-*.archive.gz && echo "gzip OK"   # MongoDB archive intact
exit
```

## 11. Restore

Restore **PostgreSQL and MongoDB from the same backup folder** (§ 9). All
commands below were run successfully against the test containers; they
have **not** been run against production.

Run them from a **root shell** (`sudo -i`): the backup directory is
root-only, so a normal user's shell can't expand the `*` in the file names.

### 11.1 PostgreSQL

Into the fresh, empty database from § 4. If `zphirex` already has data, a
replacement restore means first recreating the database, which is a
deliberate, destructive decision for the operator.

```bash
# as root (sudo -i)
B=/var/backups/zphirex/<stamp>
systemctl stop zphirex-api
install -o postgres -g postgres -m 600 $B/postgres-zphirex-*.dump /tmp/zphirex.dump
# Skip the dump's EXTENSION entries: init-database.sql already created pgvector.
runuser -u postgres -- sh -c "pg_restore --list /tmp/zphirex.dump | grep -v ' EXTENSION ' > /tmp/zphirex-restore.list"
runuser -u postgres -- pg_restore --no-owner --role=zphirex_app --exit-on-error \
    --use-list=/tmp/zphirex-restore.list -d zphirex /tmp/zphirex.dump
rm -f /tmp/zphirex.dump /tmp/zphirex-restore.list
```

Tested result: same schema revision, all tables owned by `zphirex_app`,
row-level security still forced. `--exit-on-error` also refused to restore
a database whose foreign keys were inconsistent.

### 11.2 MongoDB (GridFS)

`mongorestore` reads the URI from a temporary 0600 config file, as the
backup does, never from the command line:

```bash
# as root (sudo -i)
B=/var/backups/zphirex/<stamp>
install -d -m 700 /root/zphirex-restore
(umask 077; printf "uri: '%s'\n" "$(grep -m1 '^MONGODB_URI=' /opt/zphirex/app/backend/.env | cut -d= -f2-)" \
    > /root/zphirex-restore/mongo.yaml)

# Disaster restore into production `zphirex`: --drop replaces each restored collection.
gzip -dc $B/mongodb-zphirex-*.archive.gz \
    | mongorestore --config=/root/zphirex-restore/mongo.yaml --archive --nsInclude='zphirex.*' --drop

rm -rf /root/zphirex-restore
```

- This restores `resumes.files` and `resumes.chunks` together (plus any
  other collection in the archive). The production user's `readWrite` on
  `zphirex` is enough.
- **Test restores:** copy into another database name with
  `--nsFrom='zphirex.*' --nsTo='zphirex_restore.*'` (tested; restored files
  were byte-identical). On M0, a full copy doubles storage use against the
  512 MB cap, so for routine restore tests prefer a local or temporary
  MongoDB over the production cluster, and drop the copy afterwards.

### 11.3 Coordinated recovery (both databases)

1. Pick **one** backup folder; check `SHA256SUMS` and `MANIFEST.txt` (§ 10.5).
2. `sudo -i`, then `systemctl stop zphirex-api`, so nothing writes during the restore.
3. Restore PostgreSQL (§ 11.1).
4. Restore MongoDB from the **same** folder (§ 11.2).
5. Check: `alembic current` shows the revision in `MANIFEST.txt`;
   `sudo systemctl start zphirex-api`; `curl -s http://127.0.0.1:8000/readyz`
   returns all `ok`; download one resume in the UI.
6. If the app version is newer than the restored schema, run
   `alembic upgrade head` before starting the API.

Anything uploaded after the backup was taken is lost. Files uploaded
between the PostgreSQL and MongoDB dumps survive in MongoDB without a
record pointing at them, which is harmless.

## 12. Backup limitations and off-site copies

**Today, every backup sits on the VPS's own disk.** That protects against
mistakes, bad migrations and corrupted data, but **not** against losing the
VPS: disk failure, a deleted or compromised server, or account problems.
Atlas M0 has no backups of its own. Until an off-site copy exists, the
`mongodump` on the VPS is the **only** copy of the file bytes besides Atlas
itself.

**Recommended next step (not configured yet):** an encrypted copy to
external object storage with [rclone](https://rclone.org):

1. Create a bucket with any S3-compatible provider (e.g. Backblaze B2,
   Cloudflare R2, Wasabi) and an access key limited to that bucket.
2. On the VPS: `sudo apt install rclone`, then `sudo rclone config`. Create
   the storage remote, then a **`crypt` remote** wrapping it (e.g.
   `zphirex-offsite`), so files are encrypted on the VPS before upload. Keep
   the crypt passwords in a password manager **off** the VPS, or the copies
   can't be decrypted after losing the server.
3. Enable it in cron: `OFFSITE_RCLONE_REMOTE=zphirex-offsite:backups` before
   `/usr/bin/bash` in `/etc/cron.d/zphirex-backup`.
4. Set a lifecycle or retention rule on the bucket.

When enabled, each successful local backup folder is copied to
`<remote>/<stamp>/`. A missing or failing off-site copy **never** fails or
removes the local backup; it exits with status 2 and says so (tested).
Hostinger VPS snapshots are a useful extra layer but live with the same
provider.

## 13. Go-live checklist

- [ ] DNS resolves; HTTPS is valid for all three names; `/readyz` returns all `ok`
- [ ] Ports 5432 and 8000 are not reachable from outside; `ufw status` shows 22/80/443
- [ ] Sign in, refresh a deep page, and stay signed in
- [ ] Apply with a resume; the recruiter downloads it; it appears in Atlas → `zphirex` → `resumes.files`
- [ ] Email OTP / invitation arrives, and its link opens on `https://zphirex.com`
- [ ] Assessment page can use the camera and microphone
- [ ] `sudo bash …/backup.sh` → `BACKUP OK: PostgreSQL + MongoDB (zphirex)`; cron installed
- [ ] Atlas access list = VPS IP only; production user = `readWrite@zphirex` only
- [ ] `sudo certbot renew --dry-run` succeeds
- [ ] `backend/.env` is mode 600 and its secrets are stored in a password manager

## 14. Updating

```bash
sudo bash /opt/zphirex/app/deploy/vps/scripts/deploy-backend.sh main
```

This runs a full backup (aborting the deploy if it fails), then a
fast-forward-only `git` update, a pinned `pip install`,
`alembic upgrade head`, a restart, and a wait for `/readyz`. Frontend
updates: rebuild (§ 8) and upload with FileZilla.

## 15. Not yet verified on the real VPS

These were tested only in containers. Confirm them on the server:

- the Atlas connection itself: `mongodb+srv` DNS, the access list, and
  `mongodump` against M0 (run `backup.sh` once by hand right after § 6);
- Let's Encrypt issuance and renewal (the tests used a stand-in certificate);
- `ufw` and the real network exposure (containers have no firewall);
- the `deploy-backend.sh` flow (needs the GitHub deploy key and real systemd).

Known follow-ups: no Content-Security-Policy yet (`index.html` has an
inline theme script); `/docs` and `/openapi.json` are public on the API
host; rate limits are per process (hence 1 worker).
