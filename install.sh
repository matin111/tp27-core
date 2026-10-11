#!/usr/bin/env bash
set -Eeuo pipefail

ROLE="${1:-}"
RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ "$ROLE" == "iran" || "$ROLE" == "exit" ]] || { echo "Usage: install.sh iran|exit"; exit 2; }

export DEBIAN_FRONTEND=noninteractive

echo "============================================================"
echo " TP27 CORE v0.2"
echo " ROLE: $ROLE"
echo "============================================================"

apt-get update
apt-get install -y socat openssl iproute2 iptables jq curl ca-certificates python3 openssh-client iputils-ping

install -d -m 700 /etc/tp27 /etc/tp27/certs /etc/tp27/exits /etc/tp27/backups
install -d -m 755 /var/lib/tp27 /var/log/tp27 /usr/local/lib/tp27 /usr/local/bin

if [[ -f /etc/tp27/config.json ]]; then
  cp -a /etc/tp27/config.json "/etc/tp27/backups/config.$(date +%Y%m%d-%H%M%S).json"
fi

# Stop only the obsolete TP27 v0.1 monitor; unrelated VPN services are untouched.
systemctl disable --now tp27-agent.service 2>/dev/null || true

curl -fsSL "$RAW/bin/tp27-v2.py" -o /usr/local/lib/tp27/tp27.py
curl -fsSL "$RAW/bin/tp27-link-mtls" -o /usr/local/lib/tp27/tp27-link
curl -fsSL "$RAW/systemd/tp27-link@.service" -o /etc/systemd/system/tp27-link@.service
chmod 755 /usr/local/lib/tp27/tp27.py /usr/local/lib/tp27/tp27-link

ln -sfn /usr/local/lib/tp27/tp27.py /usr/local/bin/tp27
rm -f /usr/local/bin/tp27ctl
ln -sfn /usr/local/lib/tp27/tp27.py /usr/local/bin/tp27ctl

cat >/etc/sysctl.d/90-tp27.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv4.conf.all.rp_filter=0
net.ipv4.conf.default.rp_filter=0
net.ipv4.conf.all.src_valid_mark=1
EOF
sysctl --system >/dev/null

systemctl daemon-reload
tp27 bootstrap --role "$ROLE"

echo
echo "TP27 v0.2 installed. CLI: tp27"
tp27 status
