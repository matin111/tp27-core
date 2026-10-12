#!/usr/bin/env bash
set -Eeuo pipefail

CONTROLLER="${1:-}"
TOKEN="${2:-}"
MODE="${3:-}"
PLAIN_HINT="${4:-}"
RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ -n "$CONTROLLER" && -n "$TOKEN" ]] || {
  echo "Usage: $0 <CONTROLLER_URL> <ENROLL_TOKEN> [--insecure] [PLAIN_URL]"
  exit 2
}
[[ "$CONTROLLER" == https://* ]] || { echo "ERROR: controller URL must use https://"; exit 3; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv ca-certificates curl iproute2 iputils-ping jq openssl

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

probe_plain() {
  local base="$1"
  [[ -n "$base" ]] || return 1
  curl -fsS --connect-timeout 4 --max-time 7 "$base/health" 2>/dev/null \
    | grep -q 'plain-hmac-v1'
}

plain_candidates=()
add_plain_candidate() {
  local value="$1"
  [[ -n "$value" ]] || return 0
  local existing
  for existing in "${plain_candidates[@]:-}"; do
    [[ "$existing" == "$value" ]] && return 0
  done
  plain_candidates+=("$value")
}

add_plain_candidate "$PLAIN_HINT"
if [[ -n "$HOST" ]]; then
  for p in 443 80 8080 2053 2083 8443 10443; do
    add_plain_candidate "http://$HOST:$p"
  done
fi

REQUESTED_OK=0
STANDARD_OK=0
SELECTED_PLAIN=""

echo "===== CONTROLLER CONNECTIVITY ====="
if probe_controller "$REQUESTED_CONTROLLER"; then
  REQUESTED_OK=1
  echo "HTTPS OK: $REQUESTED_CONTROLLER"
else
  echo "HTTPS unavailable: $REQUESTED_CONTROLLER"
fi
if [[ -n "$STANDARD" && "$STANDARD" != "$REQUESTED_CONTROLLER" ]]; then
  if probe_controller "$STANDARD"; then
    STANDARD_OK=1
    echo "HTTPS OK: $STANDARD"
  else
    echo "HTTPS unavailable: $STANDARD"
  fi
fi
for candidate in "${plain_candidates[@]:-}"; do
  if probe_plain "$candidate"; then
    SELECTED_PLAIN="$candidate"
    echo "Signed control OK: $candidate"
    break
  fi
done

CORE_INSTALLER="$(mktemp /tmp/tp27-core-install.XXXXXX.sh)"
trap 'rm -f "$CORE_INSTALLER"' EXIT
curl -fsSL "$RAW/install.sh" -o "$CORE_INSTALLER"
chmod 700 "$CORE_INSTALLER"
bash "$CORE_INSTALLER" exit
rm -f "$CORE_INSTALLER"
trap - EXIT
command -v tp27 >/dev/null || { echo "ERROR: TP27 core install failed"; exit 4; }

install -d -m 755 /opt/tp27-exit-agent /usr/local/lib/tp27
install -d -m 700 /etc/tp27 /etc/tp27/backups /var/log/tp27
python3 -m venv /opt/tp27-exit-agent/venv
/opt/tp27-exit-agent/venv/bin/pip install --upgrade pip >/dev/null
/opt/tp27-exit-agent/venv/bin/pip install requests certifi >/dev/null

curl -fsSL "$RAW/agent/tp27-exit-agent.py" -o /opt/tp27-exit-agent/tp27-exit-agent.py
curl -fsSL "$RAW/agent/tp27-exit-agent-plain.py" -o /opt/tp27-exit-agent/tp27-exit-agent-plain.py
curl -fsSL "$RAW/systemd/tp27-exit-agent.service" -o /etc/systemd/system/tp27-exit-agent.service
curl -fsSL "$RAW/systemd/tp27-exit-agent-plain.service" -o /etc/systemd/system/tp27-exit-agent-plain.service
curl -fsSL "$RAW/bin/tp27-link-mtls" -o /usr/local/lib/tp27/tp27-link
curl -fsSL "$RAW/systemd/tp27-link@.service" -o /etc/systemd/system/tp27-link@.service
curl -fsSL "$RAW/bin/tp27-framed-tls.py" -o /usr/local/lib/tp27/tp27-framed-tls.py
curl -fsSL "$RAW/systemd/tp27-framed@.service" -o /etc/systemd/system/tp27-framed@.service
chmod 700 /opt/tp27-exit-agent/tp27-exit-agent.py /opt/tp27-exit-agent/tp27-exit-agent-plain.py
chmod 755 /usr/local/lib/tp27/tp27-link /usr/local/lib/tp27/tp27-framed-tls.py

/opt/tp27-exit-agent/venv/bin/python -m py_compile \
  /opt/tp27-exit-agent/tp27-exit-agent.py \
  /opt/tp27-exit-agent/tp27-exit-agent-plain.py \
  /usr/local/lib/tp27/tp27-framed-tls.py

if [[ -f /etc/tp27/exit-agent.json ]]; then
  cp -a /etc/tp27/exit-agent.json "/etc/tp27/backups/exit-agent.$(date +%Y%m%d-%H%M%S).json"
fi

enroll_https() {
  local base="$1"
  local args=(--controller "$base" --enroll "$TOKEN")
  [[ "$MODE" == "--insecure" ]] && args+=(--insecure)
  echo "Trying HTTPS enrollment: $base"
  /opt/tp27-exit-agent/venv/bin/python /opt/tp27-exit-agent/tp27-exit-agent.py "${args[@]}"
}

enroll_plain() {
  local base="$1"
  echo "Trying signed HTTP enrollment: $base"
  /opt/tp27-exit-agent/venv/bin/python /opt/tp27-exit-agent/tp27-exit-agent-plain.py \
    --controller "$base" --enroll "$TOKEN"
}

ENROLLED=0
SELECTED=""
TRANSPORT=""
SERVICE=""

if [[ "$REQUESTED_OK" -eq 1 ]]; then
  if enroll_https "$REQUESTED_CONTROLLER"; then
    ENROLLED=1; SELECTED="$REQUESTED_CONTROLLER"; TRANSPORT="https"; SERVICE="tp27-exit-agent.service"
  fi
fi
if [[ "$ENROLLED" -ne 1 && "$STANDARD_OK" -eq 1 && "$STANDARD" != "$REQUESTED_CONTROLLER" ]]; then
  if enroll_https "$STANDARD"; then
    ENROLLED=1; SELECTED="$STANDARD"; TRANSPORT="https"; SERVICE="tp27-exit-agent.service"
  fi
fi
if [[ "$ENROLLED" -ne 1 && -n "$SELECTED_PLAIN" ]]; then
  if enroll_plain "$SELECTED_PLAIN"; then
    ENROLLED=1; SELECTED="$SELECTED_PLAIN"; TRANSPORT="plain-hmac-v1"; SERVICE="tp27-exit-agent-plain.service"
  fi
fi

# A path may change between probe and enroll. Re-scan signed endpoints once.
if [[ "$ENROLLED" -ne 1 ]]; then
  for candidate in "${plain_candidates[@]:-}"; do
    if probe_plain "$candidate" && enroll_plain "$candidate"; then
      ENROLLED=1; SELECTED="$candidate"; TRANSPORT="plain-hmac-v1"; SERVICE="tp27-exit-agent-plain.service"
      break
    fi
  done
fi

if [[ "$ENROLLED" -ne 1 ]]; then
  echo "ERROR: enrollment failed on every reachable controller transport." >&2
  echo "HTTPS requested: $REQUESTED_CONTROLLER" >&2
  [[ -n "$STANDARD" ]] && echo "HTTPS standard: $STANDARD" >&2
  printf 'Signed candidates: %s\n' "${plain_candidates[*]:-none}" >&2
  echo "On Iran run/install the TP27 signed control service, then retry the same command." >&2
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
echo " Runtime: GRE + FOU + Framed TLS/TCP"
echo "============================================================"
