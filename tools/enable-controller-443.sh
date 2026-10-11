#!/usr/bin/env bash
set -Eeuo pipefail

RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"
UNIT="/etc/systemd/system/tp27-controller-443.service"
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/tp27-controller-443-backup-$TS"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }
command -v socat >/dev/null 2>&1 || { echo "ERROR: socat is not installed" >&2; exit 2; }

if ! systemctl is-active --quiet tp27-panel.service; then
  echo "ERROR: tp27-panel.service is not active" >&2
  exit 3
fi

if ! ss -lnt | awk '{print $4}' | grep -Eq '(^|:)8787$'; then
  echo "ERROR: nothing is listening on TCP 8787" >&2
  exit 4
fi

if ss -lntp | awk '{print $4,$NF}' | grep -Eq '(^|:)443[[:space:]]'; then
  if systemctl is-active --quiet tp27-controller-443.service 2>/dev/null; then
    echo "TP27 controller bridge is already active on TCP 443"
    exit 0
  fi
  echo "ERROR: TCP 443 is already in use by another process; nothing changed." >&2
  ss -lntp | grep -E '(^|:)443[[:space:]]' || true
  exit 5
fi

mkdir -p "$BACKUP"
chmod 700 "$BACKUP"
if [[ -f "$UNIT" ]]; then
  cp -a "$UNIT" "$BACKUP/"
fi

TMP="$(mktemp /tmp/tp27-controller-443.XXXXXX.service)"
trap 'rm -f "$TMP"' EXIT
curl -fsSL "$RAW/systemd/tp27-controller-443.service" -o "$TMP"
install -m 0644 "$TMP" "$UNIT"

systemctl daemon-reload
systemctl enable --now tp27-controller-443.service
sleep 1

if ! systemctl is-active --quiet tp27-controller-443.service; then
  echo "ERROR: tp27-controller-443.service failed" >&2
  systemctl status tp27-controller-443.service --no-pager -l || true
  exit 6
fi

if ! ss -lntp | grep -E '(^|:)443[[:space:]]' >/dev/null 2>&1; then
  echo "ERROR: bridge service is active but TCP 443 is not listening" >&2
  exit 7
fi

echo "============================================================"
echo " TP27 CONTROLLER STANDARD HTTPS READY"
echo " Public:  https://<iran-ip>"
echo " Backend: https://127.0.0.1:8787 (raw TLS passthrough)"
echo " Service: active"
echo " Backup:  $BACKUP"
echo "============================================================"
