#!/usr/bin/env bash
set -Eeuo pipefail
CONTROLLER="${1:-}"
TOKEN="${2:-}"
RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"
EXPECTED_IP="${3:-}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ "$CONTROLLER" == http://* ]] || { echo "ERROR: controller must be http:// for plain-hmac transport"; exit 2; }
[[ -n "$TOKEN" ]] || { echo "Usage: $0 http://CONTROLLER:443 ENROLL_TOKEN [EXPECTED_EXIT_IP]"; exit 3; }

if [[ -n "$EXPECTED_IP" ]]; then
  ip -4 addr show | grep -qw "$EXPECTED_IP" || {
    echo "ERROR: wrong server; expected local IPv4 $EXPECTED_IP"
    exit 4
  }
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv ca-certificates curl

CORE_INSTALLER="$(mktemp /tmp/tp27-core-install.XXXXXX.sh)"
trap 'rm -f "$CORE_INSTALLER"' EXIT
curl -fsSL "$RAW/install.sh" -o "$CORE_INSTALLER"
chmod 700 "$CORE_INSTALLER"
bash "$CORE_INSTALLER" exit
rm -f "$CORE_INSTALLER"
trap - EXIT

install -d -m 755 /opt/tp27-exit-agent
install -d -m 700 /etc/tp27/backups /var/log/tp27

STAMP="$(date +%Y%m%d-%H%M%S)"
[[ -f /etc/tp27/exit-agent.json ]] && cp -a /etc/tp27/exit-agent.json "/etc/tp27/backups/exit-agent.$STAMP.json"

python3 -m venv /opt/tp27-exit-agent/venv
/opt/tp27-exit-agent/venv/bin/pip install --upgrade pip >/dev/null
/opt/tp27-exit-agent/venv/bin/pip install requests certifi >/dev/null

curl -fsSL "$RAW/agent/tp27-exit-agent.py" -o /opt/tp27-exit-agent/tp27-exit-agent.py
curl -fsSL "$RAW/agent/tp27-exit-agent-plain.py" -o /opt/tp27-exit-agent/tp27-exit-agent-plain.py
curl -fsSL "$RAW/systemd/tp27-exit-agent-plain.service" -o /etc/systemd/system/tp27-exit-agent-plain.service
chmod 700 /opt/tp27-exit-agent/tp27-exit-agent.py /opt/tp27-exit-agent/tp27-exit-agent-plain.py

/opt/tp27-exit-agent/venv/bin/python -m py_compile \
  /opt/tp27-exit-agent/tp27-exit-agent.py \
  /opt/tp27-exit-agent/tp27-exit-agent-plain.py

curl -fsS --connect-timeout 5 --max-time 10 "$CONTROLLER/health" >/dev/null

systemctl disable --now tp27-exit-agent.service 2>/dev/null || true
systemctl disable --now tp27-exit-agent-plain.service 2>/dev/null || true

/opt/tp27-exit-agent/venv/bin/python \
  /opt/tp27-exit-agent/tp27-exit-agent-plain.py \
  --controller "$CONTROLLER" \
  --enroll "$TOKEN"

systemctl daemon-reload
systemctl enable tp27-exit-agent-plain.service >/dev/null
systemctl restart tp27-exit-agent-plain.service
sleep 2

systemctl is-active --quiet tp27-exit-agent-plain.service || {
  systemctl status tp27-exit-agent-plain.service --no-pager -l || true
  journalctl -u tp27-exit-agent-plain.service -n 80 --no-pager || true
  exit 5
}

echo "============================================================"
echo " TP27 EXIT AGENT READY"
echo " Controller: $CONTROLLER"
echo " Transport: plain-hmac-v1"
echo " Service: tp27-exit-agent-plain.service"
echo "============================================================"
