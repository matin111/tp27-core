#!/usr/bin/env bash
set -Eeuo pipefail

ACTION="${1:-}"
RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"
INSTALL_URL="https://raw.githubusercontent.com/matin111/tp27-core/main/install.sh"

usage() {
  cat <<'EOF'
TP27 unified installer

Iran controller + panel:
  curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/install.sh | bash -s -- iran

Exit agent:
  curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/install.sh | bash -s -- agent <CONTROLLER_URL> <ENROLL_TOKEN> [--plain <PLAIN_URL>] [--insecure]

Internal/core-only exit install:
  install.sh exit
EOF
}

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }

if [[ "$ACTION" == "agent" ]]; then
  CONTROLLER="${2:-}"
  TOKEN="${3:-}"
  [[ -n "$CONTROLLER" && -n "$TOKEN" ]] || { usage; exit 2; }
  shift 3
  MODE=""
  PLAIN_URL=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --insecure)
        MODE="--insecure"; shift ;;
      --plain)
        [[ $# -ge 2 ]] || { echo "ERROR: --plain requires URL"; exit 2; }
        PLAIN_URL="$2"; shift 2 ;;
      *)
        echo "ERROR: unknown agent option: $1"; usage; exit 2 ;;
    esac
  done
  TMP="$(mktemp /tmp/tp27-exit-agent-install.XXXXXX.sh)"
  trap 'rm -f "$TMP"' EXIT
  curl -fsSL "$RAW/install-exit-agent.sh" -o "$TMP"
  chmod 700 "$TMP"
  bash "$TMP" "$CONTROLLER" "$TOKEN" "$MODE" "$PLAIN_URL"
  exit $?
fi

ROLE="$ACTION"
[[ "$ROLE" == "iran" || "$ROLE" == "exit" ]] || { usage; exit 2; }

wait_for_apt() {
  command -v fuser >/dev/null 2>&1 || return 0
  for _ in $(seq 1 90); do
    if ! fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend >/dev/null 2>&1; then
      return 0
    fi
    echo "Waiting for apt/dpkg lock..."
    sleep 2
  done
  echo "ERROR: apt remained busy for too long"
  return 1
}

export DEBIAN_FRONTEND=noninteractive

echo "============================================================"
echo " TP27 UNIFIED INSTALLER"
echo " ROLE: $ROLE"
echo "============================================================"

wait_for_apt
apt-get update
apt-get install -y socat openssl iproute2 iptables jq curl ca-certificates python3 openssh-client iputils-ping kmod

install -d -m 700 /etc/tp27 /etc/tp27/certs /etc/tp27/exits /etc/tp27/backups
install -d -m 755 /var/lib/tp27 /var/log/tp27 /usr/local/lib/tp27 /usr/local/bin

STAMP="$(date +%Y%m%d-%H%M%S)"
if [[ -f /etc/tp27/config.json ]]; then
  cp -a /etc/tp27/config.json "/etc/tp27/backups/config.${STAMP}.json"

  if jq -e '.version == 1' /etc/tp27/config.json >/dev/null 2>&1; then
    echo "Migrating old TP27 prototype..."
    systemctl disable --now tp27-agent.service 2>/dev/null || true

    while read -r IFACE; do
      [[ "$IFACE" == tp27* ]] || continue
      systemctl disable --now "wg-quick@${IFACE}.service" 2>/dev/null || true
      ip link del "$IFACE" 2>/dev/null || true
      rm -f "/etc/wireguard/${IFACE}.conf"
    done < <(jq -r '(.exits[]?.interface // empty), (.exit.interface // empty)' /etc/tp27/config.json | sort -u)

    rm -rf /etc/tp27/keys/exits
    rm -f /etc/tp27/exits/*.json 2>/dev/null || true
  fi
fi

systemctl disable --now tp27-agent.service 2>/dev/null || true

fetch() {
  local src="$1" dst="$2"
  curl -fsSL --connect-timeout 8 --max-time 90 "$RAW/$src" -o "$dst"
}

fetch bin/tp27-v2.py /usr/local/lib/tp27/tp27.py
fetch bin/tp27-link-mtls /usr/local/lib/tp27/tp27-link
fetch bin/tp27-link-preflight /usr/local/lib/tp27/tp27-link-preflight
fetch systemd/tp27-link@.service /etc/systemd/system/tp27-link@.service
fetch bin/tp27-framed-tls.py /usr/local/lib/tp27/tp27-framed-tls.py
fetch systemd/tp27-framed@.service /etc/systemd/system/tp27-framed@.service
chmod 755 \
  /usr/local/lib/tp27/tp27.py \
  /usr/local/lib/tp27/tp27-link \
  /usr/local/lib/tp27/tp27-link-preflight \
  /usr/local/lib/tp27/tp27-framed-tls.py

python3 -m py_compile \
  /usr/local/lib/tp27/tp27.py \
  /usr/local/lib/tp27/tp27-link-preflight \
  /usr/local/lib/tp27/tp27-framed-tls.py

ln -sfn /usr/local/lib/tp27/tp27.py /usr/local/bin/tp27
rm -f /usr/local/bin/tp27ctl
ln -sfn /usr/local/lib/tp27/tp27.py /usr/local/bin/tp27ctl

# Do not change global/default rp_filter. TP27 disables rp_filter only on its
# own tunnel interfaces at runtime. This avoids changing unrelated production
# VPN/network behavior on the host.
cat >/etc/sysctl.d/90-tp27.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv4.conf.all.src_valid_mark=1
EOF
sysctl -q -w net.ipv4.ip_forward=1
sysctl -q -w net.ipv4.conf.all.src_valid_mark=1 2>/dev/null || true

systemctl daemon-reload
tp27 bootstrap --role "$ROLE"

echo
echo "TP27 core installed for role=$ROLE"
tp27 status

if [[ "$ROLE" == "iran" && "${TP27_CORE_ONLY:-0}" != "1" ]]; then
  echo
  echo "============================================================"
  echo " INSTALLING / UPGRADING TP27 PANEL"
  echo "============================================================"
  PANEL_TMP="$(mktemp /tmp/tp27-panel-install.XXXXXX.sh)"
  trap 'rm -f "$PANEL_TMP"' EXIT
  curl -fsSL "$RAW/install-panel.sh" -o "$PANEL_TMP"
  chmod 700 "$PANEL_TMP"
  bash "$PANEL_TMP"
  rm -f "$PANEL_TMP"
  trap - EXIT

  PANEL_IP="$(cat /etc/tp27/panel-ip 2>/dev/null || true)"
  TOKEN="$(cat /etc/tp27/agent-enroll.token 2>/dev/null || true)"
  PLAIN_PORT="$(awk -F= '$1=="TP27_PLAIN_PORT"{print $2}' /etc/tp27/plain-control.env 2>/dev/null | tail -1 || true)"

  echo
  echo "============================================================"
  echo " TP27 READY"
  [[ -n "$PANEL_IP" ]] && echo " Panel: https://$PANEL_IP:8787"
  echo " Credentials: /root/tp27-panel-credentials.txt"
  echo "============================================================"
  if [[ -n "$PANEL_IP" && -n "$TOKEN" ]]; then
    echo "Run this SAME installer link on every Exit server:"
    if [[ -n "$PLAIN_PORT" ]]; then
      printf 'curl -fsSL %q | bash -s -- agent %q %q --plain %q\n' \
        "$INSTALL_URL" "https://$PANEL_IP:8787" "$TOKEN" "http://$PANEL_IP:$PLAIN_PORT"
    else
      printf 'curl -fsSL %q | bash -s -- agent %q %q\n' \
        "$INSTALL_URL" "https://$PANEL_IP:8787" "$TOKEN"
    fi
  fi
  echo "============================================================"
fi
