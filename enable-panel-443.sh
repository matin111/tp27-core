#!/usr/bin/env bash
set -Eeuo pipefail

IP="${1:-}"
[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }

if [[ -z "$IP" && -f /etc/tp27/panel-ip ]]; then
  IP="$(tr -d ' \r\n\t' </etc/tp27/panel-ip)"
fi
[[ -n "$IP" ]] || { echo "ERROR: panel IP required"; exit 2; }

LIVE="/etc/letsencrypt/live/$IP"
CERT="$LIVE/fullchain.pem"
KEY="$LIVE/privkey.pem"
[[ -s "$CERT" && -s "$KEY" ]] || { echo "ERROR: trusted certificate not found for $IP"; exit 3; }
openssl x509 -in "$CERT" -noout -checkend 300 >/dev/null 2>&1 || { echo "ERROR: certificate is expired/near expiry"; exit 4; }
openssl x509 -in "$CERT" -noout -ext subjectAltName 2>/dev/null | grep -Fq "IP Address:$IP" || { echo "ERROR: certificate SAN does not match $IP"; exit 5; }

# Do not disturb an existing non-nginx service on 443.
if ss -lntp | grep -qE '(^|[[:space:]])[^[:space:]]*:443[[:space:]]'; then
  if ! ss -lntp | grep -E ':443[[:space:]]' | grep -q nginx; then
    echo "ERROR: TCP/443 is already in use by another service. Nothing changed."
    ss -lntp | grep -E ':443[[:space:]]' || true
    exit 6
  fi
fi

wait_for_apt() {
  for _ in $(seq 1 60); do
    if ! fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend >/dev/null 2>&1; then
      return 0
    fi
    echo "Waiting for apt lock..."
    sleep 2
  done
  echo "ERROR: apt remained busy for too long"
  return 1
}

if ! command -v nginx >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  wait_for_apt
  apt-get update
  apt-get install -y nginx
fi

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/tp27-panel-nginx-backup-$TS"
mkdir -p "$BACKUP"
cp -a /etc/nginx/nginx.conf "$BACKUP/" 2>/dev/null || true
cp -a /etc/nginx/sites-available "$BACKUP/" 2>/dev/null || true
cp -a /etc/nginx/sites-enabled "$BACKUP/" 2>/dev/null || true

cat >/etc/nginx/sites-available/tp27-panel <<EOF
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name _;

    ssl_certificate     $CERT;
    ssl_certificate_key $KEY;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:TP27SSL:10m;
    ssl_session_timeout 10m;

    client_max_body_size 2m;

    location / {
        proxy_pass https://127.0.0.1:8787;
        proxy_http_version 1.1;
        proxy_ssl_verify off;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_connect_timeout 5s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        proxy_buffering off;
    }
}
EOF

ln -sfn /etc/nginx/sites-available/tp27-panel /etc/nginx/sites-enabled/tp27-panel
nginx -t
systemctl enable nginx >/dev/null 2>&1 || true
systemctl restart nginx

sleep 1

echo "============================================================"
echo " TP27 PANEL STANDARD HTTPS READY"
echo " URL: https://$IP/"
echo " Browser port: 443 via nginx"
echo " Agent/API port 8787: unchanged"
echo " Backup: $BACKUP"
echo "============================================================"

ss -lntp | grep -E ':443[[:space:]]' || true
curl -fsS --max-time 8 -o /dev/null -w 'HTTPS_443=%{http_code}\n' "https://$IP/login" || true
