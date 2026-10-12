#!/usr/bin/env bash
set -Eeuo pipefail
RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"
REQUESTED_IP="${1:-}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
command -v tp27 >/dev/null || { echo "ERROR: install TP27 core first"; exit 2; }

valid_ip() {
  python3 - "$1" <<'PY'
import ipaddress, sys
try:
    ipaddress.ip_address(sys.argv[1])
except Exception:
    raise SystemExit(1)
PY
}

detect_ip() {
  local ip=""
  ip="$(python3 - <<'PY' 2>/dev/null || true
import json
try:
    x=json.load(open('/etc/tp27/config.json')).get('public_ip','')
    print(x if x else '')
except Exception:
    print('')
PY
)"
  if [[ -n "$ip" ]] && valid_ip "$ip"; then echo "$ip"; return 0; fi
  ip="$(curl -4 -fsS --max-time 5 https://ifconfig.me 2>/dev/null || true)"
  if [[ -n "$ip" ]] && valid_ip "$ip"; then echo "$ip"; return 0; fi
  ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
  [[ -n "$ip" ]] && valid_ip "$ip" && echo "$ip"
}

if [[ -n "$REQUESTED_IP" ]]; then
  valid_ip "$REQUESTED_IP" || { echo "ERROR: invalid IP: $REQUESTED_IP"; exit 3; }
  IP="$REQUESTED_IP"
else
  IP="$(detect_ip || true)"
  [[ -n "$IP" ]] || { echo "ERROR: could not detect server IP; run: install-panel.sh <IP>"; exit 3; }
fi

LIVE="/etc/letsencrypt/live/$IP"
PANEL_UNIT="/etc/systemd/system/tp27-panel.service"

valid_existing_cert() {
  command -v openssl >/dev/null 2>&1 || return 1
  [[ -s "$LIVE/fullchain.pem" && -s "$LIVE/privkey.pem" ]] || return 1
  openssl x509 -in "$LIVE/fullchain.pem" -noout -checkend 300 >/dev/null 2>&1 || return 1
  openssl x509 -in "$LIVE/fullchain.pem" -noout -ext subjectAltName 2>/dev/null | grep -Fq "IP Address:$IP"
}

patch_panel_cert() {
  local key="$1" crt="$2"
  python3 - "$PANEL_UNIT" "$key" "$crt" <<'PY'
from pathlib import Path
import re, sys
p=Path(sys.argv[1]); key=sys.argv[2]; crt=sys.argv[3]
s=p.read_text()
new=re.sub(r'--ssl-keyfile\s+\S+\s+--ssl-certfile\s+\S+', f'--ssl-keyfile {key} --ssl-certfile {crt}', s)
if new == s and '--ssl-keyfile' not in s:
    raise SystemExit('ERROR: ssl arguments not found in panel unit')
p.write_text(new)
PY
}

wait_for_apt() {
  for _ in $(seq 1 60); do
    if ! fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend >/dev/null 2>&1; then return 0; fi
    echo "Waiting for apt lock..."; sleep 2
  done
  echo "ERROR: apt remained busy for too long"; return 1
}

choose_plain_port() {
  local preferred=""
  if [[ -f /etc/tp27/plain-control.env ]]; then
    preferred="$(awk -F= '$1=="TP27_PLAIN_PORT"{print $2}' /etc/tp27/plain-control.env | tail -1)"
  fi
  systemctl stop tp27-plain-control.service 2>/dev/null || true
  local candidates=()
  [[ "$preferred" =~ ^[0-9]+$ ]] && candidates+=("$preferred")
  candidates+=(443 80 8080 2053 2083 8443 10443)
  local p seen=" "
  for p in "${candidates[@]}"; do
    [[ "$seen" == *" $p "* ]] && continue
    seen+="$p "
    if ! ss -lntp | awk -v port=":$p" '$4 ~ port"$" {found=1} END{exit !found}'; then
      echo "$p"; return 0
    fi
  done
  return 1
}

echo "============================================================"
echo " TP27 PANEL MULTI-CORE INSTALLER"
echo " PANEL IP: $IP"
echo "============================================================"

export DEBIAN_FRONTEND=noninteractive
wait_for_apt
apt-get update
apt-get install -y python3-venv openssl curl ca-certificates iptables openssh-client sshpass socat jq iproute2 iputils-ping kmod tar coreutils

