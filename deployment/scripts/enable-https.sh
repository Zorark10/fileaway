#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

DOMAIN="${1:-}"
EMAIL="${2:-}"
NGINX_CONF="/etc/nginx/sites-available/fileaway"

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
  echo "Usage: sudo $0 <domain> <letsencrypt-email>" >&2
  echo "Example: sudo $0 example.duckdns.org you@example.com" >&2
  exit 1
fi

if [[ ! -f "$NGINX_CONF" ]]; then
  echo "$NGINX_CONF not found. Run install-backend.sh first." >&2
  exit 1
fi

public_ip="$(curl -fsS --max-time 10 https://api.ipify.org)"
resolved_ip="$(getent ahostsv4 "$DOMAIN" | awk 'NR==1 {print $1}')"

if [[ -z "$resolved_ip" ]]; then
  echo "DNS lookup failed for $DOMAIN." >&2
  exit 1
fi

if [[ "$resolved_ip" != "$public_ip" ]]; then
  echo "DNS mismatch for $DOMAIN." >&2
  echo "  VM public IP: $public_ip" >&2
  echo "  DNS resolves: $resolved_ip" >&2
  echo "Update DuckDNS first (deployment/scripts/update-duckdns.sh)." >&2
  exit 1
fi

sed -i -E "s/server_name .*;/server_name ${DOMAIN};/" "$NGINX_CONF"
ln -sfn "$NGINX_CONF" /etc/nginx/sites-enabled/fileaway
nginx -t
systemctl reload nginx

curl -fsS --max-time 10 "http://${DOMAIN}/health" >/dev/null

certbot --nginx \
  -d "$DOMAIN" \
  --redirect \
  --agree-tos \
  --no-eff-email \
  --non-interactive \
  -m "$EMAIL"

nginx -t
systemctl reload nginx

curl -fsS --max-time 10 "https://${DOMAIN}/health" >/dev/null
certbot renew --dry-run

echo "HTTPS is working for https://${DOMAIN}."
echo "Renewal is handled by the certbot systemd timer."
echo "Ensure PUBLIC_BASE_URL=https://${DOMAIN} in /etc/fileaway/backend.env."
