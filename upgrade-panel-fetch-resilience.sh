#!/usr/bin/env bash
set -Eeuo pipefail

RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"
TARGET="/opt/tp27-panel/static/app.js"
EXPECTED_IP="${1:-}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ -f "$TARGET" ]] || { echo "ERROR: TP27 panel app.js not found: $TARGET"; exit 2; }

if [[ -n "$EXPECTED_IP" ]]; then
  if ! ip -4 addr show | grep -qw "$EXPECTED_IP"; then
    echo "ERROR: wrong server; expected local IPv4 $EXPECTED_IP"
    echo "No changes were made."
    exit 3
  fi
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/etc/tp27/backups/panel-fetch-$STAMP"
install -d -m 700 "$BACKUP"
cp -a "$TARGET" "$BACKUP/app.js"

TMP="$(mktemp /tmp/tp27-app.XXXXXX.js)"
trap 'rm -f "$TMP"' EXIT
curl -fsSL "$RAW/panel/static/app.js" -o "$TMP"

grep -q "ACTIVE_JOB_KEY='tp27_active_job'" "$TMP" || {
  echo "ERROR: downloaded app.js does not contain resilient job polling marker"
  exit 4
}
grep -q "ارتباط موقت با پنل قطع شد" "$TMP" || {
  echo "ERROR: downloaded app.js missing retry UI"
  exit 5
}

install -m 644 "$TMP" "$TARGET"

echo "Updated: $TARGET"
echo "Backup: $BACKUP"
echo "Panel service was NOT restarted."
echo "Browser: use Ctrl+F5 once to load the updated JavaScript."
