#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-}"

fail() {
  echo "FAILED: $*" >&2
  echo
  echo "Recent backend logs:"
  journalctl -u fileaway -n 30 --no-pager || true
  exit 1
}

systemctl is-active --quiet fileaway || fail "fileaway.service is not active"
systemctl is-enabled --quiet nginx || fail "nginx.service is not enabled"
systemctl is-active --quiet nginx || fail "nginx.service is not active"
nginx -t >/dev/null 2>&1 || fail "nginx configuration test failed"

health="$(curl -fsS --max-time 5 http://127.0.0.1:8000/health)" \
  || fail "backend health endpoint is not reachable"
if [[ "$health" != *'"status":"ok"'* ]]; then
  fail "unexpected health response: $health"
fi

if [[ -n "$DOMAIN" ]]; then
  remote_health="$(curl -fsS --max-time 10 "https://${DOMAIN}/health")" \
    || fail "public HTTPS health check failed for $DOMAIN"
  if [[ "$remote_health" != *'"status":"ok"'* ]]; then
    fail "unexpected public health response: $remote_health"
  fi
fi

echo "OK: fileaway.service, nginx, database, and local health endpoint are up."
if [[ -n "$DOMAIN" ]]; then
  echo "OK: https://${DOMAIN}/health is reachable."
fi
df -h /var/lib/fileaway | tail -n 1
