#!/usr/bin/env bash
set -Eeuo pipefail

BASE_ID="${1:-}"
MODE="${2:-}"

if [[ "$EUID" -ne 0 ]]; then
  echo "ERROR: run as root" >&2
  exit 1
fi

if [[ ! "$BASE_ID" =~ ^[A-Za-z0-9_-]{1,24}$ ]]; then
  echo "Usage: $0 <base-id> [--apply]" >&2
  exit 2
fi

EXITS_DIR="/etc/tp27/exits"
CERTS_DIR="/etc/tp27/certs"
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/tp27-legacy-cleanup-${BASE_ID}-${TS}"
REGEX="^${BASE_ID}-[0-9a-fA-F]{6}$"

mapfile -t IDS < <(
  {
    find "$EXITS_DIR" -maxdepth 1 -type f -name "${BASE_ID}-*.json" -printf '%f\n' 2>/dev/null \
      | sed 's/\.json$//'
    systemctl list-units 'tp27-link@*.service' --all --no-legend --plain 2>/dev/null \
      | awk '{print $1}' \
      | sed -n 's/^tp27-link@\(.*\)\.service$/\1/p'
  } | sort -u | grep -E "$REGEX" || true
)

echo "============================================================"
echo " TP27 LEGACY CHILD CLEANUP"
echo " BASE ID: $BASE_ID"
echo " MODE: ${MODE:---dry-run}"
echo "============================================================"

if [[ ${#IDS[@]} -eq 0 ]]; then
  echo "No legacy child IDs found."
  exit 0
fi

printf 'Found legacy child IDs:\n'
printf ' - %s\n' "${IDS[@]}"

if [[ "$MODE" != "--apply" ]]; then
  echo
  echo "Dry run only. Re-run with --apply to remove ONLY the IDs above."
  exit 0
fi

mkdir -p "$BACKUP/exits" "$BACKUP/certs"
chmod 700 "$BACKUP"

for id in "${IDS[@]}"; do
  cfg="$EXITS_DIR/$id.json"
  iface=""

  if [[ -f "$cfg" ]]; then
    cp -a "$cfg" "$BACKUP/exits/"
    iface="$(jq -r '.interface // empty' "$cfg" 2>/dev/null || true)"
  fi

  echo "Stopping tp27-link@$id.service"
  systemctl disable --now "tp27-link@$id.service" >/dev/null 2>&1 || true
  systemctl reset-failed "tp27-link@$id.service" >/dev/null 2>&1 || true

  if [[ -n "$iface" ]]; then
    ip link del "$iface" 2>/dev/null || true
  fi

  rm -f "$cfg"

  for f in "$CERTS_DIR/$id"*; do
    [[ -e "$f" ]] || continue
    cp -a "$f" "$BACKUP/certs/" 2>/dev/null || true
    rm -f "$f"
  done

done

systemctl daemon-reload

echo
echo "===== REMAINING MATCHING UNITS ====="
systemctl list-units 'tp27-link@*.service' --all --no-pager | grep -E "tp27-link@${BASE_ID}-[0-9a-fA-F]{6}\.service" || true

echo
echo "===== REMAINING MATCHING CONFIGS ====="
find "$EXITS_DIR" -maxdepth 1 -type f -name "${BASE_ID}-*.json" -print 2>/dev/null \
  | grep -E "/${BASE_ID}-[0-9a-fA-F]{6}\.json$" || true

echo
echo "===== SOCAT ====="
ps aux | grep '[s]ocat' || true

echo
echo "============================================================"
echo " CLEANUP COMPLETE"
echo " BACKUP: $BACKUP"
echo " Current base tunnel '$BASE_ID' was NOT removed."
echo " Other tunnel IDs were NOT touched."
echo "============================================================"
