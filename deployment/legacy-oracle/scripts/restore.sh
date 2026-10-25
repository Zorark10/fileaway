#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:-}"
TARGET="${2:-}"

if [[ -z "$ARCHIVE" || -z "$TARGET" ]]; then
  echo "Usage: $0 <backup.tar.gz> <empty-target-directory>" >&2
  echo "This restores to a separate location. It never writes to /var/lib/fileaway." >&2
  exit 1
fi

if [[ ! -f "$ARCHIVE" ]]; then
  echo "Backup not found: $ARCHIVE" >&2
  exit 1
fi

if [[ "$TARGET" == "/" || "$TARGET" == "/var" || "$TARGET" == "/var/lib/fileaway" ]]; then
  echo "Refusing to restore directly into $TARGET." >&2
  exit 1
fi

mkdir -p "$TARGET"
chmod 0700 "$TARGET" 2>/dev/null || true

if find "$TARGET" -mindepth 1 -print -quit | grep -q .; then
  echo "Target directory is not empty: $TARGET" >&2
  echo "Use a new/empty directory so a restore cannot mix with live data." >&2
  exit 1
fi

if [[ -f "${ARCHIVE}.sha256" ]]; then
  (
    cd "$(dirname "$ARCHIVE")"
    sha256sum --check "$(basename "$ARCHIVE").sha256"
  )
fi

tar -xzf "$ARCHIVE" -C "$TARGET" --no-same-owner
chmod -R u+rwX,go-rwx "$TARGET"

if [[ -f "$TARGET/fileaway.db" ]]; then
  integrity="$(sqlite3 "$TARGET/fileaway.db" 'PRAGMA integrity_check;')"
  if [[ "$integrity" != "ok" ]]; then
    echo "Restored database failed integrity check: $integrity" >&2
    exit 1
  fi
fi

if [[ -d "$TARGET/uploads" ]]; then
  upload_count="$(find "$TARGET/uploads" -type f | wc -l)"
else
  upload_count=0
fi

echo "Restore verified in $TARGET"
echo "  database: $([[ -f "$TARGET/fileaway.db" ]] && echo yes || echo no)"
echo "  uploaded files: $upload_count"
echo
cat <<EOF
This is a test restore only; live data was not modified.

To promote it manually (only after taking a fresh backup):
  sudo systemctl stop fileaway
  sudo install -m 0640 -o fileaway -g fileaway "$TARGET/fileaway.db" /var/lib/fileaway/fileaway.db
  sudo rsync -a --delete "$TARGET/uploads/" /var/lib/fileaway/uploads/
  sudo chown -R fileaway:fileaway /var/lib/fileaway
  sudo chmod 0750 /var/lib/fileaway /var/lib/fileaway/uploads
  sudo systemctl start fileaway
  sudo /opt/fileaway/deployment/scripts/healthcheck.sh
EOF
