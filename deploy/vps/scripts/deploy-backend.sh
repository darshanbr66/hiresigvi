#!/usr/bin/env bash
# Zphirex — deploy (or update) the backend on the VPS from Git.
#
#     sudo bash /opt/zphirex/app/deploy/vps/scripts/deploy-backend.sh [branch]
#
# Steps: back up -> fast-forward the checkout -> install pinned dependencies
# -> alembic upgrade head -> restart -> wait for /readyz.
# Each step stops the script on failure, so the API is never restarted onto
# code whose migrations did not apply. Migrations run here, explicitly, once
# per deploy — never on every service restart.
#
# Not used for the very first install (docs/deployment-vps.md § 3-5 set up
# the checkout, virtualenv and .env), though it is safe to run after it.

set -Eeuo pipefail

BRANCH="${1:-main}"
APP_USER="zphirex"
APP_DIR="/opt/zphirex/app"
VENV="/opt/zphirex/venv"
SERVICE="zphirex-api"
READY_URL="http://127.0.0.1:8000/readyz"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '\n==> %s\n' "$*"; }
as_app() { runuser -u "$APP_USER" -- "$@"; }

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run with sudo (it restarts the systemd service and runs the backup)." >&2
    exit 1
fi
if [[ ! -f "$APP_DIR/backend/.env" ]]; then
    echo "$APP_DIR/backend/.env is missing — see deploy/vps/env/backend.env.example." >&2
    exit 1
fi

# Back up first when the database already has a schema (skipped on the very
# first deploy, when there is nothing to protect yet).
if runuser -u postgres -- psql -d zphirex -tAc "SELECT to_regclass('public.alembic_version')" \
        | grep -q alembic_version; then
    log "Backing up database and uploads before migrating"
    bash "$SCRIPT_DIR/backup.sh"
fi

log "Updating checkout to origin/$BRANCH"
as_app git -C "$APP_DIR" fetch --prune origin
as_app git -C "$APP_DIR" checkout "$BRANCH"
# --ff-only: refuses to merge or discard anything if the server's checkout
# has diverged (someone edited files on the VPS) — fix that by hand.
as_app git -C "$APP_DIR" merge --ff-only "origin/$BRANCH"
as_app git -C "$APP_DIR" log -1 --oneline

log "Installing backend dependencies (pinned by backend/constraints.txt)"
cd "$APP_DIR/backend"
as_app "$VENV/bin/pip" install --quiet --upgrade pip
as_app "$VENV/bin/pip" install --quiet -c constraints.txt .

log "Applying database migrations"
as_app "$VENV/bin/alembic" current
as_app "$VENV/bin/alembic" upgrade head
as_app "$VENV/bin/alembic" current

log "Restarting $SERVICE"
systemctl restart "$SERVICE"

log "Waiting for $READY_URL"
for _ in $(seq 1 30); do
    if curl --silent --fail --max-time 3 "$READY_URL" > /dev/null; then
        curl --silent "$READY_URL"; echo
        log "Deploy complete"
        exit 0
    fi
    sleep 2
done

echo "API did not become ready within 60 s. Recent logs:" >&2
journalctl -u "$SERVICE" -n 50 --no-pager >&2
exit 1
