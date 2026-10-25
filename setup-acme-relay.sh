#!/usr/bin/env bash
set -Eeuo pipefail

IRAN_IP="${1:-}"
PUBLIC_KEY="${2:-}"
USER_NAME="${TP27_ACME_RELAY_USER:-tp27-acme}"
ACME_HOST="${TP27_ACME_HOST:-acme-v02.api.letsencrypt.org}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }
[[ -n "$IRAN_IP" && -n "$PUBLIC_KEY" ]] || {
  echo "Usage: $0 <iran-controller-ip> '<ssh-ed25519 public-key>'" >&2
  exit 2
}

python3 - "$IRAN_IP" <<'PY'
import ipaddress, sys
ipaddress.ip_address(sys.argv[1])
PY
[[ "$PUBLIC_KEY" == ssh-ed25519\ * || "$PUBLIC_KEY" == ssh-rsa\ * || "$PUBLIC_KEY" == ecdsa-*\ * ]] \
  || { echo "ERROR: invalid SSH public key format" >&2; exit 3; }

if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$USER_NAME"
fi
passwd -l "$USER_NAME" >/dev/null 2>&1 || true

HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
[[ -n "$HOME_DIR" ]] || { echo "ERROR: cannot resolve relay user home" >&2; exit 4; }
install -d -o "$USER_NAME" -g "$USER_NAME" -m 700 "$HOME_DIR/.ssh"
AUTH="$HOME_DIR/.ssh/authorized_keys"
touch "$AUTH"
chown "$USER_NAME:$USER_NAME" "$AUTH"
chmod 600 "$AUTH"

STAMP="$(date -u +%Y%m%d-%H%M%S)"
cp -a "$AUTH" "${AUTH}.backup.${STAMP}"

KEY_BODY="$(printf '%s\n' "$PUBLIC_KEY" | awk '{print $1" "$2}')"
TMP="${AUTH}.tmp.$$"
grep -vF "$KEY_BODY" "$AUTH" >"$TMP" || true
printf 'from="%s",no-agent-forwarding,no-X11-forwarding,no-pty,no-user-rc,permitopen="%s:443" %s tp27-acme-relay\n' \
  "$IRAN_IP" "$ACME_HOST" "$KEY_BODY" >>"$TMP"
chown "$USER_NAME:$USER_NAME" "$TMP"
chmod 600 "$TMP"
mv -f "$TMP" "$AUTH"

sshd -t

echo "============================================================"
echo " TP27 ACME RELAY READY"
echo " USER=$USER_NAME"
echo " SOURCE=$IRAN_IP"
echo " PERMITOPEN=$ACME_HOST:443"
echo " AUTHORIZED_KEYS=$AUTH"
echo " NO SUDO GRANTED"
echo " NO TUNNEL MODIFIED"
echo "============================================================"
