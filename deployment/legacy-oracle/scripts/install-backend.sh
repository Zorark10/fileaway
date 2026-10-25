#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

APP_DIR="${APP_DIR:-/opt/fileaway}"
DATA_DIR="${DATA_DIR:-/var/lib/fileaway}"
ENV_DIR="${ENV_DIR:-/etc/fileaway}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/fileaway}"

if [[ ! -f "$APP_DIR/app/main.py" ]]; then
  echo "Repository not found at $APP_DIR." >&2
  echo "Clone the repository there first, or set APP_DIR." >&2
  exit 1
fi

if [[ ! -f /etc/os-release ]]; then
  echo "Unsupported operating system (missing /etc/os-release)." >&2
  exit 1
fi

. /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "This script targets Ubuntu LTS (24.04 recommended). Detected: ${PRETTY_NAME:-unknown}" >&2
  exit 1
fi

echo "Installing packages..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates \
  certbot \
  curl \
  git \
  nginx \
  python3 \
  python3-certbot-nginx \
  python3-pip \
  python3-venv \
  rsync \
  sqlite3 \
  ufw

if ! id fileaway >/dev/null 2>&1; then
  useradd \
    --system \
    --home-dir "$DATA_DIR" \
    --create-home \
    --shell /usr/sbin/nologin \
    fileaway
fi

install -d -m 0750 -o fileaway -g fileaway "$DATA_DIR"
install -d -m 0750 -o fileaway -g fileaway "$DATA_DIR/uploads"
install -d -m 0750 -o root -g fileaway "$ENV_DIR"
install -d -m 0700 -o root -g root "$BACKUP_DIR"

if [[ ! -f "$ENV_DIR/backend.env" ]]; then
  install -m 0640 -o root -g fileaway \
    "$APP_DIR/deployment/env/backend.env.example" \
    "$ENV_DIR/backend.env"
  echo "Created $ENV_DIR/backend.env from the template. Edit it before starting."
else
  echo "Keeping existing $ENV_DIR/backend.env."
fi

chown -R root:fileaway "$APP_DIR"
chmod -R o-rwx "$APP_DIR"
chmod -R g-w "$APP_DIR"
chmod 0750 "$APP_DIR"/deployment/scripts/*.sh

echo "Creating Python virtual environment..."
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

chown -R root:fileaway "$APP_DIR"
chmod -R o-rwx "$APP_DIR"
chmod -R g-w "$APP_DIR"
chmod 0750 "$APP_DIR"/deployment/scripts/*.sh

install -m 0644 "$APP_DIR/deployment/systemd/fileaway.service" \
  /etc/systemd/system/fileaway.service
install -m 0644 "$APP_DIR/deployment/systemd/fileaway-backup.service" \
  /etc/systemd/system/fileaway-backup.service
install -m 0644 "$APP_DIR/deployment/systemd/fileaway-backup.timer" \
  /etc/systemd/system/fileaway-backup.timer

if [[ "$APP_DIR" != "/opt/fileaway" ]]; then
  sed -i "s|/opt/fileaway|$APP_DIR|g" \
    /etc/systemd/system/fileaway.service \
    /etc/systemd/system/fileaway-backup.service
fi

install -m 0644 "$APP_DIR/deployment/nginx/fileaway.conf" \
  /etc/nginx/sites-available/fileaway

systemctl daemon-reload
systemctl enable fileaway.service
systemctl enable fileaway-backup.timer

cat <<EOF

Backend installation complete.

Manual steps still required:
  1. Edit $ENV_DIR/backend.env (PUBLIC_BASE_URL, CORS_ORIGINS, paths).
  2. Edit /etc/nginx/sites-available/fileaway and set server_name, then:
       sudo ln -s /etc/nginx/sites-available/fileaway /etc/nginx/sites-enabled/fileaway
       sudo nginx -t && sudo systemctl reload nginx
  3. Run: sudo $APP_DIR/deployment/scripts/migrate.sh
  4. Run: sudo systemctl start fileaway
  5. Configure OCI/UFW firewall ports 22, 80, 443 (see deployment/README.md).
  6. Point DuckDNS at the VM public IP and run enable-https.sh.

The service was enabled but not started.
EOF
