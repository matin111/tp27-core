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
fetch panel/provision_resilient.py /opt/tp27-panel/provision_gre.py
fetch bin/tp27-framed-tls.py /usr/local/lib/tp27/tp27-framed-tls.py
fetch systemd/tp27-framed@.service /etc/systemd/system/tp27-framed@.service
fetch bin/tp27-link-mtls /usr/local/lib/tp27/tp27-link
fetch systemd/tp27-link@.service /etc/systemd/system/tp27-link@.service

chmod 600 /opt/tp27-panel/provision_gre.py
chmod 755 /usr/local/lib/tp27/tp27-framed-tls.py /usr/local/lib/tp27/tp27-link

/opt/tp27-panel/venv/bin/python -m py_compile \
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
p=Path('/opt/tp27-panel/provision_gre.py')
s=p.read_text(encoding='utf-8')
checks=['MIN_AGENT_VERSION = (1, 5, 0)', 'Framed TLS', "provision_tls_exit"]
missing=[x for x in checks if x not in s]
if missing:
    raise SystemExit('ERROR: resilient builder validation failed: '+', '.join(missing))
print('RESILIENT_BUILDER=OK')
PY

echo "============================================================"
echo " TP27 PANEL RESILIENT UPGRADE READY"
echo " Panel: active"
echo " AUTO: Raw GRE -> GRE/UDP FOU -> Framed TLS/TCP"
echo " Existing tunnel configs: preserved"
echo " Existing VPN daemons: untouched"
echo " Backup: $BACKUP"
echo "============================================================"
