#!/usr/bin/env bash
set -Eeuo pipefail

DOMAIN="${1:-}"
IP="${2:-}"
MODE="${3:-}"
RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"
CONFIG="/etc/tp27/domain-ssl.env"
LIVE="/etc/letsencrypt/live/$DOMAIN"
BUNDLE="/etc/haproxy/tp27-certs/domain.pem"
HAPROXY_CFG="/etc/haproxy/haproxy.cfg"
BACKEND="127.0.0.1:18787"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }
[[ -n "$DOMAIN" && -n "$IP" ]] || {
  echo "Usage: $0 <domain> <controller-ip> [--domain-only]" >&2
  exit 2
}

python3 - "$DOMAIN" "$IP" <<'PY'
import ipaddress, re, sys
name, ip = sys.argv[1], sys.argv[2]
ipaddress.ip_address(ip)
if len(name) > 253 or not re.fullmatch(r'(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}', name):
    raise SystemExit('ERROR: invalid DNS domain')
PY

for cmd in curl openssl haproxy systemctl ss; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: missing command: $cmd" >&2; exit 3; }
done

DNS_IP="$(getent ahostsv4 "$DOMAIN" | awk '$2=="STREAM"{print $1; exit}')"
[[ "$DNS_IP" == "$IP" ]] || { echo "ERROR: $DOMAIN resolves to ${DNS_IP:-none}, expected $IP" >&2; exit 4; }
[[ -s "$LIVE/fullchain.pem" && -s "$LIVE/privkey.pem" ]] || { echo "ERROR: domain certificate not found under $LIVE" >&2; exit 5; }
openssl x509 -in "$LIVE/fullchain.pem" -noout -ext subjectAltName | grep -Fq "DNS:$DOMAIN" \
  || { echo "ERROR: certificate SAN does not contain DNS:$DOMAIN" >&2; exit 6; }
openssl x509 -in "$LIVE/fullchain.pem" -noout -checkend 604800 >/dev/null \
  || { echo "ERROR: certificate has less than 7 days validity" >&2; exit 7; }

if ! ss -lntH | awk '{print $4}' | grep -qE '127\.0\.0\.1:18787$'; then
  echo "ERROR: migrated TP27 backend is not listening on $BACKEND" >&2
  echo "Refusing to rewrite TLS frontend on an unknown panel architecture." >&2
  exit 8
fi

BACKEND_HTTP="$(curl --connect-timeout 3 --max-time 6 -sS -o /dev/null -w '%{http_code}' "http://$BACKEND/" 2>/dev/null || true)"
[[ "$BACKEND_HTTP" != "000" ]] || { echo "ERROR: backend health failed" >&2; exit 9; }

echo "============================================================"
echo " TP27 DOMAIN TLS ACTIVATION"
echo " DOMAIN=$DOMAIN"
echo " IP=$IP"
echo " BACKEND=$BACKEND"
echo "============================================================"
echo "BACKEND_HTTP=$BACKEND_HTTP"

STAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP="/etc/tp27/backups/domain-ssl-$STAMP"
install -d -m 700 "$BACKUP" /etc/tp27 /etc/tp27/backups
install -d -m 755 /usr/local/lib/tp27 /etc/haproxy/tp27-certs
cp -a "$HAPROXY_CFG" "$BACKUP/haproxy.cfg"
[[ -f /etc/systemd/system/tp27-panel.service ]] && cp -a /etc/systemd/system/tp27-panel.service "$BACKUP/tp27-panel.service"

echo "BACKUP=$BACKUP"

fetch() {
  local src="$1" dst="$2" tmp
  tmp="${dst}.tp27-download.$$"
  curl -fsSL --connect-timeout 10 --max-time 90 "$RAW/$src" -o "$tmp"
  [[ -s "$tmp" ]] || { rm -f "$tmp"; return 1; }
  mv -f "$tmp" "$dst"
}

fetch bin/tp27-domain-cert-renew /usr/local/lib/tp27/tp27-domain-cert-renew
fetch systemd/tp27-domain-cert-renew.service /etc/systemd/system/tp27-domain-cert-renew.service
fetch systemd/tp27-domain-cert-renew.timer /etc/systemd/system/tp27-domain-cert-renew.timer
chmod 755 /usr/local/lib/tp27/tp27-domain-cert-renew

