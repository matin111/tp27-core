#!/usr/bin/env bash
set -Eeuo pipefail

ROLE="${1:-}"
REPO_RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: run as root"
  exit 1
fi

if [[ "${ROLE}" != "iran" && "${ROLE}" != "exit" ]]; then
  echo "Usage: install.sh iran|exit"
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive

echo "============================================================"
echo " TP27 CORE INSTALLER"
echo " ROLE: ${ROLE}"
echo "============================================================"

apt-get update
apt-get install -y \
  wireguard-tools \
  iproute2 \
  iptables \
  conntrack \
  curl \
  jq \
  iputils-ping \
  tcpdump \
  ca-certificates \
  python3

install -d -m 700 /etc/tp27
install -d -m 700 /etc/tp27/keys
install -d -m 755 /var/lib/tp27
install -d -m 755 /var/log/tp27
install -d -m 755 /usr/local/lib/tp27
install -d -m 755 /usr/local/bin

if [[ -f /etc/tp27/config.json ]]; then
  cp -a /etc/tp27/config.json "/etc/tp27/config.json.backup.$(date +%Y%m%d-%H%M%S)"
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"

if [[ -n "${SCRIPT_DIR}" && -f "${SCRIPT_DIR}/bin/tp27ctl.py" && -f "${SCRIPT_DIR}/bin/tp27-agent.py" ]]; then
  echo "Install source: local repository"
  install -m 755 "${SCRIPT_DIR}/bin/tp27ctl.py" /usr/local/lib/tp27/tp27ctl.py
  install -m 755 "${SCRIPT_DIR}/bin/tp27-agent.py" /usr/local/lib/tp27/tp27-agent.py
  install -m 644 "${SCRIPT_DIR}/systemd/tp27-agent.service" /etc/systemd/system/tp27-agent.service
else
  echo "Install source: ${REPO_RAW}"
  curl -fsSL "${REPO_RAW}/bin/tp27ctl.py" -o /usr/local/lib/tp27/tp27ctl.py
  curl -fsSL "${REPO_RAW}/bin/tp27-agent.py" -o /usr/local/lib/tp27/tp27-agent.py
  curl -fsSL "${REPO_RAW}/systemd/tp27-agent.service" -o /etc/systemd/system/tp27-agent.service
fi

chmod 755 /usr/local/lib/tp27/tp27ctl.py
chmod 755 /usr/local/lib/tp27/tp27-agent.py

cat >/usr/local/bin/tp27ctl <<'EOF'
#!/usr/bin/env bash
exec /usr/bin/python3 /usr/local/lib/tp27/tp27ctl.py "$@"
EOF
chmod 755 /usr/local/bin/tp27ctl

cat >/etc/sysctl.d/90-tp27.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv4.conf.all.rp_filter=0
net.ipv4.conf.default.rp_filter=0
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.default.accept_redirects=0
net.ipv4.conf.all.send_redirects=0
net.ipv4.conf.default.send_redirects=0
net.ipv4.conf.all.src_valid_mark=1
EOF

sysctl --system >/dev/null

/usr/local/bin/tp27ctl bootstrap --role "${ROLE}"

systemctl daemon-reload
systemctl enable --now tp27-agent.service

echo
echo "============================================================"
echo " TP27 INSTALLED"
echo "============================================================"
echo "Role: ${ROLE}"
echo "Config: /etc/tp27/config.json"
echo "CLI: tp27ctl"
echo
tp27ctl status || true
