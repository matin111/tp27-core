#!/usr/bin/env bash
set -Eeuo pipefail

ROLE="${1:-}"
LOCAL_PUBLIC="${2:-}"
REMOTE_PUBLIC="${3:-}"
ID="${4:-2tunl}"
LOCAL_TUN="${5:-}"
PHT_PORT="${6:-80}"
INTERNAL_PORT="${7:-18500}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }
[[ "$ROLE" == "iran" || "$ROLE" == "exit" ]] || { echo "ERROR: role must be iran or exit" >&2; exit 2; }
command -v gost >/dev/null 2>&1 || { echo "ERROR: /usr/local/bin/gost missing" >&2; exit 3; }
ip -4 addr show | grep -qw "$LOCAL_PUBLIC" || { echo "ERROR: wrong server; expected $LOCAL_PUBLIC" >&2; exit 4; }

IFACE="t27$(printf '%s' "$ID" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')"
IFACE="${IFACE:0:15}"
SERVICE="tp27-gost-pht-${ID}.service"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/etc/tp27/backups/emergency-pht-${ID}-${STAMP}"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"

systemctl cat "$SERVICE" >"$BACKUP/pht-unit.txt" 2>/dev/null || true
systemctl cat "tp27-link@${ID}.service" >"$BACKUP/gre-unit.txt" 2>/dev/null || true
iptables-save >"$BACKUP/iptables-save.txt" 2>/dev/null || true

systemctl stop "$SERVICE" 2>/dev/null || true
systemctl disable --now "tp27-link@${ID}.service" >/dev/null 2>&1 || true
ip link del "$IFACE" 2>/dev/null || true

if [[ "$ROLE" == "iran" ]]; then
  ss -lntp | grep -q ":${PHT_PORT} " && {
    echo "ERROR: TCP/${PHT_PORT} busy" >&2
    ss -lntp | grep ":${PHT_PORT} " || true
    exit 5
  }

  cat >"/etc/systemd/system/$SERVICE" <<EOF
[Unit]
Description=TP27 GOST PHT TCP Fallback (${ID}, Iran)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/gost "-L=tun://:${INTERNAL_PORT}?net=${LOCAL_TUN}&name=${IFACE}&mtu=1300" "-L=relay+pht://${LOCAL_PUBLIC}:${PHT_PORT}?bind=true"
Restart=always
RestartSec=2
User=root
Group=root
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
else
  cat >"/etc/systemd/system/$SERVICE" <<EOF
[Unit]
Description=TP27 GOST PHT TCP Fallback (${ID}, Exit)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/gost "-L=tun://:0/:${INTERNAL_PORT}?net=${LOCAL_TUN}&name=${IFACE}&mtu=1300&keepAlive=true&ttl=5s" "-F=relay+pht://${REMOTE_PUBLIC}:${PHT_PORT}"
Restart=always
RestartSec=2
User=root
Group=root
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
fi

systemd-analyze verify "/etc/systemd/system/$SERVICE"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"

for _ in $(seq 1 30); do
  ip -4 addr show dev "$IFACE" 2>/dev/null | grep -Fq "inet ${LOCAL_TUN%/*}/" && break
  sleep 0.5
done

systemctl is-active "$SERVICE" || true
ip -br -4 addr show dev "$IFACE" 2>/dev/null || true
journalctl -u "$SERVICE" -n 30 --no-pager -o cat || true

echo "BACKUP=$BACKUP"
