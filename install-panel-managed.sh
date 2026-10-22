#!/usr/bin/env bash
set -Eeuo pipefail

RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"
TARGET="${TP27_INSTALL_COMMIT:-}"
PANEL=/opt/tp27-panel
STATIC="$PANEL/static"
UPDATER=/usr/local/lib/tp27/tp27-panel-self-update

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ -d "$PANEL" && -x "$PANEL/venv/bin/python" ]] || { echo "ERROR: TP27 panel is not installed"; exit 2; }

DATA_PLANES_BEFORE="$(systemctl list-units 'tp27-link@*.service' 'tp27-framed@*.service' --type=service --state=running --no-legend --no-pager 2>/dev/null | awk '{print $1}' | sort -u)"

wait_for_apt() {
  local waited=0 max_wait="${TP27_APT_WAIT_SECONDS:-900}" busy=0
  while (( waited < max_wait )); do
    busy=0
    if command -v fuser >/dev/null 2>&1; then
      if fuser /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock /var/cache/apt/archives/lock >/dev/null 2>&1; then busy=1; fi
    elif pgrep -x apt >/dev/null 2>&1 || pgrep -x apt-get >/dev/null 2>&1 || pgrep -x dpkg >/dev/null 2>&1 || pgrep -f '[u]nattended-upgrade' >/dev/null 2>&1; then
      busy=1
    fi
    (( busy == 0 )) && return 0
    (( waited == 0 )) && echo "APT/DPKG busy; waiting safely..."
    sleep 5
    waited=$((waited+5))
  done
  echo "ERROR: apt/dpkg remained busy for ${max_wait}s" >&2
  return 1
}

export DEBIAN_FRONTEND=noninteractive
wait_for_apt
apt-get update -qq
wait_for_apt
apt-get install -y -qq curl ca-certificates iperf3 util-linux >/dev/null

install -d -m 755 "$PANEL" "$STATIC" /usr/local/lib/tp27
install -d -m 700 /etc/tp27 /var/lib/tp27 /var/lib/tp27-panel-updater /var/backups/tp27-panel /var/log/tp27/panel-jobs

fetch() {
  local src="$1" dst="$2" tmp
  tmp="${dst}.tp27-download.$$"
  rm -f "$tmp"
  echo "FETCH $src"
  if ! curl -fL \
      --retry 6 \
      --retry-all-errors \
      --retry-delay 2 \
      --connect-timeout 20 \
      --max-time 180 \
      --speed-time 30 \
      --speed-limit 1024 \
      "$RAW/$src" -o "$tmp"; then
    rm -f "$tmp"
    echo "ERROR: download failed after retries: $src" >&2
    return 1
  fi
  if [[ ! -s "$tmp" ]]; then
    rm -f "$tmp"
    echo "ERROR: empty download: $src" >&2
    return 1
  fi
  mv -f "$tmp" "$dst"
}

echo "===== TP27 MANAGED PANEL UPDATE ====="
echo "SOURCE=$RAW"
echo "TARGET=${TARGET:-main}"

fetch panel/requirements.txt "$PANEL/requirements.txt"
"$PANEL/venv/bin/pip" install -r "$PANEL/requirements.txt" >/dev/null

for f in app.py auth.py provision.py provision_password.py provision_agent.py agent_control.py ports.py portplan.py carrier_check.py plain_control.py self_update.py safe_switch.py safe_switch_router.py health_v2.py failover_v2.py; do
  fetch "panel/$f" "$PANEL/$f"
done
fetch panel/provision_resilient.py "$PANEL/provision_resilient.py"
fetch panel/provision_multicore.py "$PANEL/provision_gre.py"

for f in index.html login.html style.css first-paint.css app.js fast-ui.js agent-manage.js premium.css v9.css premium-ui.js runtime-refresh-fix.js benchmark-ui.js benchmark-auto.js server-speed-ui.js speed-recovery-fix.js safe-switch-ui.js self-update.js ui-stability.js; do
  fetch "panel/static/$f" "$STATIC/$f"
done

python3 - "$STATIC/index.html" <<'PY'
from pathlib import Path
p=Path('/opt/tp27-panel/static/index.html')
s=p.read_text(encoding='utf-8')
boot='<link rel="stylesheet" href="/static/first-paint.css">'
style='<link rel="stylesheet" href="/static/style.css">'
safe='<script src="/static/safe-switch-ui.js"></script>'
self_update='<script src="/static/self-update.js"></script>'
stability='<script src="/static/ui-stability.js"></script>'
if boot not in s:
    s=s.replace(style, style+'\n  '+boot) if style in s else s.replace('</head>', '  '+boot+'\n</head>')
if safe not in s:
    s=s.replace(self_update, safe+'\n'+self_update) if self_update in s else s.replace('</body>', safe+'\n</body>')
