#!/usr/bin/env bash
set -Eeuo pipefail

RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"
TARGET="${TP27_INSTALL_COMMIT:-}"
PANEL=/opt/tp27-panel
STATIC="$PANEL/static"
UPDATER=/usr/local/lib/tp27/tp27-panel-self-update

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ -d "$PANEL" && -x "$PANEL/venv/bin/python" ]] || { echo "ERROR: TP27 panel is not installed"; exit 2; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates iperf3 util-linux >/dev/null

install -d -m 755 "$PANEL" "$STATIC" /usr/local/lib/tp27
install -d -m 700 /etc/tp27 /var/lib/tp27-panel-updater /var/backups/tp27-panel /var/log/tp27/panel-jobs

fetch() {
  local src="$1" dst="$2"
  curl -fsSL --connect-timeout 10 --max-time 90 "$RAW/$src" -o "$dst"
}

echo "===== TP27 MANAGED PANEL UPDATE ====="
echo "SOURCE=$RAW"
echo "TARGET=${TARGET:-main}"

fetch panel/requirements.txt "$PANEL/requirements.txt"
"$PANEL/venv/bin/pip" install -r "$PANEL/requirements.txt" >/dev/null

for f in app.py auth.py provision.py provision_password.py provision_agent.py agent_control.py ports.py portplan.py plain_control.py self_update.py; do
  fetch "panel/$f" "$PANEL/$f"
done
fetch panel/provision_resilient.py "$PANEL/provision_resilient.py"
fetch panel/provision_multicore.py "$PANEL/provision_gre.py"

for f in index.html login.html style.css app.js fast-ui.js agent-manage.js premium.css premium-ui.js runtime-refresh-fix.js benchmark-ui.js benchmark-auto.js self-update.js; do
  fetch "panel/static/$f" "$STATIC/$f"
done

fetch systemd/tp27-panel.service /etc/systemd/system/tp27-panel.service
fetch systemd/tp27-ports-reconcile.service /etc/systemd/system/tp27-ports-reconcile.service
fetch systemd/tp27-plain-control.service /etc/systemd/system/tp27-plain-control.service
fetch systemd/tp27-benchmark.service /etc/systemd/system/tp27-benchmark.service

fetch bin/tp27-link-mtls /usr/local/lib/tp27/tp27-link
fetch bin/tp27-link-dispatcher /usr/local/lib/tp27/tp27-link-dispatcher
fetch bin/tp27-link-preflight /usr/local/lib/tp27/tp27-link-preflight
fetch bin/tp27-openvpn-core /usr/local/lib/tp27/tp27-openvpn-core
fetch bin/tp27-framed-tls.py /usr/local/lib/tp27/tp27-framed-tls.py
fetch bin/tp27-gost-core.py /usr/local/lib/tp27/tp27-gost-core.py
fetch bin/tp27-gost-install.sh /usr/local/lib/tp27/tp27-gost-install.sh
fetch bin/tp27-panel-self-update "$UPDATER"

chmod 700 "$UPDATER"
chmod 755 \
  /usr/local/lib/tp27/tp27-link \
  /usr/local/lib/tp27/tp27-link-dispatcher \
  /usr/local/lib/tp27/tp27-link-preflight \
  /usr/local/lib/tp27/tp27-openvpn-core \
  /usr/local/lib/tp27/tp27-framed-tls.py \
  /usr/local/lib/tp27/tp27-gost-core.py \
  /usr/local/lib/tp27/tp27-gost-install.sh
chmod 600 "$PANEL"/*.py

python3 - "$PANEL/app.py" <<'PY'
from pathlib import Path
p=Path('/opt/tp27-panel/app.py')
s=p.read_text(encoding='utf-8')
marker='# TP27_SELF_UPDATE_ROUTER_V1'
if marker not in s:
    s=s.rstrip()+"\n\n"+marker+"\nfrom self_update import router as self_update_router\napp.include_router(self_update_router)\n"
    p.write_text(s,encoding='utf-8')
PY

"$PANEL/venv/bin/python" -m py_compile \
  "$PANEL/app.py" "$PANEL/auth.py" "$PANEL/provision.py" \
  "$PANEL/provision_password.py" "$PANEL/provision_agent.py" \
  "$PANEL/provision_resilient.py" "$PANEL/provision_gre.py" \
  "$PANEL/agent_control.py" "$PANEL/plain_control.py" \
  "$PANEL/ports.py" "$PANEL/portplan.py" "$PANEL/self_update.py" \
  /usr/local/lib/tp27/tp27-link-preflight \
  /usr/local/lib/tp27/tp27-framed-tls.py \
  /usr/local/lib/tp27/tp27-gost-core.py

if [[ "$TARGET" =~ ^[0-9a-f]{40}$ ]]; then
  printf '%s\n' "$TARGET" >/etc/tp27/panel-version
  chmod 600 /etc/tp27/panel-version
fi

systemctl daemon-reload
systemctl enable tp27-panel.service >/dev/null 2>&1 || true
systemctl enable tp27-ports-reconcile.service >/dev/null 2>&1 || true
systemctl enable tp27-benchmark.service >/dev/null 2>&1 || true

systemctl restart tp27-panel.service
systemctl restart tp27-ports-reconcile.service || true
systemctl restart tp27-benchmark.service || true

# Control-plane Agent connections may use this service. Restart only if it already exists/enabled.
if systemctl cat tp27-plain-control.service >/dev/null 2>&1; then
  systemctl restart tp27-plain-control.service || true
fi

sleep 2
systemctl is-active --quiet tp27-panel.service || { systemctl status tp27-panel.service --no-pager -l; exit 3; }
curl -kfsS --connect-timeout 2 --max-time 5 https://127.0.0.1:8787/login >/dev/null

echo "PANEL=active"
echo "BENCHMARK=$(systemctl is-active tp27-benchmark.service 2>/dev/null || true)"
echo "VERSION=$(cat /etc/tp27/panel-version 2>/dev/null || echo unknown)"
echo "DATA_PLANES=untouched"
echo "===== TP27 MANAGED UPDATE COMPLETE ====="
