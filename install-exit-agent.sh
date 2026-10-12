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
PLAIN=""
[[ -n "$HOST" ]] && STANDARD="https://$HOST"
[[ -n "$HOST" ]] && PLAIN="http://$HOST:443"

probe_controller() {
  local base="$1"
  local extra=()
  [[ "$MODE" == "--insecure" ]] && extra+=(--insecure)
  curl "${extra[@]}" -fsS --connect-timeout 4 --max-time 7 \
    "$base/api/control-key" >/dev/null 2>&1
}

probe_plain() {
  local base="$1"
  [[ -n "$base" ]] || return 1
  curl -fsS --connect-timeout 4 --max-time 7 "$base/health" \
    | grep -q 'plain-hmac-v1'
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
if [[ -n "$PLAIN" ]]; then
  if probe_plain "$PLAIN"; then
    echo "Signed HTTP fallback probe OK: $PLAIN"
  else
    echo "Signed HTTP fallback is not available yet: $PLAIN"
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
install -d -m 700 /etc/tp27 /etc/tp27/backups /var/log/tp27
python3 -m venv /opt/tp27-exit-agent/venv
/opt/tp27-exit-agent/venv/bin/pip install --upgrade pip >/dev/null
/opt/tp27-exit-agent/venv/bin/pip install requests certifi >/dev/null

curl -fsSL "$RAW/agent/tp27-exit-agent.py" -o /opt/tp27-exit-agent/tp27-exit-agent.py
curl -fsSL "$RAW/agent/tp27-exit-agent-plain.py" -o /opt/tp27-exit-agent/tp27-exit-agent-plain.py
curl -fsSL "$RAW/systemd/tp27-exit-agent.service" -o /etc/systemd/system/tp27-exit-agent.service
curl -fsSL "$RAW/systemd/tp27-exit-agent-plain.service" -o /etc/systemd/system/tp27-exit-agent-plain.service
chmod 700 /opt/tp27-exit-agent/tp27-exit-agent.py /opt/tp27-exit-agent/tp27-exit-agent-plain.py

/opt/tp27-exit-agent/venv/bin/python -m py_compile \
  /opt/tp27-exit-agent/tp27-exit-agent.py \
  /opt/tp27-exit-agent/tp27-exit-agent-plain.py

if [[ -f /etc/tp27/exit-agent.json ]]; then
  cp -a /etc/tp27/exit-agent.json "/etc/tp27/backups/exit-agent.$(date +%Y%m%d-%H%M%S).json"
fi

enroll_https() {
  local base="$1"
  local args=(--controller "$base" --enroll "$TOKEN")
  if [[ "$MODE" == "--insecure" ]]; then
    args+=(--insecure)
  fi
  echo "Trying HTTPS enrollment: $base"
  /opt/tp27-exit-agent/venv/bin/python \
    /opt/tp27-exit-agent/tp27-exit-agent.py \
    "${args[@]}"
}

enroll_plain() {
  local base="$1"
  echo "Trying signed HTTP enrollment: $base"
  /opt/tp27-exit-agent/venv/bin/python \
    /opt/tp27-exit-agent/tp27-exit-agent-plain.py \
    --controller "$base" \
    --enroll "$TOKEN"
}

ENROLLED=0
SELECTED=""
TRANSPORT=""
SERVICE=""

if enroll_https "$REQUESTED_CONTROLLER"; then
  ENROLLED=1
  SELECTED="$REQUESTED_CONTROLLER"
  TRANSPORT="https"
  SERVICE="tp27-exit-agent.service"
elif [[ -n "$STANDARD" && "$STANDARD" != "$REQUESTED_CONTROLLER" ]]; then
  echo "Requested controller enrollment failed; retrying standard HTTPS."
  if enroll_https "$STANDARD"; then
    ENROLLED=1
    SELECTED="$STANDARD"
    TRANSPORT="https"
    SERVICE="tp27-exit-agent.service"
  fi
fi

if [[ "$ENROLLED" -ne 1 && -n "$PLAIN" ]]; then
  echo "HTTPS enrollment unavailable; checking signed HTTP fallback."
  if probe_plain "$PLAIN" && enroll_plain "$PLAIN"; then
    ENROLLED=1
    SELECTED="$PLAIN"
    TRANSPORT="plain-hmac-v1"
    SERVICE="tp27-exit-agent-plain.service"
  fi
fi

if [[ "$ENROLLED" -ne 1 ]]; then
  echo "ERROR: enrollment failed on every controller transport tried." >&2
  echo "Tried HTTPS: $REQUESTED_CONTROLLER" >&2
  [[ -n "$STANDARD" && "$STANDARD" != "$REQUESTED_CONTROLLER" ]] && echo "Tried HTTPS: $STANDARD" >&2
  [[ -n "$PLAIN" ]] && echo "Tried signed HTTP: $PLAIN" >&2
  echo "On the Iran controller, install/start install-plain-control.sh when filtered paths break HTTPS." >&2
  exit 6
fi

systemctl daemon-reload
if [[ "$SERVICE" == "tp27-exit-agent.service" ]]; then
  systemctl disable --now tp27-exit-agent-plain.service 2>/dev/null || true
else
  systemctl disable --now tp27-exit-agent.service 2>/dev/null || true
fi
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"
sleep 2

if ! systemctl is-active --quiet "$SERVICE"; then
  echo "ERROR: $SERVICE failed"
  systemctl status "$SERVICE" --no-pager -l || true
  journalctl -u "$SERVICE" -n 80 --no-pager || true
  exit 5
fi

AGENT_VERSION="$(python3 - /opt/tp27-exit-agent/tp27-exit-agent.py <<'PY'
import re,sys
s=open(sys.argv[1],encoding='utf-8').read()
m=re.search(r"^VERSION\s*=\s*['\"]([^'\"]+)",s,re.M)
print(m.group(1) if m else 'unknown')
PY
)"

echo "Selected controller: $SELECTED"
echo "============================================================"
echo " TP27 EXIT AGENT READY"
echo " Controller: $SELECTED"
echo " Transport: $TRANSPORT"
echo " Agent version: $AGENT_VERSION"
echo " Service: $SERVICE"
echo " Config: /etc/tp27/exit-agent.json"
if [[ "$TRANSPORT" == "plain-hmac-v1" ]]; then
  echo " Note: signed fallback is restricted to non-secret GRE lifecycle actions."
fi
echo "============================================================"