install -d -m 755 /opt/tp27-panel /opt/tp27-panel/static /usr/local/lib/tp27
install -d -m 700 /etc/tp27 /etc/tp27/backups /var/log/tp27/panel-jobs
python3 -m venv /opt/tp27-panel/venv
/opt/tp27-panel/venv/bin/pip install --upgrade pip >/dev/null
curl -fsSL "$RAW/panel/requirements.txt" -o /opt/tp27-panel/requirements.txt
/opt/tp27-panel/venv/bin/pip install -r /opt/tp27-panel/requirements.txt >/dev/null

for f in app.py auth.py provision.py provision_password.py provision_agent.py agent_control.py ports.py portplan.py plain_control.py; do
  curl -fsSL "$RAW/panel/$f" -o "/opt/tp27-panel/$f"
done
# Keep the mature GRE/FOU/TLS builder as an internal layer and expose the
# multi-core wrapper as provision_gre.py so app.py does not need a migration.
curl -fsSL "$RAW/panel/provision_resilient.py" -o /opt/tp27-panel/provision_resilient.py
curl -fsSL "$RAW/panel/provision_multicore.py" -o /opt/tp27-panel/provision_gre.py

for f in index.html login.html style.css app.js fast-ui.js agent-manage.js; do
  curl -fsSL "$RAW/panel/static/$f" -o "/opt/tp27-panel/static/$f"
done

curl -fsSL "$RAW/systemd/tp27-panel.service" -o "$PANEL_UNIT"
curl -fsSL "$RAW/systemd/tp27-ports-reconcile.service" -o /etc/systemd/system/tp27-ports-reconcile.service
curl -fsSL "$RAW/systemd/tp27-plain-control.service" -o /etc/systemd/system/tp27-plain-control.service
curl -fsSL "$RAW/bin/tp27-link-mtls" -o /usr/local/lib/tp27/tp27-link
curl -fsSL "$RAW/bin/tp27-link-dispatcher" -o /usr/local/lib/tp27/tp27-link-dispatcher
curl -fsSL "$RAW/systemd/tp27-link@.service" -o /etc/systemd/system/tp27-link@.service
curl -fsSL "$RAW/bin/tp27-framed-tls.py" -o /usr/local/lib/tp27/tp27-framed-tls.py
curl -fsSL "$RAW/systemd/tp27-framed@.service" -o /etc/systemd/system/tp27-framed@.service
curl -fsSL "$RAW/bin/tp27-gost-core.py" -o /usr/local/lib/tp27/tp27-gost-core.py
curl -fsSL "$RAW/bin/tp27-gost-install.sh" -o /usr/local/lib/tp27/tp27-gost-install.sh
chmod 755 \
  /usr/local/lib/tp27/tp27-link \
  /usr/local/lib/tp27/tp27-link-dispatcher \
  /usr/local/lib/tp27/tp27-framed-tls.py \
  /usr/local/lib/tp27/tp27-gost-core.py \
  /usr/local/lib/tp27/tp27-gost-install.sh
