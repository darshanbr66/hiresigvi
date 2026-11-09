#!/usr/bin/env bash
# Zphirex — full backup: PostgreSQL (application records + file metadata)
# AND MongoDB GridFS (the uploaded file bytes), taken together so both halves
# of every stored file can be restored as one consistent set.
#
# Run as root (cron, or manually before every migration):
#     sudo bash /opt/zphirex/app/deploy/vps/scripts/backup.sh
#
# Output: $BACKUP_DIR/<STAMP>/  (STAMP = UTC time, e.g. 20261109T023000Z)
#     postgres-<db>-<STAMP>.dump          pg_dump custom format
#     postgres-globals-<STAMP>.sql        role definitions (pg_dumpall -g)
#     mongodb-<db>-<STAMP>.archive.gz     mongodump archive, gzip-compressed
#     uploads-<STAMP>.tar.gz              only if local upload files exist
#     MANIFEST.txt                        what was backed up, sizes, versions
#     SHA256SUMS                          checksums of all of the above
#
# Exit status:
#     0  complete backup written and validated
#     1  backup FAILED — nothing new was kept, no old backup was deleted
#     2  local backup complete, but the optional off-site copy failed
#
# Configuration — no second configuration system:
#   * MongoDB settings are read from the API's own protected settings file
#     ($BACKEND_ENV_FILE): RESUME_STORAGE_PROVIDER, MONGODB_URI,
#     MONGODB_DATABASE — exactly what the running API uses.
#   * The script's own knobs are environment variables (defaults below), e.g.
#         sudo RETENTION_DAYS=30 bash backup.sh
#
# Credentials: PostgreSQL is read as the local `postgres` user over peer
# authentication (no password anywhere). The MongoDB URI is passed to
# mongodump through a temporary 0600 config file, never as a command-line
# argument (visible in `ps`) and never printed; tool output is filtered to
# redact any credentials it might echo.

set -Eeuo pipefail
umask 077
# The postgres and zphirex users it switches to cannot enter root's home.
cd /

DB_NAME="${DB_NAME:-zphirex}"
APP_USER="${APP_USER:-zphirex}"
BACKEND_ENV_FILE="${BACKEND_ENV_FILE:-/opt/zphirex/app/backend/.env}"
UPLOAD_DIR="${UPLOAD_DIR:-/var/lib/zphirex/uploads}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/zphirex}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
# Optional off-site copy, DISABLED when empty: an rclone destination such as
# "zphirex-offsite:backups" (ideally an rclone `crypt` remote, so archives
# are encrypted before leaving the VPS). docs/deployment-vps.md § 12.
OFFSITE_RCLONE_REMOTE="${OFFSITE_RCLONE_REMOTE:-}"

