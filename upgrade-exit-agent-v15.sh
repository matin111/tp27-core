#!/usr/bin/env bash
set -Eeuo pipefail
RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ -f /etc/tp27/exit-agent.json ]] || { echo "ERROR: exit agent is not enrolled"; exit 2; }

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/etc/tp27/backups/agent-multicore-$STAMP"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"

for f in \
  /opt/tp27-exit-agent/tp27-exit-agent.py \
  /opt/tp27-exit-agent/tp27-exit-agent-plain.py \
  /opt/tp27-exit-agent/tp27-exit-agent-multicore.py \
  /opt/tp27-exit-agent/tp27_gost_addon.py \
  /etc/systemd/system/tp27-exit-agent.service \
  /etc/systemd/system/tp27-exit-agent-plain.service \
  /usr/local/lib/tp27/tp27-link \
  /usr/local/lib/tp27/tp27-link-dispatcher \
  /etc/systemd/system/tp27-link@.service \
  /usr/local/lib/tp27/tp27-framed-tls.py \
  /usr/local/lib/tp27/tp27-gost-core.py \
  /usr/local/lib/tp27/tp27-gost-install.sh \
  /etc/systemd/system/tp27-framed@.service; do
  [[ -f "$f" ]] && cp -a "$f" "$BACKUP/"
done

install -d -m 755 /opt/tp27-exit-agent /usr/local/lib/tp27

fetch() {
  local src="$1" dst="$2"
  curl -fsSL --connect-timeout 8 --max-time 90 "$RAW/$src" -o "$dst"
}

fetch agent/tp27-exit-agent.py /opt/tp27-exit-agent/tp27-exit-agent.py
fetch agent/tp27-exit-agent-plain.py /opt/tp27-exit-agent/tp27-exit-agent-plain.py
fetch agent/tp27-exit-agent-multicore.py /opt/tp27-exit-agent/tp27-exit-agent-multicore.py
fetch agent/tp27_gost_addon.py /opt/tp27-exit-agent/tp27_gost_addon.py
fetch systemd/tp27-exit-agent.service /etc/systemd/system/tp27-exit-agent.service
fetch systemd/tp27-exit-agent-plain.service /etc/systemd/system/tp27-exit-agent-plain.service
fetch bin/tp27-link-mtls /usr/local/lib/tp27/tp27-link
fetch bin/tp27-link-dispatcher /usr/local/lib/tp27/tp27-link-dispatcher
fetch systemd/tp27-link@.service /etc/systemd/system/tp27-link@.service
fetch bin/tp27-framed-tls.py /usr/local/lib/tp27/tp27-framed-tls.py
fetch systemd/tp27-framed@.service /etc/systemd/system/tp27-framed@.service
fetch bin/tp27-gost-core.py /usr/local/lib/tp27/tp27-gost-core.py
fetch bin/tp27-gost-install.sh /usr/local/lib/tp27/tp27-gost-install.sh

chmod 700 \
  /opt/tp27-exit-agent/tp27-exit-agent.py \
  /opt/tp27-exit-agent/tp27-exit-agent-plain.py \
  /opt/tp27-exit-agent/tp27-exit-agent-multicore.py \
  /opt/tp27-exit-agent/tp27_gost_addon.py
chmod 755 \
  /usr/local/lib/tp27/tp27-link \
  /usr/local/lib/tp27/tp27-link-dispatcher \
  /usr/local/lib/tp27/tp27-framed-tls.py \
  /usr/local/lib/tp27/tp27-gost-core.py \
  /usr/local/lib/tp27/tp27-gost-install.sh

/opt/tp27-exit-agent/venv/bin/python -m py_compile \
  /opt/tp27-exit-agent/tp27-exit-agent.py \
  /opt/tp27-exit-agent/tp27-exit-agent-plain.py \
  /opt/tp27-exit-agent/tp27-exit-agent-multicore.py \
  /opt/tp27-exit-agent/tp27_gost_addon.py \
  /usr/local/lib/tp27/tp27-framed-tls.py \
  /usr/local/lib/tp27/tp27-gost-core.py

python3 - <<'PY'
from pathlib import Path
import re

s=Path('/usr/local/lib/tp27/tp27-framed-tls.py').read_text(encoding='utf-8')
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

for path in (
    '/opt/tp27-exit-agent/tp27-exit-agent-multicore.py',
    '/opt/tp27-exit-agent/tp27_gost_addon.py',
    '/usr/local/lib/tp27/tp27-link-dispatcher',
    '/usr/local/lib/tp27/tp27-gost-core.py',
    '/usr/local/lib/tp27/tp27-gost-install.sh',
):
    if not Path(path).exists():
        raise SystemExit('ERROR: missing multi-core runtime: ' + path)

unit=Path('/etc/systemd/system/tp27-exit-agent.service').read_text(encoding='utf-8')
if 'tp27-exit-agent-multicore.py' not in unit:
    raise SystemExit('ERROR: standard agent unit is not multi-core aware')
link_unit=Path('/etc/systemd/system/tp27-link@.service').read_text(encoding='utf-8')
if 'tp27-link-dispatcher' not in link_unit:
    raise SystemExit('ERROR: link unit is not using the multi-core dispatcher')
print('FRAMED_RUNTIME_VERSION=' + '.'.join(map(str, version)))
print('MULTICORE_AGENT=OK')
print('GOST_CORE_FILES=OK')
PY

GOST_STATE="deferred"
if /usr/local/lib/tp27/tp27-gost-install.sh; then
  GOST_STATE="ready"
else
  echo "WARNING: GOST v3.3.0 download is currently unavailable; provisioning will retry automatically later." >&2
fi

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
echo " TP27 EXIT AGENT MULTI-CORE READY"
echo " Service: $SERVICE"
echo " Agent base: v1.5.0"
echo " Cores: GRE + FOU + GOST/WSS + Framed TLS"
echo " GOST: v3.3.0 ($GOST_STATE)"
echo " GOST service isolation: tp27-link@<id> via dispatcher"
echo " Existing VPN daemons: untouched"
echo " Backup: $BACKUP"
echo "============================================================"
