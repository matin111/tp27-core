#!/usr/bin/env bash
set -Eeuo pipefail

IP="${1:-193.39.9.220}"
PANEL_UNIT="/etc/systemd/system/tp27-panel.service"
CERTBOT_VENV="/opt/certbot"
LIVE="/etc/letsencrypt/live/$IP"
DOMAIN_MODE="/etc/tp27/domain-ssl.env"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ -f "$PANEL_UNIT" ]] || { echo "ERROR: $PANEL_UNIT not found"; exit 2; }

# Domain TLS is the authoritative production mode once enabled. Refuse to
# silently replace the HAProxy/domain architecture with a short-lived IP cert.
if [[ -s "$DOMAIN_MODE" && "${TP27_ALLOW_IP_SSL_DOWNGRADE:-0}" != "1" ]]; then
  echo "ERROR: TP27 domain TLS mode is active ($DOMAIN_MODE)." >&2
  echo "Refusing to downgrade the controller to direct short-lived IP TLS." >&2
  echo "Set TP27_ALLOW_IP_SSL_DOWNGRADE=1 only for an explicit emergency rollback." >&2
  exit 10
fi

python3 - "$IP" <<'PY'
import ipaddress, sys
ipaddress.ip_address(sys.argv[1])
PY

echo "============================================================"
echo " TP27 TRUSTED IP SSL"
echo " IP: $IP"
echo "============================================================"

valid_existing_cert() {
  [[ -s "$LIVE/fullchain.pem" && -s "$LIVE/privkey.pem" ]] || return 1
  openssl x509 -in "$LIVE/fullchain.pem" -noout -checkend 300 >/dev/null 2>&1 || return 1
  openssl x509 -in "$LIVE/fullchain.pem" -noout -ext subjectAltName 2>/dev/null \
    | grep -Fq "IP Address:$IP" || return 1
  return 0
}

apply_cert_to_panel() {
  local key="$1" crt="$2"
  python3 - "$PANEL_UNIT" "$key" "$crt" <<'PY'
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
}

install_renewal_units() {
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
  systemctl enable --now tp27-certbot-renew.timer >/dev/null 2>&1 || true
}

if valid_existing_cert; then
  echo "Existing trusted IP certificate is valid; preserving it."
  cp -a "$PANEL_UNIT" "$PANEL_UNIT.backup.$(date +%Y%m%d-%H%M%S)"
  apply_cert_to_panel "$LIVE/privkey.pem" "$LIVE/fullchain.pem"
  install_renewal_units
  systemctl daemon-reload
  systemctl restart tp27-panel.service
  sleep 2

  echo
  echo "===== CERTIFICATE ====="
  openssl x509 -in "$LIVE/fullchain.pem" -noout -issuer -subject -dates -ext subjectAltName
  echo
  echo "===== PANEL ====="
  systemctl --no-pager --full status tp27-panel.service | sed -n '1,12p'
  echo
  echo "============================================================"
  echo " TRUSTED HTTPS READY"
  echo " https://$IP:8787"
  echo "============================================================"
  exit 0
fi

cp -a "$PANEL_UNIT" "$PANEL_UNIT.backup.$(date +%Y%m%d-%H%M%S)"

for _ in $(seq 1 60); do
  if ! fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend >/dev/null 2>&1; then
    break
  fi
  echo "Waiting for apt lock..."
  sleep 2
done

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

if ss -lntH | awk '{print $4}' | grep -Eq '(^|\]):80$|(^|:)80$'; then
  echo "ERROR: TCP port 80 is already in use."
  echo "Current listeners:"
  ss -lntp | grep ':80 ' || true
  echo "No service was stopped automatically."
  exit 3
fi

certbot certonly \
  --standalone \
  --non-interactive \
  --agree-tos \
  --register-unsafely-without-email \
  --preferred-profile shortlived \
  --ip-address "$IP"

[[ -f "$LIVE/fullchain.pem" && -f "$LIVE/privkey.pem" ]] || {
  echo "ERROR: expected certificate files were not created under $LIVE"
  certbot certificates || true
  exit 4
}

apply_cert_to_panel "$LIVE/privkey.pem" "$LIVE/fullchain.pem"
install_renewal_units
systemctl daemon-reload
systemctl restart tp27-panel.service
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
