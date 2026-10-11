#!/usr/bin/env bash
set -Eeuo pipefail

IP="${1:-193.39.9.220}"
PANEL_UNIT="/etc/systemd/system/tp27-panel.service"
CERTBOT_VENV="/opt/certbot"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ -f "$PANEL_UNIT" ]] || { echo "ERROR: $PANEL_UNIT not found"; exit 2; }

python3 - "$IP" <<'PY'
import ipaddress, sys
ipaddress.ip_address(sys.argv[1])
PY

echo "============================================================"
echo " TP27 TRUSTED IP SSL"
echo " IP: $IP"
echo "============================================================"

# Back up only TP27 panel unit before changing certificate paths.
cp -a "$PANEL_UNIT" "$PANEL_UNIT.backup.$(date +%Y%m%d-%H%M%S)"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv ca-certificates curl openssl

if [[ ! -x "$CERTBOT_VENV/bin/certbot" ]]; then
  python3 -m venv "$CERTBOT_VENV"
fi
"$CERTBOT_VENV/bin/pip" install --upgrade pip >/dev/null
"$CERTBOT_VENV/bin/pip" install --upgrade 'certbot>=5.4' >/dev/null
ln -sfn "$CERTBOT_VENV/bin/certbot" /usr/local/bin/certbot

echo "Certbot: $(certbot --version 2>&1)"

# IP certificate validation uses HTTP-01 on TCP/80. Do not stop unrelated services.
if ss -lntH | awk '{print $4}' | grep -Eq '(^|\]):80$|(^|:)80$'; then
  echo "ERROR: TCP port 80 is already in use."
  echo "Current listeners:"
  ss -lntp | grep ':80 ' || true
  echo "No service was stopped automatically."
  exit 3
fi

# Request a publicly trusted short-lived IP certificate from Let's Encrypt.
certbot certonly \
  --standalone \
  --non-interactive \
  --agree-tos \
  --register-unsafely-without-email \
  --preferred-profile shortlived \
  --ip-address "$IP"

LIVE="/etc/letsencrypt/live/$IP"
[[ -f "$LIVE/fullchain.pem" && -f "$LIVE/privkey.pem" ]] || {
  echo "ERROR: expected certificate files were not created under $LIVE"
  certbot certificates || true
  exit 4
}

python3 - "$PANEL_UNIT" "$LIVE/privkey.pem" "$LIVE/fullchain.pem" <<'PY'
from pathlib import Path
import re, sys
p=Path(sys.argv[1])
key=sys.argv[2]
crt=sys.argv[3]
s=p.read_text()
new=re.sub(r'--ssl-keyfile\s+\S+\s+--ssl-certfile\s+\S+', f'--ssl-keyfile {key} --ssl-certfile {crt}', s)
if new == s and '--ssl-keyfile' not in s:
    raise SystemExit('ERROR: ssl arguments not found in panel unit')
p.write_text(new)
PY

install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
cat >/etc/letsencrypt/renewal-hooks/deploy/tp27-panel.sh <<'EOF'
#!/usr/bin/env bash
systemctl restart tp27-panel.service
EOF
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/tp27-panel.sh

cat >/etc/systemd/system/tp27-certbot-renew.service <<'EOF'
[Unit]
Description=Renew TP27 Let's Encrypt IP certificate

[Service]
Type=oneshot
ExecStart=/opt/certbot/bin/certbot renew -q
EOF

cat >/etc/systemd/system/tp27-certbot-renew.timer <<'EOF'
[Unit]
Description=Periodic renewal check for TP27 IP certificate

[Timer]
OnCalendar=*-*-* 00,06,12,18:17:00
RandomizedDelaySec=1200
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl restart tp27-panel.service
systemctl enable --now tp27-certbot-renew.timer

sleep 2

echo
echo "===== CERTIFICATE ====="
openssl x509 -in "$LIVE/fullchain.pem" -noout -issuer -subject -dates -ext subjectAltName

echo
echo "===== PANEL ====="
systemctl --no-pager --full status tp27-panel.service | sed -n '1,12p'

echo
echo "===== RENEW TIMER ====="
systemctl list-timers tp27-certbot-renew.timer --no-pager

echo
echo "============================================================"
echo " TRUSTED HTTPS READY"
echo " https://$IP:8787"
echo "============================================================"
