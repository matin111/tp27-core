#!/usr/bin/env bash
set -Eeuo pipefail

RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/etc/tp27/backups/fou443-$STAMP"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
command -v curl >/dev/null || { echo "ERROR: curl missing"; exit 2; }

install -d -m 700 "$BACKUP"
install -d -m 755 /usr/local/lib/tp27

if [[ -f /usr/local/lib/tp27/tp27-link ]]; then
  cp -a /usr/local/lib/tp27/tp27-link "$BACKUP/tp27-link.before"
fi
if [[ -f /opt/tp27-exit-agent/tp27-exit-agent.py ]]; then
  cp -a /opt/tp27-exit-agent/tp27-exit-agent.py "$BACKUP/tp27-exit-agent.py.before"
fi

TMP_LINK="$(mktemp /tmp/tp27-link.XXXXXX)"
TMP_AGENT="$(mktemp /tmp/tp27-agent.XXXXXX.py)"
cleanup(){ rm -f "$TMP_LINK" "$TMP_AGENT"; }
trap cleanup EXIT

curl -fsSL "$RAW/bin/tp27-link-mtls" -o "$TMP_LINK"
bash -n "$TMP_LINK"
install -m 755 "$TMP_LINK" /usr/local/lib/tp27/tp27-link

echo "Updated: /usr/local/lib/tp27/tp27-link"

if [[ -d /opt/tp27-exit-agent ]]; then
  curl -fsSL "$RAW/agent/tp27-exit-agent.py" -o "$TMP_AGENT"
  python3 -m py_compile "$TMP_AGENT"
  install -m 700 "$TMP_AGENT" /opt/tp27-exit-agent/tp27-exit-agent.py
  echo "Updated: /opt/tp27-exit-agent/tp27-exit-agent.py"

  if systemctl is-active --quiet tp27-exit-agent-plain.service 2>/dev/null; then
    systemctl restart tp27-exit-agent-plain.service
    echo "Restarted: tp27-exit-agent-plain.service"
  elif systemctl is-active --quiet tp27-exit-agent.service 2>/dev/null; then
    systemctl restart tp27-exit-agent.service
    echo "Restarted: tp27-exit-agent.service"
  fi
fi

echo "Backup: $BACKUP"
echo "FOU443 hotfix installed. Existing TP27 link services were NOT restarted."
