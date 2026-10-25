#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/fileaway}"
ENV_FILE="${ENV_FILE:-/etc/fileaway/backend.env}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is not set in $ENV_FILE." >&2
  exit 1
fi

if [[ "${1:-}" != "--no-backup" && -f /var/lib/fileaway/fileaway.db ]]; then
  echo "Creating a backup before migration..."
  "$SCRIPT_DIR/backup.sh"
fi

cd "$APP_DIR"

alembic_env=(
  "DATABASE_URL=${DATABASE_URL}"
  "UPLOAD_DIR=${UPLOAD_DIR:-/var/lib/fileaway/uploads}"
)

if [[ ${EUID} -eq 0 ]] && id fileaway >/dev/null 2>&1; then
  runuser -u fileaway -- env "${alembic_env[@]}" \
    "$APP_DIR/.venv/bin/python" -m alembic \
    -c "$APP_DIR/alembic.ini" upgrade head
  runuser -u fileaway -- env "${alembic_env[@]}" \
    "$APP_DIR/.venv/bin/python" -m alembic \
    -c "$APP_DIR/alembic.ini" current
else
  env "${alembic_env[@]}" \
    "$APP_DIR/.venv/bin/python" -m alembic \
    -c "$APP_DIR/alembic.ini" upgrade head
  env "${alembic_env[@]}" \
    "$APP_DIR/.venv/bin/python" -m alembic \
    -c "$APP_DIR/alembic.ini" current
fi

echo "Database migrations are up to date."