# --- helpers -----------------------------------------------------------------

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# Masks credentials in tool output. Two layers: the exact secret strings
# (set in REDACT_VALUES once the URI is known — replaced inside bash, so the
# secrets never become a sed/grep argument visible in `ps`), and any
# "scheme://user:pass@" pattern. The first layer matters because a tool that
# rejects a malformed URI may echo pieces of it in forms the pattern misses.
REDACT_VALUES=()
redact() {
    local line value
    while IFS= read -r line || [[ -n "$line" ]]; do
        for value in "${REDACT_VALUES[@]}"; do
            [[ ${#value} -ge 4 ]] && line="${line//"$value"/<redacted>}"
        done
        printf '%s\n' "$line"
    done | sed -E 's#://[^@/[:space:]]*@#://<redacted>@#g'
}

STEP="startup checks"
die() { log "BACKUP FAILED during: $STEP — $*" >&2; exit 1; }

# Reads one KEY from the backend settings file the way the API's dotenv
# loader does for plain KEY=value lines: first match wins, surrounding
# quotes are removed, an unquoted trailing " # comment" is dropped. The file
# is never `source`d (it is not shell syntax) and values are never echoed.
env_value() {
    local key="$1" line value
    line="$(grep -m1 -E "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$BACKEND_ENV_FILE" || true)"
    [[ -n "$line" ]] || return 0
    value="${line#*=}"
    value="${value%$'\r'}"
    value="${value#"${value%%[![:space:]]*}"}"
    if [[ ${#value} -ge 2 && "${value:0:1}" == '"' && "${value: -1}" == '"' ]] \
        || [[ ${#value} -ge 2 && "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
    else
        value="${value%%[[:space:]]#*}"
        value="${value%"${value##*[![:space:]]}"}"
    fi
    printf '%s' "$value"
}

psql_value() {
    runuser -u postgres -- psql -X -q -t -A -v ON_ERROR_STOP=1 -d "$DB_NAME" -c "$1"
}

# Rows that point at a file stored in GridFS. Postgres is read as the
# `postgres` superuser, so Row-Level Security does not hide other tenants.
GRIDFS_REFERENCE_COLUMNS=(
    "resumes:storage_provider"
    "candidates:tenth_marks_card_storage_provider"
    "program_enrollments:agreement_document_storage_provider"
)
count_gridfs_references() {
    local spec table column present n total=0
    for spec in "${GRIDFS_REFERENCE_COLUMNS[@]}"; do
        table="${spec%%:*}"
        column="${spec#*:}"
        present="$(psql_value "SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = '$table' AND column_name = '$column'")"
        if [[ "$present" == "1" ]]; then
            n="$(psql_value "SELECT count(*) FROM public.$table WHERE $column = 'mongodb_gridfs'")"
            total=$((total + n))
        fi
    done
    printf '%s' "$total"
}

# First bytes of the decompressed stream (the archive header and prelude).
# pipefail is off inside this subshell only: `head` closing the pipe early
# is expected here, not an error.
archive_head() { ( set +o pipefail; gzip -dc -- "$1" | head -c "$2" ); }

# --- startup checks ----------------------------------------------------------

[[ "$(id -u)" -eq 0 ]] || die "must run as root (reads the database as the postgres user)"
[[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] && (( RETENTION_DAYS >= 1 )) \
    || die "RETENTION_DAYS must be a positive integer"
[[ "$BACKUP_DIR" == /* && "$BACKUP_DIR" != "/" && "$BACKUP_DIR" != *".."* ]] \
    || die "BACKUP_DIR must be an absolute directory path other than /"
[[ -r "$BACKEND_ENV_FILE" ]] || die "cannot read $BACKEND_ENV_FILE"

STORAGE_PROVIDER="$(env_value RESUME_STORAGE_PROVIDER)"
STORAGE_PROVIDER="${STORAGE_PROVIDER:-local}"
MONGODB_URI_VALUE="$(env_value MONGODB_URI)"
if [[ -n "$MONGODB_URI_VALUE" ]]; then
    _userinfo="${MONGODB_URI_VALUE#*://}"
    _userinfo="${_userinfo%%@*}"
    _password="${_userinfo#*:}"
    # Raw and URL-decoded forms (%40 -> @ ...) of every secret part.
    REDACT_VALUES=(
        "$MONGODB_URI_VALUE" "$_userinfo" "$_password"
        "$(printf '%b' "${_password//%/\\x}")"
    )
    unset _userinfo _password
fi
MONGO_DB="$(env_value MONGODB_DATABASE)"
MONGO_DB="${MONGO_DB:-ai_recruitment}"   # the API's own default (app/core/config.py)

# MongoDB is part of the backup whenever the API stores (or can still read)
# files there. In mongodb_gridfs mode it is mandatory: a PostgreSQL-only
# backup must never be reported as a complete one.
MONGO_ENABLED=0
if [[ "$STORAGE_PROVIDER" == "mongodb_gridfs" ]]; then
    [[ -n "$MONGODB_URI_VALUE" ]] || die "RESUME_STORAGE_PROVIDER=mongodb_gridfs but MONGODB_URI is empty"
    MONGO_ENABLED=1
elif [[ -n "$MONGODB_URI_VALUE" ]]; then
    MONGO_ENABLED=1
fi
if (( MONGO_ENABLED )); then
    command -v mongodump > /dev/null \
        || die "mongodump not found — install mongodb-database-tools (docs/deployment-vps.md § 2)"
    [[ "$MONGO_DB" =~ ^[A-Za-z0-9_-]+$ ]] || die "MONGODB_DATABASE has unexpected characters"
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# Never run two backups at once (cron overlapping a manual run).
exec 9> "$BACKUP_DIR/.lock"
flock -n 9 || die "another backup is already running"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="$BACKUP_DIR/$STAMP"
PARTIAL="$TARGET.partial"
MONGO_TMP=""

cleanup() {
    # A failed run leaves no half-written folder that looks like a backup.
    [[ -d "$PARTIAL" ]] && rm -rf -- "$PARTIAL"
    [[ -n "$MONGO_TMP" && -d "$MONGO_TMP" ]] && rm -rf -- "$MONGO_TMP"
    return 0
}
trap cleanup EXIT
trap 'log "BACKUP FAILED during: $STEP (exit $?) — no backup kept, existing backups untouched" >&2' ERR

mkdir -p -- "$PARTIAL"
PG_FILE="postgres-$DB_NAME-$STAMP.dump"
GLOBALS_FILE="postgres-globals-$STAMP.sql"
MONGO_FILE="mongodb-$MONGO_DB-$STAMP.archive.gz"
UPLOADS_FILE="uploads-$STAMP.tar.gz"
log "Backup $STAMP -> $TARGET"

# --- 1. PostgreSQL -----------------------------------------------------------
# Dumped BEFORE MongoDB: files are written to GridFS before the row that
# references them is committed, so every file a PostgreSQL row in this dump
# points at already exists when MongoDB is dumped afterwards.

STEP="PostgreSQL dump"
log "PostgreSQL: dumping $DB_NAME"
runuser -u postgres -- pg_dump --format=custom --compress=6 --no-password --dbname="$DB_NAME" \
    > "$PARTIAL/$PG_FILE"

STEP="PostgreSQL dump verification"
[[ -s "$PARTIAL/$PG_FILE" ]] || die "dump file is empty"
PG_TOC_ENTRIES="$(runuser -u postgres -- pg_restore --list < "$PARTIAL/$PG_FILE" | grep -vc '^;' || true)"
(( PG_TOC_ENTRIES > 0 )) || die "pg_restore could not list the dump"
PG_TABLE_DATA="$(runuser -u postgres -- pg_restore --list < "$PARTIAL/$PG_FILE" | grep -c ' TABLE DATA ' || true)"
ALEMBIC_REVISION="$(psql_value "SELECT CASE WHEN to_regclass('public.alembic_version') IS NULL
    THEN 'none' ELSE (SELECT string_agg(version_num, ',') FROM alembic_version) END")"
log "PostgreSQL: ok ($PG_TOC_ENTRIES archive entries, $PG_TABLE_DATA tables, schema revision $ALEMBIC_REVISION)"

STEP="PostgreSQL role definitions"
runuser -u postgres -- pg_dumpall --globals-only --no-password > "$PARTIAL/$GLOBALS_FILE"
[[ -s "$PARTIAL/$GLOBALS_FILE" ]] || die "pg_dumpall produced no output"

STEP="counting GridFS references in PostgreSQL"
GRIDFS_REFERENCES="$(count_gridfs_references)"

# --- 2. MongoDB GridFS -------------------------------------------------------

MONGO_SUMMARY="not configured (RESUME_STORAGE_PROVIDER=$STORAGE_PROVIDER, MONGODB_URI unset)"
if (( MONGO_ENABLED )); then
    STEP="MongoDB dump"
    log "MongoDB: dumping database $MONGO_DB (all collections, including GridFS resumes.files / resumes.chunks)"

    # mongodump runs as the unprivileged API user — root is not needed to
    # talk to Atlas. Its URI goes in a private config file in a directory
    # only that user can enter, removed on exit.
    MONGO_TMP="$(runuser -u "$APP_USER" -- mktemp -d)"
    # Authenticate exactly where the API does. Without authSource in the
    # URI, the API's driver (pymongo) uses the URI's database path, else
    # "admin" — where Atlas keeps its users — but mongodump given --db would
    # authenticate against that database instead and fail. So the same
    # authSource pymongo would pick is made explicit for mongodump.
    DUMP_URI="$MONGODB_URI_VALUE"
    if [[ "$DUMP_URI" != *[?\&]authSource=* ]]; then
        _hosts_path="${DUMP_URI#*://}"
        _hosts_path="${_hosts_path#*@}"
        _hosts_path="${_hosts_path%%\?*}"
        _auth_db="admin"
        if [[ "$_hosts_path" == */* && -n "${_hosts_path#*/}" ]]; then
            _auth_db="${_hosts_path#*/}"
        fi
        if [[ "$DUMP_URI" == *\?* ]]; then
            DUMP_URI="$DUMP_URI&authSource=$_auth_db"
        elif [[ "$_hosts_path" == */* ]]; then
            DUMP_URI="$DUMP_URI?authSource=$_auth_db"
        else
            DUMP_URI="$DUMP_URI/?authSource=$_auth_db"
        fi
        unset _hosts_path _auth_db
    fi
    printf "uri: '%s'\n" "${DUMP_URI//\'/\'\'}" > "$MONGO_TMP/mongodump.yaml"
    REDACT_VALUES+=("$DUMP_URI")
    unset DUMP_URI
    chown "$APP_USER": "$MONGO_TMP/mongodump.yaml"
    chmod 600 "$MONGO_TMP/mongodump.yaml"

    runuser -u "$APP_USER" -- mongodump --config="$MONGO_TMP/mongodump.yaml" \
            --db="$MONGO_DB" --archive \
            2> >(redact >&2) \
        | gzip -6 > "$PARTIAL/$MONGO_FILE"
    rm -rf -- "$MONGO_TMP"
    MONGO_TMP=""

    STEP="MongoDB archive verification"
    [[ -s "$PARTIAL/$MONGO_FILE" ]] || die "MongoDB archive is empty"
    gzip -t -- "$PARTIAL/$MONGO_FILE" || die "MongoDB archive is not valid gzip"
    # mongodump archives start with the magic number 0x8199e26d (little-endian).
    MAGIC="$(archive_head "$PARTIAL/$MONGO_FILE" 4 | od -An -tx1 | tr -d ' \n')"
    [[ "$MAGIC" == "6de29981" ]] || die "not a mongodump archive (unexpected header)"
    # The archive prelude lists every dumped collection before any data.
    NAMESPACES="$(archive_head "$PARTIAL/$MONGO_FILE" 16777216 \
        | LC_ALL=C grep -a -o -E 'resumes\.(files|chunks)' | sort -u | tr '\n' ' ' || true)"
    HAS_FILES=0; HAS_CHUNKS=0
    [[ "$NAMESPACES" == *"resumes.files"* ]] && HAS_FILES=1
    [[ "$NAMESPACES" == *"resumes.chunks"* ]] && HAS_CHUNKS=1

    if (( HAS_FILES && HAS_CHUNKS )); then
        MONGO_SUMMARY="ok — GridFS bucket 'resumes' (resumes.files + resumes.chunks) included"
    elif (( HAS_FILES || HAS_CHUNKS )); then
        die "GridFS bucket is incomplete in the archive (found: ${NAMESPACES:-nothing})"
    elif (( GRIDFS_REFERENCES > 0 )); then
        die "PostgreSQL references $GRIDFS_REFERENCES GridFS file(s) but the archive of '$MONGO_DB' has no GridFS collections — check MONGODB_DATABASE"
    else
        MONGO_SUMMARY="ok — database '$MONGO_DB' has no GridFS files yet (none referenced in PostgreSQL)"
    fi
    log "MongoDB: $MONGO_SUMMARY"
elif (( GRIDFS_REFERENCES > 0 )); then
    STEP="MongoDB configuration"
    die "PostgreSQL references $GRIDFS_REFERENCES GridFS file(s) but MongoDB is not configured in $BACKEND_ENV_FILE"
else
    log "MongoDB: $MONGO_SUMMARY"
fi

# --- 3. Local upload files (only when the local-disk provider stored any) ----

UPLOADS_SUMMARY="none on local disk"
if [[ -d "$UPLOAD_DIR" ]] && [[ -n "$(find "$UPLOAD_DIR" -type f -print -quit)" ]]; then
    STEP="local uploads archive"
    log "Uploads: archiving $UPLOAD_DIR"
    tar --create --gzip --file "$PARTIAL/$UPLOADS_FILE" \
        --directory "$(dirname "$UPLOAD_DIR")" "$(basename "$UPLOAD_DIR")"
    gzip -t -- "$PARTIAL/$UPLOADS_FILE"
    UPLOADS_SUMMARY="included"
fi

# --- 4. Manifest and checksums -----------------------------------------------

STEP="manifest and checksums"
{
    echo "Zphirex backup $STAMP"
    echo "host:                 $(hostname)"
    echo "postgresql database:  $DB_NAME (schema revision $ALEMBIC_REVISION, $PG_TABLE_DATA tables)"
    echo "gridfs references:    $GRIDFS_REFERENCES row(s) in PostgreSQL"
    echo "storage provider:     $STORAGE_PROVIDER"
    echo "mongodb:              $MONGO_SUMMARY"
    (( MONGO_ENABLED )) && echo "mongodb database:     $MONGO_DB"
    echo "local uploads:        $UPLOADS_SUMMARY"
    echo "order:                PostgreSQL first, then MongoDB (restore both from this folder)"
    echo
    echo "files (bytes):"
    find "$PARTIAL" -maxdepth 1 -type f ! -name MANIFEST.txt -printf '  %s\t%f\n' | sort -k2
} > "$PARTIAL/MANIFEST.txt"
(cd "$PARTIAL" && sha256sum -- * > SHA256SUMS && sha256sum --check --quiet SHA256SUMS)

# --- 5. Publish, then (and only then) apply retention ------------------------

STEP="finalizing"
mv -- "$PARTIAL" "$TARGET"
trap - ERR
log "Backup complete: $TARGET ($(du -sh "$TARGET" | cut -f1))"
if (( MONGO_ENABLED )); then
    log "BACKUP OK: PostgreSQL + MongoDB ($MONGO_DB)"
else
    log "BACKUP OK: PostgreSQL only — no MongoDB is configured for this API"
fi

# Retention runs only after a new backup has been written and validated, and
# only ever touches timestamped folders directly inside $BACKUP_DIR.
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d \
    -regextype posix-extended -regex '.*/[0-9]{8}T[0-9]{6}Z' \
    ! -path "$TARGET" -mtime "+$RETENTION_DAYS" \
    -printf 'Retention: removing %p\n' -exec rm -rf -- {} +
# Leftovers of runs that were killed outright (the EXIT trap could not run).
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d \
    -regextype posix-extended -regex '.*/[0-9]{8}T[0-9]{6}Z\.partial' \
    -mtime +0 -printf 'Retention: removing stale %p\n' -exec rm -rf -- {} +

# --- 6. Optional off-site copy (disabled unless configured) ------------------

if [[ -z "$OFFSITE_RCLONE_REMOTE" ]]; then
    log "Off-site copy: not configured (backups exist only on this VPS)"
    exit 0
fi
if ! command -v rclone > /dev/null; then
    log "OFF-SITE COPY FAILED: rclone is not installed — the local backup is complete" >&2
    exit 2
fi
log "Off-site copy: uploading to $OFFSITE_RCLONE_REMOTE/$STAMP"
if rclone copy --quiet "$TARGET" "$OFFSITE_RCLONE_REMOTE/$STAMP" 2> >(redact >&2); then
    log "Off-site copy: ok"
else
    log "OFF-SITE COPY FAILED — the local backup is complete" >&2
    exit 2
fi