if stability not in s:
    s=s.replace('</body>', stability+'\n</body>')
p.write_text(s,encoding='utf-8')
PY

# Keep the live tp27-panel.service untouched so a trusted IP certificate path
# already patched into ExecStart can never be replaced by the repository default.
fetch systemd/tp27-ports-reconcile.service /etc/systemd/system/tp27-ports-reconcile.service
fetch systemd/tp27-plain-control.service /etc/systemd/system/tp27-plain-control.service
fetch systemd/tp27-benchmark.service /etc/systemd/system/tp27-benchmark.service
fetch systemd/tp27-failover-v2.service /etc/systemd/system/tp27-failover-v2.service
fetch systemd/tp27-link@.service /etc/systemd/system/tp27-link@.service
fetch systemd/tp27-framed@.service /etc/systemd/system/tp27-framed@.service

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

# A fresh installation ships Failover V2 disabled. Managed updates must preserve
# an existing operator-owned runtime configuration exactly as-is.
export TP27_FAILOVER_CONFIG_CREATED=0
if [[ ! -f /etc/tp27/failover-v2.json ]]; then
  cat >/etc/tp27/failover-v2.json <<'JSON'
{
  "enabled": false,
  "interval_seconds": 5,
  "failure_threshold": 4,
  "cooldown_seconds": 300,
  "tunnels": []
}
JSON
  chmod 600 /etc/tp27/failover-v2.json
  TP27_FAILOVER_CONFIG_CREATED=1
fi

FAILOVER_ENABLED="$(python3 - <<'PY'
import json
from pathlib import Path
p=Path('/etc/tp27/failover-v2.json')
obj=json.loads(p.read_text(encoding='utf-8'))
value=obj.get('enabled')
if not isinstance(value, bool):
    raise SystemExit('Failover V2 enabled must be boolean')
print('true' if value else 'false')
PY
)"

python3 - "$PANEL/app.py" <<'PY'
from pathlib import Path
p=Path('/opt/tp27-panel/app.py')
s=p.read_text(encoding='utf-8')
marker='# TP27_SELF_UPDATE_ROUTER_V1'
if marker not in s:
    s=s.rstrip()+"\n\n"+marker+"\nfrom self_update import router as self_update_router\napp.include_router(self_update_router)\n"
switch_marker='# TP27_SAFE_SWITCH_ROUTER_V1'
if switch_marker not in s:
    s=s.rstrip()+"\n\n"+switch_marker+"\nfrom safe_switch_router import router as safe_switch_router\napp.include_router(safe_switch_router)\n"
p.write_text(s,encoding='utf-8')
PY

"$PANEL/venv/bin/python" -m py_compile \
  "$PANEL/app.py" "$PANEL/auth.py" "$PANEL/provision.py" \
  "$PANEL/provision_password.py" "$PANEL/provision_agent.py" \
  "$PANEL/provision_resilient.py" "$PANEL/provision_gre.py" \
  "$PANEL/agent_control.py" "$PANEL/plain_control.py" \
  "$PANEL/ports.py" "$PANEL/portplan.py" "$PANEL/carrier_check.py" "$PANEL/self_update.py" \
  "$PANEL/safe_switch.py" "$PANEL/safe_switch_router.py" \
  "$PANEL/health_v2.py" "$PANEL/failover_v2.py" \
  /usr/local/lib/tp27/tp27-link-preflight \
  /usr/local/lib/tp27/tp27-framed-tls.py \
  /usr/local/lib/tp27/tp27-gost-core.py

if command -v node >/dev/null 2>&1; then
  for js in \
    app.js fast-ui.js agent-manage.js premium-ui.js runtime-refresh-fix.js \
    benchmark-ui.js benchmark-auto.js server-speed-ui.js speed-recovery-fix.js \
    safe-switch-ui.js self-update.js ui-stability.js; do
    node --check "$STATIC/$js" >/dev/null
  done
fi

if [[ "$TARGET" =~ ^[0-9a-f]{40}$ ]]; then
  printf '%s\n' "$TARGET" >/etc/tp27/panel-version
  chmod 600 /etc/tp27/panel-version
fi

systemctl daemon-reload
systemctl enable tp27-panel.service >/dev/null 2>&1 || true
systemctl enable tp27-ports-reconcile.service >/dev/null 2>&1 || true
systemctl enable tp27-benchmark.service >/dev/null 2>&1 || true
systemctl enable tp27-failover-v2.service >/dev/null 2>&1 || true

