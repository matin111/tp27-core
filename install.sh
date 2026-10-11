#!/usr/bin/env bash
set -Eeuo pipefail

ROLE="${1:-}"
RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"

if [[ "$EUID" -ne 0 ]]; then
  echo "ERROR: run as root"
  exit 1
fi
if [[ "$ROLE" != "iran" && "$ROLE" != "exit" ]]; then
  echo "Usage: install.sh iran|exit"
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y socat openssl iproute2 iptables jq curl ca-certificates python3 openssh-client

install -d -m 700 /etc/tp27
install -d -m 700 /etc/tp27/certs
install -d -m 700 /etc/tp27/exits
install -d -m 700 /etc/tp27/backups
install -d -m 755 /var/lib/tp27
install -d -m 755 /var/log/tp27
install -d -m 755 /usr/local/lib/tp27
install -d -m 755 /usr/local/bin

# Migrate only the old TP27 WireGuard prototype. Never touch unrelated WG interfaces.
if [[ -f /etc/tp27/config.json ]] && grep -q '"version"[[:space:]]*:[[:space:]]*1' /etc/tp27/config.json; then
  TS="$(date +%Y%m%d-%H%M%S)"
  cp -a /etc/tp27/config.json "/etc/tp27/backups/config.v1.${TS}.json"
  while read -r IFACE; do
    [[ -n "$IFACE" ]] || continue
    systemctl disable --now "wg-quick@${IFACE}.service" 2>/dev/null || true
    ip link del "$IFACE" 2>/dev/null || true
    rm -f "/etc/wireguard/${IFACE}.conf"
  done < <(ip -o link show | awk -F': ' '$2 ~ /^tp27exit/ {print $2}')
  rm -rf /etc/tp27/keys/exits
fi

curl -fsSL "${RAW}/bin/tp27.py" -o /usr/local/lib/tp27/tp27.py
curl -fsSL "${RAW}/bin/tp27-link" -o /usr/local/lib/tp27/tp27-link
curl -fsSL "${RAW}/systemd/tp27-link@.service" -o /etc/systemd/system/tp27-link@.service

chmod 755 /usr/local/lib/tp27/tp27.py /usr/local/lib/tp27/tp27-link
ln -sf /usr/local/lib/tp27/tp27.py /usr/local/bin/tp27

cat >/etc/sysctl.d/90-tp27.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv4.conf.all.rp_filter=0
net.ipv4.conf.default.rp_filter=0
net.ipv4.conf.all.src_valid_mark=1
EOF
sysctl --system >/dev/null

/usr/local/bin/tp27 bootstrap --role "$ROLE"
systemctl daemon-reload

echo
echo "============================================================"
echo " TP27 CORE v0.2 INSTALLED"
echo " ROLE: ${ROLE}"
echo "============================================================"
echo "CLI: tp27"
echo
tp27 status
