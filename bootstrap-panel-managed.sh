#!/usr/bin/env bash
set -Eeuo pipefail

REPO=matin111/tp27-core
RAW=https://raw.githubusercontent.com/matin111/tp27-core
STAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP="/root/tp27-managed-bootstrap-$STAMP"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ -d /opt/tp27-panel && -x /opt/tp27-panel/venv/bin/python ]] || { echo "ERROR: TP27 panel is not installed"; exit 2; }

TARGET="$(curl -fsSL --connect-timeout 10 --max-time 20 \
  -H 'Accept: application/vnd.github+json' \
  -H 'User-Agent: TP27-Bootstrap/1.0' \
  "https://api.github.com/repos/$REPO/commits/main" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("sha", ""))')"
[[ "$TARGET" =~ ^[0-9a-f]{40}$ ]] || { echo "ERROR: could not resolve main SHA"; exit 3; }

mkdir -p "$BACKUP"
chmod 700 "$BACKUP"
printf '%s\n' "$(cat /etc/tp27/panel-version 2>/dev/null || echo unknown)" > "$BACKUP/version.before"

FILES=(opt/tp27-panel usr/local/lib/tp27)
for p in \
  etc/systemd/system/tp27-panel.service \
  etc/systemd/system/tp27-ports-reconcile.service \
  etc/systemd/system/tp27-plain-control.service \
  etc/systemd/system/tp27-benchmark.service \
  etc/systemd/system/tp27-link@.service \
  etc/systemd/system/tp27-framed@.service; do
  [[ -e "/$p" ]] && FILES+=("$p")
done

echo "============================================================"
echo " TP27 MANAGED UPDATE BOOTSTRAP"
echo " TARGET: $TARGET"
echo " BACKUP: $BACKUP"
echo "============================================================"

tar -C / -czf "$BACKUP/system.tar.gz" --exclude='opt/tp27-panel/venv' "${FILES[@]}"

INSTALLER="$(mktemp /tmp/tp27-managed-bootstrap.XXXXXX.sh)"
trap 'rm -f "$INSTALLER"' EXIT
curl -fsSL --connect-timeout 10 --max-time 90 "$RAW/$TARGET/install-panel-managed.sh" -o "$INSTALLER"
chmod 700 "$INSTALLER"

set +e
TP27_REPO_RAW="$RAW/$TARGET" TP27_INSTALL_COMMIT="$TARGET" bash "$INSTALLER"
RC=$?
set -e

if [[ "$RC" -eq 0 ]] \
  && systemctl is-active --quiet tp27-panel.service \
  && curl -kfsS --connect-timeout 2 --max-time 5 https://127.0.0.1:8787/login >/dev/null; then
  echo "BOOTSTRAP=SUCCESS"
  echo "PANEL_VERSION=$TARGET"
  echo "DATA_PLANES=UNTOUCHED"
  exit 0
fi

echo "BOOTSTRAP=FAILED"
echo "ROLLBACK=START"
tar -xzf "$BACKUP/system.tar.gz" -C /
if [[ -s "$BACKUP/version.before" ]] && grep -Eq '^[0-9a-f]{40}$' "$BACKUP/version.before"; then
  cp -a "$BACKUP/version.before" /etc/tp27/panel-version
else
  rm -f /etc/tp27/panel-version
fi
systemctl daemon-reload
systemctl restart tp27-panel.service || true
systemctl restart tp27-ports-reconcile.service 2>/dev/null || true
systemctl restart tp27-plain-control.service 2>/dev/null || true
systemctl restart tp27-benchmark.service 2>/dev/null || true
sleep 2
if systemctl is-active --quiet tp27-panel.service; then
  echo "ROLLBACK=SUCCESS"
else
  echo "ROLLBACK=FAILED"
fi
exit 1
