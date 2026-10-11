#!/usr/bin/env bash
set -Eeuo pipefail
RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"
[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
command -v tp27 >/dev/null || { echo "ERROR: install TP27 core first"; exit 2; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv openssl curl ca-certificates

install -d -m 755 /opt/tp27-panel /opt/tp27-panel/static
install -d -m 700 /etc/tp27 /var/log/tp27/panel-jobs
python3 -m venv /opt/tp27-panel/venv
/opt/tp27-panel/venv/bin/pip install --upgrade pip >/dev/null
curl -fsSL "$RAW/panel/requirements.txt" -o /opt/tp27-panel/requirements.txt
/opt/tp27-panel/venv/bin/pip install -r /opt/tp27-panel/requirements.txt

for f in app.py auth.py provision.py; do
  curl -fsSL "$RAW/panel/$f" -o "/opt/tp27-panel/$f"
done
for f in index.html login.html style.css app.js; do
  curl -fsSL "$RAW/panel/static/$f" -o "/opt/tp27-panel/static/$f"
done
curl -fsSL "$RAW/systemd/tp27-panel.service" -o /etc/systemd/system/tp27-panel.service
chmod 600 /opt/tp27-panel/*.py

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

IP="$(python3 - <<'PY'
import json
try:
    print(json.load(open('/etc/tp27/config.json')).get('public_ip','127.0.0.1'))
except Exception:
    print('127.0.0.1')
PY
)"
if [[ ! -f /etc/tp27/panel.key || ! -f /etc/tp27/panel.crt ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \
    -subj "/CN=$IP" -addext "subjectAltName=IP:$IP" \
    -keyout /etc/tp27/panel.key -out /etc/tp27/panel.crt >/dev/null 2>&1
  chmod 600 /etc/tp27/panel.key
  chmod 644 /etc/tp27/panel.crt
fi

systemctl daemon-reload
systemctl enable --now tp27-panel.service

echo
echo "============================================================"
echo " TP27 PANEL INSTALLED"
echo " URL: https://$IP:8787"
if [[ -n "$NEW_PASSWORD" ]]; then
  echo " USER: admin"
  echo " PASSWORD: $NEW_PASSWORD"
  printf 'URL=https://%s:8787\nUSER=admin\nPASSWORD=%s\n' "$IP" "$NEW_PASSWORD" >/root/tp27-panel-credentials.txt
  chmod 600 /root/tp27-panel-credentials.txt
  echo " Saved: /root/tp27-panel-credentials.txt"
else
  echo " Existing panel password preserved."
fi
echo "============================================================"
