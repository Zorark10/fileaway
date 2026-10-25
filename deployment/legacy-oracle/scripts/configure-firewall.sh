#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

SSH_PORT="${SSH_PORT:-22}"
SSH_ALLOW_FROM="${SSH_ALLOW_FROM:-}"

if [[ -z "$SSH_ALLOW_FROM" && -n "${SSH_CLIENT:-}" ]]; then
  SSH_ALLOW_FROM="${SSH_CLIENT%% *}"
fi

if [[ -z "$SSH_ALLOW_FROM" ]]; then
  echo "Set SSH_ALLOW_FROM to your IP or CIDR, for example:" >&2
  echo "  sudo SSH_ALLOW_FROM=203.0.113.10/32 $0" >&2
  exit 1
fi

ufw default deny incoming
ufw default allow outgoing
ufw allow from "$SSH_ALLOW_FROM" to any port "$SSH_PORT" proto tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status verbose

cat <<EOF

UFW is enabled.
SSH allowed only from: ${SSH_ALLOW_FROM} on ${SSH_PORT}/tcp
HTTP/HTTPS allowed from anywhere: 80/tcp, 443/tcp.
Mirror these rules in the OCI VCN Security List.
EOF
