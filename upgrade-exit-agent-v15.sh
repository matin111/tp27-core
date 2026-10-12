#!/usr/bin/env bash
set -Eeuo pipefail
RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ -f /etc/tp27/exit-agent.json ]] || { echo "ERROR: exit agent is not enrolled"; exit 2; }

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/etc/tp27/backups/agent-v15-$STAMP"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"

for f in \
  /opt/tp27-exit-agent/tp27-exit-agent.py \
  /opt/tp27-exit-agent/tp27-exit-agent-plain.py \
  /etc/systemd/system/tp27-exit-agent.service \
  /etc/systemd/system/tp27-exit-agent-plain.service \
  /usr/local/lib/tp27/tp27-link \
  /etc/systemd/system/tp27-link@.service \
  /usr/local/lib/tp27/tp27-framed-tls.py \
  /etc/systemd/system/tp27-framed@.service; do
  [[ -f "$f" ]] && cp -a "$f" "$BACKUP/"
done

install -d -m 755 /opt/tp27-exit-agent /usr/local/lib/tp27

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

python3 - <<'PY'
from pathlib import Path
import re

p = Path('/usr/local/lib/tp27/tp27-framed-tls.py')
s = p.read_text(encoding='utf-8')

m = re.search(r"RUNTIME_VERSION\s*=\s*['\"]([0-9]+)\.([0-9]+)\.([0-9]+)['\"]", s)
if not m:
    raise SystemExit('ERROR: framed TLS runtime version not found')
version = tuple(int(x) for x in m.groups())
if version < (1, 1, 1):
    raise SystemExit(f'ERROR: framed TLS runtime {version} is older than 1.1.1')

if "'peer', peer_ip + '/32'" not in s:
    raise SystemExit('ERROR: point-to-point TUN fix is missing')

link_pos = s.find("['ip', 'link', 'set', 'dev', iface")
route_pos = s.find("['ip', 'route', 'replace', peer_ip + '/32'")
if link_pos < 0 or route_pos < 0 or link_pos > route_pos:
    raise SystemExit('ERROR: framed runtime must bring TUN up before installing peer route')

print('FRAMED_RUNTIME_VERSION=' + '.'.join(map(str, version)))
print('FRAMED_RUNTIME_1_1_1_PLUS=OK')
print('FRAMED_LINK_UP_BEFORE_ROUTE=OK')
PY

TRANSPORT="$(python3 - <<'PY'
import json
try:
    print(json.load(open('/etc/tp27/exit-agent.json')).get('transport','https'))
except Exception:
    print('https')
PY
)"
SERVICE="tp27-exit-agent.service"
[[ "$TRANSPORT" == "plain-hmac-v1" ]] && SERVICE="tp27-exit-agent-plain.service"

systemctl daemon-reload
systemctl restart "$SERVICE"
sleep 3

systemctl is-active --quiet "$SERVICE" || {
  echo "ERROR: $SERVICE failed"
  systemctl status "$SERVICE" --no-pager -l || true
  exit 3
}

echo "============================================================"
echo " TP27 EXIT AGENT UPGRADED"
echo " Service: $SERVICE"
echo " Agent: v1.5.0"
echo " Runtime: GRE + FOU + Framed TLS/TCP v1.1.1+"
echo " TUN topology: explicit local/32 <-> peer/32"
echo " TUN order: link UP -> peer route"
echo " Backup: $BACKUP"
echo "============================================================"
