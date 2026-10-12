#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_IP="${1:-}"
SOURCE="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ -x /opt/tp27-panel/venv/bin/python ]] || { echo "ERROR: TP27 panel not installed"; exit 2; }
[[ -f /opt/tp27-panel/app.py ]] || { echo "ERROR: TP27 panel files missing"; exit 2; }

if [[ -n "$EXPECTED_IP" ]]; then
  ip -4 addr show | grep -qw "$EXPECTED_IP" || {
    echo "ERROR: wrong server; expected $EXPECTED_IP"
    exit 3
  }
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/etc/tp27/backups/panel-resilient-$STAMP"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"

for f in \
  /opt/tp27-panel/app.py \
  /opt/tp27-panel/agent_control.py \
  /opt/tp27-panel/provision_gre.py \
  /usr/local/lib/tp27/tp27-framed-tls.py \
  /etc/systemd/system/tp27-framed@.service \
  /usr/local/lib/tp27/tp27-link \
  /etc/systemd/system/tp27-link@.service; do
  [[ -f "$f" ]] && cp -a "$f" "$BACKUP/"
done

fetch() {
  local path="$1" out="$2"
  curl -fsSL --connect-timeout 8 --max-time 45 "$SOURCE/$path" -o "$out"
}

install -d -m 755 /usr/local/lib/tp27
fetch panel/app.py /opt/tp27-panel/app.py
fetch panel/agent_control.py /opt/tp27-panel/agent_control.py
fetch panel/provision_resilient.py /opt/tp27-panel/provision_gre.py
fetch bin/tp27-framed-tls.py /usr/local/lib/tp27/tp27-framed-tls.py
fetch systemd/tp27-framed@.service /etc/systemd/system/tp27-framed@.service
fetch bin/tp27-link-mtls /usr/local/lib/tp27/tp27-link
fetch systemd/tp27-link@.service /etc/systemd/system/tp27-link@.service

chmod 600 /opt/tp27-panel/app.py /opt/tp27-panel/agent_control.py /opt/tp27-panel/provision_gre.py
chmod 755 /usr/local/lib/tp27/tp27-framed-tls.py /usr/local/lib/tp27/tp27-link

/opt/tp27-panel/venv/bin/python -m py_compile \
  /opt/tp27-panel/app.py \
  /opt/tp27-panel/agent_control.py \
  /opt/tp27-panel/provision_gre.py \
  /usr/local/lib/tp27/tp27-framed-tls.py

systemd-analyze verify /etc/systemd/system/tp27-framed@.service >/dev/null
systemctl daemon-reload
systemctl restart tp27-panel.service
sleep 3

systemctl is-active --quiet tp27-panel.service || {
  echo "ERROR: panel failed after upgrade"
  journalctl -u tp27-panel.service -n 80 --no-pager -o cat || true
  exit 4
}

python3 - <<'PY'
from pathlib import Path
import re

builder=Path('/opt/tp27-panel/provision_gre.py').read_text(encoding='utf-8')
app=Path('/opt/tp27-panel/app.py').read_text(encoding='utf-8')
ctl=Path('/opt/tp27-panel/agent_control.py').read_text(encoding='utf-8')
runtime=Path('/usr/local/lib/tp27/tp27-framed-tls.py').read_text(encoding='utf-8')

m = re.search(r"RUNTIME_VERSION\s*=\s*['\"]([0-9]+)\.([0-9]+)\.([0-9]+)['\"]", runtime)
runtime_version = tuple(int(x) for x in m.groups()) if m else (0, 0, 0)
link_pos = runtime.find("['ip', 'link', 'set', 'dev', iface")
route_pos = runtime.find("['ip', 'route', 'replace', peer_ip + '/32'")

checks=[
    ('builder agent v1.5', 'MIN_AGENT_VERSION = (1, 5, 0)' in builder),
    ('builder framed TLS', 'Framed TLS' in builder and 'provision_tls_exit' in builder),
    ('controller TLS action', "'provision_tls_exit'" in ctl),
    ('transport-aware health', "transport == 'framed-tls'" in app and 'tp27-framed@' in app),
    ('framed runtime v1.1.1+', runtime_version >= (1, 1, 1)),
    ('point-to-point TUN fix', "'peer', peer_ip + '/32'" in runtime),
    ('link up before route', link_pos >= 0 and route_pos >= 0 and link_pos < route_pos),
]
bad=[name for name,ok in checks if not ok]
if bad:
    raise SystemExit('ERROR: validation failed: '+', '.join(bad))
print('RESILIENT_BUILDER=OK')
print('CONTROLLER_TLS_ACTION=OK')
print('TRANSPORT_AWARE_HEALTH=OK')
print('FRAMED_RUNTIME_VERSION=' + '.'.join(map(str, runtime_version)))
print('FRAMED_RUNTIME_1_1_1_PLUS=OK')
print('FRAMED_LINK_UP_BEFORE_ROUTE=OK')
PY

echo "============================================================"
echo " TP27 PANEL RESILIENT UPGRADE READY"
echo " Panel: active"
echo " AUTO: Raw GRE -> GRE/UDP FOU -> Framed TLS/TCP"
echo " Controller: provision_tls_exit allowed"
echo " Health/Repair: transport-aware"
echo " Framed runtime: v1.1.1+ point-to-point TUN"
echo " TUN order: link UP -> peer route"
echo " Existing tunnel configs: preserved"
echo " Existing VPN daemons: untouched"
echo " Backup: $BACKUP"
echo "============================================================"
