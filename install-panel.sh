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
  DETECTED_IP="$(detect_ip || true)"
  [[ -n "$DETECTED_IP" ]] || { echo "ERROR: could not detect server IP; run: install-panel.sh <IP>"; exit 3; }
  IP="$DETECTED_IP"
  if [[ -r /dev/tty && -w /dev/tty ]]; then
    echo
    echo "Detected server IP: $DETECTED_IP"
    printf 'Panel IP [%s]: ' "$DETECTED_IP" >/dev/tty
    INPUT_IP=""
    IFS= read -r INPUT_IP </dev/tty || true
    if [[ -n "$INPUT_IP" ]]; then
      valid_ip "$INPUT_IP" || { echo "ERROR: invalid IP: $INPUT_IP"; exit 3; }
      IP="$INPUT_IP"
    fi
  fi
fi

echo "============================================================"
echo " TP27 PANEL INSTALLER"
echo " PANEL IP: $IP"
echo "============================================================"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv openssl curl ca-certificates iptables openssh-client sshpass

install -d -m 755 /opt/tp27-panel /opt/tp27-panel/static
install -d -m 700 /etc/tp27 /var/log/tp27/panel-jobs
python3 -m venv /opt/tp27-panel/venv
/opt/tp27-panel/venv/bin/pip install --upgrade pip >/dev/null
curl -fsSL "$RAW/panel/requirements.txt" -o /opt/tp27-panel/requirements.txt
/opt/tp27-panel/venv/bin/pip install -r /opt/tp27-panel/requirements.txt

for f in app.py auth.py provision.py provision_password.py ports.py portplan.py; do
  curl -fsSL "$RAW/panel/$f" -o "/opt/tp27-panel/$f"
done
for f in index.html login.html style.css app.js; do
  curl -fsSL "$RAW/panel/static/$f" -o "/opt/tp27-panel/static/$f"
done
curl -fsSL "$RAW/systemd/tp27-panel.service" -o /etc/systemd/system/tp27-panel.service
curl -fsSL "$RAW/systemd/tp27-ports-reconcile.service" -o /etc/systemd/system/tp27-ports-reconcile.service
chmod 600 /opt/tp27-panel/*.py

# Dedicated control-plane SSH identity. Private key never leaves the Iran node.
CONTROL_KEY=/etc/tp27/control_ed25519
if [[ ! -s "$CONTROL_KEY" || ! -s "${CONTROL_KEY}.pub" ]]; then
  rm -f "$CONTROL_KEY" "${CONTROL_KEY}.pub"
  ssh-keygen -q -t ed25519 -N '' -C "tp27-control@$IP" -f "$CONTROL_KEY"
fi
chmod 600 "$CONTROL_KEY"
chmod 644 "${CONTROL_KEY}.pub"

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
password=os.environ['TP27_PANEL_PASSWORD']
salt=secrets.token_bytes(16)
digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,240000)
obj={'password_salt':salt.hex(),'password_hash':digest.hex(),'session_secret':secrets.token_bytes(32).hex()}
p=Path('/etc/tp27/panel.json')
p.write_text(json.dumps(obj,indent=2)+'\n')
p.chmod(0o600)
PY
fi

printf '%s\n' "$IP" >/etc/tp27/panel-ip
chmod 600 /etc/tp27/panel-ip

if [[ ! -f /etc/tp27/panel.key || ! -f /etc/tp27/panel.crt ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \
    -subj "/CN=$IP" -addext "subjectAltName=IP:$IP" \
    -keyout /etc/tp27/panel.key -out /etc/tp27/panel.crt >/dev/null 2>&1
  chmod 600 /etc/tp27/panel.key
  chmod 644 /etc/tp27/panel.crt
fi

systemctl daemon-reload
systemctl enable tp27-panel.service >/dev/null 2>&1 || true
systemctl restart tp27-panel.service
systemctl enable tp27-ports-reconcile.service >/dev/null 2>&1 || true
systemctl restart tp27-ports-reconcile.service || true

SSL_STATE="self-signed"
echo
echo "===== TRUSTED IP SSL ====="
SSL_SCRIPT="$(mktemp /tmp/tp27-ip-ssl.XXXXXX.sh)"
if curl -fsSL "$RAW/enable-ip-ssl.sh" -o "$SSL_SCRIPT"; then
  chmod 700 "$SSL_SCRIPT"
  if bash "$SSL_SCRIPT" "$IP"; then
    SSL_STATE="trusted"
  else
    echo "WARNING: trusted IP SSL could not be issued now."
    echo "Panel remains available with the fallback self-signed certificate."
  fi
else
  echo "WARNING: could not download IP SSL helper."
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
  if [[ -f /root/tp27-panel-credentials.txt ]]; then
    sed -i "s#^URL=.*#URL=https://$IP:8787#" /root/tp27-panel-credentials.txt || true
  fi
fi

echo
echo " Service: $(systemctl is-active tp27-panel.service || true)"
echo " Port reconcile: $(systemctl is-active tp27-ports-reconcile.service || true)"
echo " Control key: ready"
if [[ "$SSL_STATE" == "trusted" ]]; then
  echo " Trusted IP certificate and automatic renewal: enabled"
else
  echo " Trusted IP certificate: pending/fallback"
fi
echo "============================================================"
