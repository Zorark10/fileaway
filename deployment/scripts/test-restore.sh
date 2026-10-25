#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

DATA_DIR="$WORK_DIR/data"
BACKUP_DIR="$WORK_DIR/backups"
RESTORE_DIR="$WORK_DIR/restored"
mkdir -p "$DATA_DIR/uploads"

sqlite3 "$DATA_DIR/fileaway.db" <<'SQL'
CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    original_name TEXT NOT NULL,
    share_token TEXT NOT NULL
);
INSERT INTO files (id, original_name, share_token)
VALUES (1, 'restore-test.txt', 'restore-test-token');
SQL

printf 'restore fixture\n' > "$DATA_DIR/uploads/restore-fixture"

DATA_DIR="$DATA_DIR" \
UPLOAD_DIR="$DATA_DIR/uploads" \
DB_PATH="$DATA_DIR/fileaway.db" \
BACKUP_DIR="$BACKUP_DIR" \
"$SCRIPT_DIR/backup.sh"

archive="$(find "$BACKUP_DIR" -name 'fileaway-*.tar.gz' -print -quit)"
"$SCRIPT_DIR/restore.sh" "$archive" "$RESTORE_DIR"

count="$(sqlite3 "$RESTORE_DIR/fileaway.db" \
  "SELECT COUNT(*) FROM files WHERE share_token = 'restore-test-token';")"
if [[ "$count" != "1" ]]; then
  echo "Restored database content mismatch." >&2
  exit 1
fi

if ! cmp -s "$DATA_DIR/uploads/restore-fixture" \
  "$RESTORE_DIR/uploads/restore-fixture"; then
  echo "Restored upload content mismatch." >&2
  exit 1
fi

echo "Backup/restore test passed in a separate temporary location."