cat >"$CONFIG" <<EOF
TP27_DOMAIN=$DOMAIN
TP27_CONTROLLER_IP=$IP
TP27_ACME_HOST=acme-v02.api.letsencrypt.org
TP27_ACME_LOCAL=127.0.0.2
TP27_ACME_FORWARD_SERVICE=tp27-acme-ssh-forward.service
TP27_PLAIN_SERVICE=tp27-plain-control.service
TP27_CERTBOT=/usr/local/bin/certbot
TP27_HAPROXY_CFG=/etc/haproxy/haproxy.cfg
TP27_CERT_LIVE=/etc/letsencrypt/live/$DOMAIN
TP27_HAPROXY_CERT_BUNDLE=/etc/haproxy/tp27-certs/domain.pem
TP27_RENEW_BEFORE_SECONDS=2592000
EOF
chmod 600 "$CONFIG"

TMP_BUNDLE="${BUNDLE}.tmp.$$"
cat "$LIVE/fullchain.pem" "$LIVE/privkey.pem" >"$TMP_BUNDLE"
chown root:haproxy "$TMP_BUNDLE" 2>/dev/null || chown root:root "$TMP_BUNDLE"
chmod 640 "$TMP_BUNDLE"
mv -f "$TMP_BUNDLE" "$BUNDLE"

if [[ "$MODE" == "--domain-only" ]]; then
  cat >"$HAPROXY_CFG" <<EOF
global
    log /dev/log local0
    log /dev/log local1 notice
    user haproxy
    group haproxy
    daemon
    maxconn 10000

defaults
    log global
    mode http
    option httplog
    option dontlognull
    option forwardfor
    timeout connect 10s
    timeout client 300s
    timeout server 300s

frontend tp27_control_tls
    bind 0.0.0.0:8787 ssl crt $BUNDLE
    http-request set-header X-Forwarded-Proto https
    default_backend tp27_control_backend

backend tp27_control_backend
    server tp27panel 127.0.0.1:18787 check inter 1s fall 3 rise 2
EOF
fi

if ! haproxy -c -f "$HAPROXY_CFG"; then
  cp -a "$BACKUP/haproxy.cfg" "$HAPROXY_CFG"
  echo "ERROR: new HAProxy config invalid; restored backup" >&2
  exit 10
fi

# Retire the two overlapping IP-certificate renewal paths. They must not race
# the domain renewal job or restart the backend behind HAProxy.
systemctl disable --now tp27-cert-renew.timer >/dev/null 2>&1 || true
systemctl disable --now tp27-certbot-renew.timer >/dev/null 2>&1 || true
if [[ -f /etc/letsencrypt/renewal-hooks/deploy/tp27-panel.sh ]]; then
  mv /etc/letsencrypt/renewal-hooks/deploy/tp27-panel.sh "$BACKUP/tp27-panel-letsencrypt-hook.sh"
fi

systemctl daemon-reload
systemctl enable --now tp27-domain-cert-renew.timer
systemctl reload haproxy

DOMAIN_HTTP="000"
for attempt in $(seq 1 15); do
  DOMAIN_HTTP="$(curl -4 --connect-timeout 3 --max-time 6 -sS -o /dev/null -w '%{http_code}' "https://${DOMAIN}:8787/" 2>/dev/null || true)"
  [[ "$DOMAIN_HTTP" != "000" ]] && break
  sleep 1
done

if [[ "$DOMAIN_HTTP" == "000" ]]; then
  cp -a "$BACKUP/haproxy.cfg" "$HAPROXY_CFG"
  haproxy -c -f "$HAPROXY_CFG" >/dev/null && systemctl reload haproxy || true
  echo "ERROR: strict domain TLS health failed; HAProxy config rolled back" >&2
  exit 11
fi

/usr/local/lib/tp27/tp27-domain-cert-renew

echo "DOMAIN_HTTP=$DOMAIN_HTTP"
echo "DOMAIN_CERT_TIMER=$(systemctl is-active tp27-domain-cert-renew.timer)"
echo "IP_RENEW_TIMER_1=$(systemctl is-active tp27-cert-renew.timer 2>/dev/null || true)"
echo "IP_RENEW_TIMER_2=$(systemctl is-active tp27-certbot-renew.timer 2>/dev/null || true)"
openssl x509 -in "$LIVE/fullchain.pem" -noout -issuer -serial -dates -ext subjectAltName

echo "============================================================"
echo " DOMAIN TLS MODE READY"
echo " URL=https://${DOMAIN}:8787"
echo " MODE=${MODE:---dual-preserved}"
echo " AUTO_RENEW=ENABLED"
echo " NO_TUNNEL_MODIFIED"
echo " BACKUP=$BACKUP"
echo "============================================================"
