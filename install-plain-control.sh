#!/usr/bin/env bash
set -Eeuo pipefail
RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"
EXPECTED_IP="${1:-}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ -x /opt/tp27-panel/venv/bin/python ]] || { echo "ERROR: TP27 panel is not installed"; exit 2; }
[[ -f /opt/tp27-panel/agent_control.py ]] || { echo "ERROR: TP27 panel agent controller is missing"; exit 3; }

if [[ -n "$EXPECTED_IP" ]]; then
  ip -4 addr show | grep -qw "$EXPECTED_IP" || {
    echo "ERROR: wrong server; expected local IPv4 $EXPECTED_IP"
    exit 4
  }
fi

if systemctl is-active --quiet tp27-controller-443.service 2>/dev/null; then
  systemctl disable --now tp27-controller-443.service
fi

if ss -lntp | grep -q ':443 '; then
  echo "ERROR: TCP/443 is already in use by another service"
  ss -lntp | grep ':443 ' || true
  exit 5
fi

install -d -m 755 /opt/tp27-panel
install -d -m 700 /etc/tp27/backups
STAMP="$(date +%Y%m%d-%H%M%S)"
[[ -f /opt/tp27-panel/plain_control.py ]] && cp -a /opt/tp27-panel/plain_control.py "/etc/tp27/backups/plain_control.$STAMP.py"
[[ -f /etc/systemd/system/tp27-plain-control.service ]] && cp -a /etc/systemd/system/tp27-plain-control.service "/etc/tp27/backups/tp27-plain-control.$STAMP.service"

curl -fsSL "$RAW/panel/plain_control.py" -o /opt/tp27-panel/plain_control.py
curl -fsSL "$RAW/systemd/tp27-plain-control.service" -o /etc/systemd/system/tp27-plain-control.service
chmod 600 /opt/tp27-panel/plain_control.py

/opt/tp27-panel/venv/bin/python -m py_compile /opt/tp27-panel/plain_control.py

systemctl daemon-reload
systemctl enable tp27-plain-control.service >/dev/null
systemctl restart tp27-plain-control.service
sleep 2

systemctl is-active --quiet tp27-plain-control.service || {
  systemctl status tp27-plain-control.service --no-pager -l || true
  journalctl -u tp27-plain-control.service -n 80 --no-pager || true
  exit 6
}

curl -fsS --connect-timeout 3 --max-time 5 http://127.0.0.1:443/health
printf '\n'

echo "============================================================"
echo " TP27 SIGNED HTTP CONTROL READY"
echo " Listen: TCP/443"
echo " Transport: plain-hmac-v1"
echo " Service: tp27-plain-control.service"
echo " Existing panel HTTPS/8787 remains unchanged"
echo "============================================================"
