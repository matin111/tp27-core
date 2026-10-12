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
BACKUP="/etc/tp27/backups/panel-multicore-$STAMP"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"

for f in \
  /opt/tp27-panel/app.py \
  /opt/tp27-panel/agent_control.py \
  /opt/tp27-panel/provision_gre.py \
  /opt/tp27-panel/provision_resilient.py \
  /usr/local/lib/tp27/tp27-framed-tls.py \
  /usr/local/lib/tp27/tp27-gost-core.py \
  /usr/local/lib/tp27/tp27-gost-install.sh \
  /usr/local/lib/tp27/tp27-link-dispatcher \
  /etc/systemd/system/tp27-framed@.service \
  /usr/local/lib/tp27/tp27-link \
  /etc/systemd/system/tp27-link@.service; do
  [[ -f "$f" ]] && cp -a "$f" "$BACKUP/"
done

fetch() {
  local path="$1" out="$2"
  curl -fsSL --connect-timeout 8 --max-time 90 "$SOURCE/$path" -o "$out"
}

install -d -m 755 /usr/local/lib/tp27
fetch panel/app.py /opt/tp27-panel/app.py
fetch panel/agent_control.py /opt/tp27-panel/agent_control.py
fetch panel/provision_resilient.py /opt/tp27-panel/provision_resilient.py
fetch panel/provision_multicore.py /opt/tp27-panel/provision_gre.py
fetch bin/tp27-framed-tls.py /usr/local/lib/tp27/tp27-framed-tls.py
fetch systemd/tp27-framed@.service /etc/systemd/system/tp27-framed@.service
fetch bin/tp27-link-mtls /usr/local/lib/tp27/tp27-link
fetch bin/tp27-link-dispatcher /usr/local/lib/tp27/tp27-link-dispatcher
fetch systemd/tp27-link@.service /etc/systemd/system/tp27-link@.service
fetch bin/tp27-gost-core.py /usr/local/lib/tp27/tp27-gost-core.py
fetch bin/tp27-gost-install.sh /usr/local/lib/tp27/tp27-gost-install.sh

chmod 600 \
  /opt/tp27-panel/app.py \
  /opt/tp27-panel/agent_control.py \
  /opt/tp27-panel/provision_gre.py \
  /opt/tp27-panel/provision_resilient.py
chmod 755 \
  /usr/local/lib/tp27/tp27-framed-tls.py \
  /usr/local/lib/tp27/tp27-link \
  /usr/local/lib/tp27/tp27-link-dispatcher \
  /usr/local/lib/tp27/tp27-gost-core.py \
  /usr/local/lib/tp27/tp27-gost-install.sh

/opt/tp27-panel/venv/bin/python -m py_compile \
  /opt/tp27-panel/app.py \
  /opt/tp27-panel/agent_control.py \
  /opt/tp27-panel/provision_resilient.py \
  /opt/tp27-panel/provision_gre.py \
  /usr/local/lib/tp27/tp27-framed-tls.py \
  /usr/local/lib/tp27/tp27-gost-core.py

systemd-analyze verify /etc/systemd/system/tp27-framed@.service >/dev/null
systemd-analyze verify /etc/systemd/system/tp27-link@.service >/dev/null
systemctl daemon-reload

GOST_STATE="deferred"
if /usr/local/lib/tp27/tp27-gost-install.sh; then
  GOST_STATE="ready"
else
  echo "WARNING: GOST v3.3.0 download is currently unavailable; builder will retry automatically when GOST is selected." >&2
fi

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
legacy=Path('/opt/tp27-panel/provision_resilient.py').read_text(encoding='utf-8')
app=Path('/opt/tp27-panel/app.py').read_text(encoding='utf-8')
runtime=Path('/usr/local/lib/tp27/tp27-framed-tls.py').read_text(encoding='utf-8')
dispatcher=Path('/usr/local/lib/tp27/tp27-link-dispatcher').read_text(encoding='utf-8')
link_unit=Path('/etc/systemd/system/tp27-link@.service').read_text(encoding='utf-8')

m = re.search(r"RUNTIME_VERSION\s*=\s*['\"]([0-9]+)\.([0-9]+)\.([0-9]+)['\"]", runtime)
runtime_version = tuple(int(x) for x in m.groups()) if m else (0, 0, 0)
checks=[
    ('legacy resilient builder', 'MIN_AGENT_VERSION = (1, 5, 0)' in legacy and 'provision_tls_exit' in legacy),
    ('multi-core wrapper', 'provision_gost_exit' in builder and 'GOST_PORT_CANDIDATES' in builder),
    ('controller action registration', "ALLOWED_ACTIONS.add('provision_gost_exit')" in builder),
    ('GOST stage before framed fallback', builder.find('مرحله 3: سوییچ خودکار به GOST Core') >= 0 and builder.find('مرحله 4: GOST Core سالم نشد') >= 0),
    ('transport-aware health', "transport == 'framed-tls'" in app and 'tp27-framed@' in app),
    ('framed runtime v1.1.1+', runtime_version >= (1, 1, 1)),
    ('GOST dispatcher', 'gost-wss' in dispatcher and 'tp27-gost-core.py' in dispatcher),
    ('link unit dispatcher', 'tp27-link-dispatcher' in link_unit),
    ('GOST core runtime', Path('/usr/local/lib/tp27/tp27-gost-core.py').exists()),
]
bad=[name for name,ok in checks if not ok]
if bad:
    raise SystemExit('ERROR: validation failed: '+', '.join(bad))
print('RESILIENT_GRE_FOU=OK')
print('MULTICORE_BUILDER=OK')
print('CONTROLLER_GOST_ACTION=OK')
print('GOST_DISPATCHER=OK')
print('FRAMED_RUNTIME_VERSION=' + '.'.join(map(str, runtime_version)))
PY

echo "============================================================"
echo " TP27 PANEL MULTI-CORE READY"
echo " Panel: active"
echo " AUTO CORE: Raw GRE -> GRE/UDP FOU -> GOST/WSS -> Framed TLS"
echo " GOST: v3.3.0 ($GOST_STATE)"
echo " GOST auth: per-tunnel Relay credentials + pinned TLS certificate"
echo " Health/Repair: tp27-link dispatcher aware"
echo " Existing tunnel configs: preserved"
echo " Existing VPN daemons: untouched"
echo " Backup: $BACKUP"
echo "============================================================"