# No tp27-link@ or tp27-framed@ service is restarted here.
systemctl restart tp27-panel.service
systemctl restart tp27-ports-reconcile.service || true
systemctl restart tp27-benchmark.service || true
# Do not restart an already-enabled Failover V2 controller during a managed
# panel update: it could immediately evaluate/switch a tunnel and violate the
# update transaction's data-plane preservation guarantee. Disabled/new configs
# are safe to refresh immediately; enabled configs are deliberately deferred.
if [[ "$FAILOVER_ENABLED" == "false" ]]; then
  systemctl restart tp27-failover-v2.service || true
  FAILOVER_RESTART_STATE=performed-disabled
else
  FAILOVER_RESTART_STATE=deferred-enabled
  echo "FAILOVER_V2_RESTART=deferred-enabled"
fi

# Agent control reconnects automatically; a short control-plane restart does not touch data planes.
if systemctl cat tp27-plain-control.service >/dev/null 2>&1; then
  systemctl restart tp27-plain-control.service || true
fi

sleep 2
systemctl is-active --quiet tp27-panel.service || { systemctl status tp27-panel.service --no-pager -l; exit 3; }
curl -kfsS --connect-timeout 2 --max-time 5 https://127.0.0.1:8787/login >/dev/null

# Full bootstrap smoke: authenticated API + required dashboard/static contracts.
"$PANEL/venv/bin/python" - <<'PY'
import hashlib
import hmac
import json
import os
import ssl
import time
import urllib.request
from pathlib import Path

BASE='https://127.0.0.1:8787'
ctx=ssl._create_unverified_context()
cfg=json.loads(Path('/etc/tp27/panel.json').read_text(encoding='utf-8'))
exp=str(int(time.time())+300)
sig=hmac.new(bytes.fromhex(cfg['session_secret']),exp.encode(),hashlib.sha256).hexdigest()
cookie=f'tp27_session={exp}.{sig}'


def get(path, auth=False):
    headers={'User-Agent':'TP27-Installer-Smoke/1.0'}
    if auth:
        headers['Cookie']=cookie
    req=urllib.request.Request(BASE+path,headers=headers)
    with urllib.request.urlopen(req,timeout=5,context=ctx) as r:
        body=r.read()
        if r.status != 200:
            raise RuntimeError(f'{path}: HTTP {r.status}')
        return body

home=get('/',auth=True).decode('utf-8','replace')
required=(
    '/static/style.css',
    '/static/premium.css',
    '/static/v9.css',
    '/static/app.js',
    '/static/premium-ui.js',
    '/static/runtime-refresh-fix.js',
    '/static/safe-switch-ui.js',
    '/static/ui-stability.js',
)
for ref in required:
    if ref not in home:
        raise RuntimeError(f'index missing {ref}')
    if len(get(ref)) < 32:
        raise RuntimeError(f'{ref}: empty/too small')
status=json.loads(get('/api/status',auth=True).decode('utf-8'))
agents=json.loads(get('/api/agents',auth=True).decode('utf-8'))
if not isinstance(status.get('exits'),list):
    raise RuntimeError('/api/status contract failed')
if not isinstance(agents.get('agents'),list):
    raise RuntimeError('/api/agents contract failed')
failover=json.loads(Path('/etc/tp27/failover-v2.json').read_text(encoding='utf-8'))
failover_enabled=failover.get('enabled')
if not isinstance(failover_enabled, bool):
    raise RuntimeError('Failover V2 enabled must be boolean')
created=os.environ.get('TP27_FAILOVER_CONFIG_CREATED') == '1'
if created and failover_enabled is not False:
    raise RuntimeError('New Failover V2 config must default to disabled')
origin='created' if created else 'preserved'
print(
    f"INSTALL_SMOKE=OK EXITS={len(status['exits'])} AGENTS={len(agents['agents'])} "
    f"FAILOVER_V2_ENABLED={str(failover_enabled).lower()} FAILOVER_CONFIG={origin}"
)
PY

while IFS= read -r unit; do
  [[ -z "$unit" ]] && continue
  systemctl is-active --quiet "$unit" || {
    echo "ERROR: data plane stopped during panel update: $unit" >&2
    exit 4
  }
done <<<"$DATA_PLANES_BEFORE"

echo "PANEL=active"
echo "BENCHMARK=$(systemctl is-active tp27-benchmark.service 2>/dev/null || true)"
echo "FAILOVER_V2=$(systemctl is-active tp27-failover-v2.service 2>/dev/null || true)"
echo "FAILOVER_V2_ENABLED=$(python3 -c 'import json; print(str(bool(json.load(open("/etc/tp27/failover-v2.json")).get("enabled"))).lower())' 2>/dev/null || echo unknown)"
echo "FAILOVER_V2_RESTART=$FAILOVER_RESTART_STATE"
echo "VERSION=$(cat /etc/tp27/panel-version 2>/dev/null || echo unknown)"
echo "DATA_PLANES=verified-untouched"
echo "===== TP27 MANAGED UPDATE COMPLETE ====="