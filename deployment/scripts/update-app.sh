#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

APP_DIR="${APP_DIR:-/opt/fileaway}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$APP_DIR"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Tracked files have local changes. Commit or discard them first." >&2
  exit 1
fi

echo "Creating pre-update backup..."
"$SCRIPT_DIR/backup.sh"

previous_commit="$(git rev-parse HEAD)"
echo "Updating from commit $previous_commit..."

git -c safe.directory="$APP_DIR" pull --ff-only

"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

chown -R root:fileaway "$APP_DIR"
chmod -R o-rwx "$APP_DIR"
chmod -R g-w "$APP_DIR"
chmod 0750 "$APP_DIR"/deployment/scripts/*.sh

"$SCRIPT_DIR/migrate.sh" --no-backup

systemctl restart fileaway
sleep 2

if ! "$SCRIPT_DIR/healthcheck.sh"; then
  cat <<EOF

Update restarted the service but the health check failed.
Previous commit: $previous_commit

Do not delete any data. Inspect:
  sudo journalctl -u fileaway -n 100 --no-pager

If a code rollback is required:
  cd $APP_DIR
  git checkout $previous_commit
  sudo $APP_DIR/deployment/scripts/migrate.sh
  sudo systemctl restart fileaway

If data must be restored, follow deployment/README.md "Restore".
EOF
  exit 1
fi

echo "Update complete. Uploads and database were not deleted."
