#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/fileaway/duckdns.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy deployment/env/duckdns.env.example there." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

if [[ -z "${DUCKDNS_DOMAIN:-}" || -z "${DUCKDNS_TOKEN:-}" ]]; then
  echo "DUCKDNS_DOMAIN and DUCKDNS_TOKEN must be set in $ENV_FILE." >&2
  exit 1
fi

response="$(curl -fsS --get "https://www.duckdns.org/update" \
  --data-urlencode "domains=${DUCKDNS_DOMAIN}" \
  --data-urlencode "token=${DUCKDNS_TOKEN}" \
  --data-urlencode "verbose=true")"

if [[ "$response" != OK* ]]; then
  echo "DuckDNS update failed." >&2
  exit 1
fi

echo "DuckDNS update succeeded for ${DUCKDNS_DOMAIN}.duckdns.org"
printf '%s\n' "$response" | tail -n +2
