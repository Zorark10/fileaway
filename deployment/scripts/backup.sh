#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="${DATA_DIR:-/var/lib/fileaway}"
UPLOAD_DIR="${UPLOAD_DIR:-$DATA_DIR/uploads}"
DB_PATH="${DB_PATH:-$DATA_DIR/fileaway.db}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/fileaway}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"

command -v sqlite3 >/dev/null 2>&1 || {
  echo "sqlite3 is required." >&2
  exit 1
}

mkdir -p "$BACKUP_DIR"
chmod 0700 "$BACKUP_DIR" 2>/dev/null || true

if [[ ! -f "$DB_PATH" && ! -d "$UPLOAD_DIR" ]]; then
  echo "Nothing to back up: neither $DB_PATH nor $UPLOAD_DIR exists." >&2
  exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$BACKUP_DIR/fileaway-${timestamp}.tar.gz"
staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT

if [[ -f "$DB_PATH" ]]; then
  sqlite3 "$DB_PATH" ".timeout 30000" ".backup '$staging/fileaway.db'"
  integrity="$(sqlite3 "$staging/fileaway.db" 'PRAGMA integrity_check;')"
  if [[ "$integrity" != "ok" ]]; then
    echo "Backup database integrity check failed: $integrity" >&2
    exit 1
  fi
fi

tar_args=(-czf "$archive" -C "$staging")
if [[ -f "$staging/fileaway.db" ]]; then
  tar_args+=(fileaway.db)
fi
if [[ -d "$UPLOAD_DIR" ]]; then
  tar_args+=(-C "$(dirname "$UPLOAD_DIR")" "$(basename "$UPLOAD_DIR")")
fi

tar_status=0
tar --warning=no-file-changed "${tar_args[@]}" || tar_status=$?
if [[ $tar_status -gt 1 ]]; then
  echo "tar failed with status $tar_status" >&2
  rm -f "$archive"
  exit "$tar_status"
fi

chmod 0600 "$archive"
archive_name="$(basename "$archive")"
(
  cd "$BACKUP_DIR"
  sha256sum "$archive_name" > "${archive_name}.sha256"
)
chmod 0600 "${archive}.sha256"

find "$BACKUP_DIR" -maxdepth 1 -type f \
  \( -name 'fileaway-*.tar.gz' -o -name 'fileaway-*.tar.gz.sha256' \) \
  -mtime "+${RETENTION_DAYS}" -delete

echo "Backup created: $archive"
echo "Checksum: ${archive}.sha256"
echo "Retention: ${RETENTION_DAYS} days (stored locally only)."
