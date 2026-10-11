#!/usr/bin/env bash
set -Eeuo pipefail

CONTROLLER="${1:-}"
TOKEN="${2:-}"
MODE="${3:-}"
RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ -n "$CONTROLLER" && -n "$TOKEN" ]] || {
  echo "Usage: $0 <CONTROLLER_URL> <ENROLL_TOKEN> [--insecure]"
  exit 2
}

if [[ "$CONTROLLER" != https://* ]]; then
  echo "ERROR: controller URL must use https://"
  exit 3
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv ca-certificates curl

REQUESTED_CONTROLLER="$CONTROLLER"
HOST="$(python3 - "$CONTROLLER" <<'PY'
from urllib.parse import urlsplit
import sys
u=urlsplit(sys.argv[1])
print(u.hostname or '')
PY
)"
STANDARD=""
[[ -n "$HOST" ]] && STANDARD="https://$HOST"

probe_controller() {
  local base="$1"
  local extra=()
  [[ "$MODE" == "--insecure" ]] && extra+=(--insecure)
  curl "${extra[@]}" -fsS --connect-timeout 4 --max-time 7 \
    "$base/api/control-key" >/dev/null 2>&1
}

echo "===== CONTROLLER CONNECTIVITY (DIAGNOSTIC) ====="
if probe_controller "$REQUESTED_CONTROLLER"; then
  echo "Controller probe OK: $REQUESTED_CONTROLLER"
else
  echo "Requested endpoint did not answer probe: $REQUESTED_CONTROLLER"
fi
if [[ -n "$STANDARD" && "$STANDARD" != "$REQUESTED_CONTROLLER" ]]; then
  if probe_controller "$STANDARD"; then
    echo "Standard HTTPS probe OK: $STANDARD"
  else
    echo "Standard HTTPS did not answer probe yet: $STANDARD"
  fi
fi

CORE_INSTALLER="$(mktemp /tmp/tp27-core-install.XXXXXX.sh)"
trap 'rm -f "$CORE_INSTALLER"' EXIT
curl -fsSL "$RAW/install.sh" -o "$CORE_INSTALLER"
chmod 700 "$CORE_INSTALLER"
bash "$CORE_INSTALLER" exit
rm -f "$CORE_INSTALLER"
trap - EXIT

command -v tp27 >/dev/null || { echo "ERROR: TP27 core install failed"; exit 4; }

install -d -m 755 /opt/tp27-exit-agent
install -d -m 700 /etc/tp27 /var/log/tp27
python3 -m venv /opt/tp27-exit-agent/venv
/opt/tp27-exit-agent/venv/bin/pip install --upgrade pip >/dev/null
/opt/tp27-exit-agent/venv/bin/pip install requests certifi >/dev/null

curl -fsSL "$RAW/agent/tp27-exit-agent.py" -o /opt/tp27-exit-agent/tp27-exit-agent.py
curl -fsSL "$RAW/systemd/tp27-exit-agent.service" -o /etc/systemd/system/tp27-exit-agent.service
chmod 700 /opt/tp27-exit-agent/tp27-exit-agent.py

enroll_once() {
  local base="$1"
  local args=(--controller "$base" --enroll "$TOKEN")
  if [[ "$MODE" == "--insecure" ]]; then
    args+=(--insecure)
  fi
  echo "Trying enrollment: $base"
  /opt/tp27-exit-agent/venv/bin/python \
    /opt/tp27-exit-agent/tp27-exit-agent.py \
    "${args[@]}"
}

ENROLLED=0
SELECTED=""
if enroll_once "$REQUESTED_CONTROLLER"; then
  ENROLLED=1
  SELECTED="$REQUESTED_CONTROLLER"
elif [[ -n "$STANDARD" && "$STANDARD" != "$REQUESTED_CONTROLLER" ]]; then
  echo "Requested controller enrollment failed; retrying standard HTTPS."
  if enroll_once "$STANDARD"; then
    ENROLLED=1
    SELECTED="$STANDARD"
  fi
fi

if [[ "$ENROLLED" -ne 1 ]]; then
  echo "ERROR: enrollment failed on every controller endpoint tried." >&2
  echo "Tried: $REQUESTED_CONTROLLER" >&2
  [[ -n "$STANDARD" && "$STANDARD" != "$REQUESTED_CONTROLLER" ]] && echo "Tried: $STANDARD" >&2
  echo "If TCP 8787 is filtered, enable TP27's standard-HTTPS bridge on the Iran controller and retry." >&2
  exit 6
fi
CONTROLLER="$SELECTED"

echo "Selected controller: $CONTROLLER"

systemctl daemon-reload
systemctl enable tp27-exit-agent.service >/dev/null
systemctl restart tp27-exit-agent.service
sleep 2

if ! systemctl is-active --quiet tp27-exit-agent.service; then
  echo "ERROR: tp27-exit-agent.service failed"
  systemctl status tp27-exit-agent.service --no-pager -l || true
  journalctl -u tp27-exit-agent.service -n 80 --no-pager || true
  exit 5
fi

AGENT_VERSION="$(python3 - /opt/tp27-exit-agent/tp27-exit-agent.py <<'PY'
import re,sys
s=open(sys.argv[1],encoding='utf-8').read()
m=re.search(r"^VERSION\s*=\s*['\"]([^'\"]+)",s,re.M)
print(m.group(1) if m else 'unknown')
PY
)"

echo "============================================================"
echo " TP27 EXIT AGENT READY"
echo " Controller: $CONTROLLER"
echo " Agent version: $AGENT_VERSION"
echo " Core data-plane: AUTO GRE + GRE/UDP FOU + TLS fallback"
echo " Service: active"
echo " Config: /etc/tp27/exit-agent.json"
echo "============================================================"