chmod 600 /opt/tp27-panel/*.py

/opt/tp27-panel/venv/bin/python -m py_compile \
  /opt/tp27-panel/app.py /opt/tp27-panel/auth.py /opt/tp27-panel/provision.py \
  /opt/tp27-panel/provision_password.py /opt/tp27-panel/provision_agent.py \
  /opt/tp27-panel/provision_resilient.py /opt/tp27-panel/provision_gre.py \
  /opt/tp27-panel/agent_control.py /opt/tp27-panel/plain_control.py \
  /opt/tp27-panel/ports.py /opt/tp27-panel/portplan.py \
  /usr/local/lib/tp27/tp27-framed-tls.py /usr/local/lib/tp27/tp27-gost-core.py

GOST_STATE="deferred"
if /usr/local/lib/tp27/tp27-gost-install.sh; then
  GOST_STATE="ready"
else
  echo "WARNING: GOST v3.3.0 download is currently unavailable; builder will retry automatically when selected." >&2
fi

if [[ ! -s /etc/tp27/agent-enroll.token ]]; then
  python3 - <<'PY'
import secrets
from pathlib import Path
p=Path('/etc/tp27/agent-enroll.token'); p.write_text(secrets.token_urlsafe(32)+'\n'); p.chmod(0o600)
PY
fi

NEW_PASSWORD=""
if [[ ! -f /etc/tp27/panel.json ]]; then
  NEW_PASSWORD="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(14))
PY
)"
  TP27_PANEL_PASSWORD="$NEW_PASSWORD" python3 - <<'PY'
import hashlib, json, os, secrets
from pathlib import Path
password=os.environ['TP27_PANEL_PASSWORD']; salt=secrets.token_bytes(16)
digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,240000)
obj={'password_salt':salt.hex(),'password_hash':digest.hex(),'session_secret':secrets.token_bytes(32).hex()}
p=Path('/etc/tp27/panel.json'); p.write_text(json.dumps(obj,indent=2)+'\n'); p.chmod(0o600)
PY
fi

printf '%s\n' "$IP" >/etc/tp27/panel-ip
chmod 600 /etc/tp27/panel-ip

if [[ ! -f /etc/tp27/panel.key || ! -f /etc/tp27/panel.crt ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \
    -subj "/CN=$IP" -addext "subjectAltName=IP:$IP" \
    -keyout /etc/tp27/panel.key -out /etc/tp27/panel.crt >/dev/null 2>&1
  chmod 600 /etc/tp27/panel.key; chmod 644 /etc/tp27/panel.crt
fi

PREEXISTING_TRUSTED=0
if valid_existing_cert; then
  PREEXISTING_TRUSTED=1
  patch_panel_cert "$LIVE/privkey.pem" "$LIVE/fullchain.pem"
  echo "Preserving existing trusted IP certificate during upgrade."
fi

systemctl daemon-reload
systemctl enable tp27-panel.service >/dev/null 2>&1 || true
systemctl restart tp27-panel.service
systemctl enable tp27-ports-reconcile.service >/dev/null 2>&1 || true
systemctl restart tp27-ports-reconcile.service || true

PLAIN_PORT="$(choose_plain_port || true)"
if [[ -n "$PLAIN_PORT" ]]; then
  printf 'TP27_PLAIN_PORT=%s\n' "$PLAIN_PORT" >/etc/tp27/plain-control.env
  chmod 600 /etc/tp27/plain-control.env
  systemctl daemon-reload
  systemctl enable tp27-plain-control.service >/dev/null 2>&1 || true
  systemctl restart tp27-plain-control.service
  sleep 1
  if curl -fsS --connect-timeout 2 --max-time 4 "http://127.0.0.1:$PLAIN_PORT/health" | grep -q 'plain-hmac-v1'; then
    echo "Signed Agent Control: http://$IP:$PLAIN_PORT"
  else
    echo "WARNING: signed Agent Control failed health check"
  fi
else
  echo "WARNING: no free signed-control TCP port found; HTTPS enrollment remains available"
fi

SSL_STATE="self-signed"
[[ "$PREEXISTING_TRUSTED" -eq 1 ]] && SSL_STATE="trusted"
echo
echo "===== TRUSTED IP SSL ====="
SSL_SCRIPT="$(mktemp /tmp/tp27-ip-ssl.XXXXXX.sh)"
if curl -fsSL "$RAW/enable-ip-ssl.sh" -o "$SSL_SCRIPT"; then
  chmod 700 "$SSL_SCRIPT"
  if bash "$SSL_SCRIPT" "$IP"; then SSL_STATE="trusted"; else
    [[ "$PREEXISTING_TRUSTED" -eq 1 ]] && SSL_STATE="trusted" || true
  fi
fi
rm -f "$SSL_SCRIPT"

echo
echo "============================================================"
echo " TP27 PANEL INSTALLED"
echo " URL: https://$IP:8787"
echo " SSL: $SSL_STATE"
if [[ -n "$NEW_PASSWORD" ]]; then
  echo " USER: admin"
  echo " PASSWORD: $NEW_PASSWORD"
  printf 'URL=https://%s:8787\nUSER=admin\nPASSWORD=%s\n' "$IP" "$NEW_PASSWORD" >/root/tp27-panel-credentials.txt
  chmod 600 /root/tp27-panel-credentials.txt
  echo " Saved: /root/tp27-panel-credentials.txt"
else
  echo " Existing panel password preserved."
  [[ -f /root/tp27-panel-credentials.txt ]] && sed -i "s#^URL=.*#URL=https://$IP:8787#" /root/tp27-panel-credentials.txt || true
fi

echo " Panel: $(systemctl is-active tp27-panel.service || true)"
echo " Signed control: $(systemctl is-active tp27-plain-control.service 2>/dev/null || true)${PLAIN_PORT:+ · TCP/$PLAIN_PORT}"
echo " Auto cores: Raw GRE -> GRE/UDP FOU -> GOST/WSS -> Framed TLS/TCP"
echo " GOST: v3.3.0 ($GOST_STATE)"
echo " Existing VPN daemons are not restarted by TP27 provisioning"
echo "============================================================"
