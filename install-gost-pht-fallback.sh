#!/usr/bin/env bash
set -Eeuo pipefail

ROLE="${1:-}"
LOCAL_PUBLIC="${2:-}"
REMOTE_PUBLIC="${3:-}"
ID="${4:-}"
LOCAL_TUN="${5:-}"
PEER_TUN="${6:-}"
PHT_PORT="${7:-80}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }
[[ "$ROLE" == "iran" || "$ROLE" == "exit" ]] || { echo "ERROR: role must be iran or exit" >&2; exit 2; }
[[ "$ID" =~ ^[A-Za-z0-9_-]{1,24}$ ]] || { echo "ERROR: invalid id" >&2; exit 3; }
[[ "$PHT_PORT" =~ ^[0-9]+$ ]] && (( PHT_PORT >= 1 && PHT_PORT <= 65535 )) || { echo "ERROR: invalid PHT port" >&2; exit 4; }

python3 - "$LOCAL_PUBLIC" "$REMOTE_PUBLIC" "$LOCAL_TUN" "$PEER_TUN" <<'PY'
import ipaddress, sys
for value in sys.argv[1:3]:
    a=ipaddress.ip_address(value)
    assert a.version == 4
iface=ipaddress.ip_interface(sys.argv[3])
assert iface.version == 4
peer=ipaddress.ip_address(sys.argv[4])
assert peer.version == 4
assert peer in iface.network
assert peer != iface.ip
PY

ip -4 addr show | grep -qw "$LOCAL_PUBLIC" || {
  echo "ERROR: wrong server; expected local IPv4 $LOCAL_PUBLIC" >&2
  exit 5
}

IFACE="t27$(printf '%s' "$ID" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')"
IFACE="${IFACE:0:15}"
INTERNAL_PORT=$((18420 + (PHT_PORT % 1000)))
SERVICE="tp27-gost-pht-${ID}.service"
OLD_SERVICE="tp27-link@${ID}.service"
ROOT="/etc/tp27"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$ROOT/backups/gost-pht-${ID}-${STAMP}"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"

if [[ "$ROLE" == "iran" ]]; then
  if ss -lntp | awk -v p=":${PHT_PORT}" '$4 ~ p"$" {print}' | grep -q .; then
    if ! systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
      echo "ERROR: TCP/${PHT_PORT} is already in use; nothing changed" >&2
      ss -lntp | grep ":${PHT_PORT} " || true
      exit 6
    fi
  fi
fi

# Backup only TP27 state touched by this fallback.
systemctl cat "$OLD_SERVICE" >"$BACKUP/old-service.txt" 2>/dev/null || true
systemctl is-enabled "$OLD_SERVICE" >"$BACKUP/old-service-enabled.txt" 2>/dev/null || true
systemctl is-active "$OLD_SERVICE" >"$BACKUP/old-service-active.txt" 2>/dev/null || true
[[ -f "$ROOT/exits/${ID}.json" ]] && cp -a "$ROOT/exits/${ID}.json" "$BACKUP/" || true
iptables-save >"$BACKUP/iptables-save.txt" 2>/dev/null || true
[[ -f "/etc/systemd/system/$SERVICE" ]] && cp -a "/etc/systemd/system/$SERVICE" "$BACKUP/" || true

# Install a pinned stable GOST build with checksum verification.
GOST_VERSION="3.3.0"
GOST_ARCHIVE="gost_${GOST_VERSION}_linux_amd64.tar.gz"
GOST_URL="https://github.com/go-gost/gost/releases/download/v${GOST_VERSION}/${GOST_ARCHIVE}"
GOST_SHA256="676fb7f78d267b6ae73df719c0c7f2b565dde7147da935cfafbc1e1da558b6d5"

need_gost=1
if command -v gost >/dev/null 2>&1; then
  if gost -V 2>&1 | grep -q "${GOST_VERSION}"; then
    need_gost=0
  fi
fi
if (( need_gost )); then
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -fL --retry 3 --connect-timeout 10 "$GOST_URL" -o "$TMP/$GOST_ARCHIVE"
  echo "$GOST_SHA256  $TMP/$GOST_ARCHIVE" | sha256sum -c -
  tar -xzf "$TMP/$GOST_ARCHIVE" -C "$TMP"
  install -m 0755 "$TMP/gost" /usr/local/bin/gost
  rm -rf "$TMP"
  trap - EXIT
fi

# Stop only this TP27 link instance. Production VPN daemons are untouched.
systemctl disable --now "$OLD_SERVICE" >/dev/null 2>&1 || true
sleep 1
ip link del "$IFACE" 2>/dev/null || true

# Restrict the public PHT listener to its paired exit IP only.
if [[ "$ROLE" == "iran" ]]; then
  CHAIN="TP27_PHT_IN"
  iptables -w 5 -t filter -N "$CHAIN" 2>/dev/null || true
  iptables -w 5 -t filter -C INPUT -j "$CHAIN" 2>/dev/null || iptables -w 5 -t filter -I INPUT 1 -j "$CHAIN"
  # Remove only rules for this listener socket before rebuilding them.
  while iptables -w 5 -t filter -C "$CHAIN" -p tcp -s "$REMOTE_PUBLIC/32" -d "$LOCAL_PUBLIC/32" --dport "$PHT_PORT" -j ACCEPT 2>/dev/null; do
    iptables -w 5 -t filter -D "$CHAIN" -p tcp -s "$REMOTE_PUBLIC/32" -d "$LOCAL_PUBLIC/32" --dport "$PHT_PORT" -j ACCEPT || break
  done
  while iptables -w 5 -t filter -C "$CHAIN" -p tcp -d "$LOCAL_PUBLIC/32" --dport "$PHT_PORT" -j DROP 2>/dev/null; do
    iptables -w 5 -t filter -D "$CHAIN" -p tcp -d "$LOCAL_PUBLIC/32" --dport "$PHT_PORT" -j DROP || break
  done
  iptables -w 5 -t filter -A "$CHAIN" -p tcp -s "$REMOTE_PUBLIC/32" -d "$LOCAL_PUBLIC/32" --dport "$PHT_PORT" -j ACCEPT
  iptables -w 5 -t filter -A "$CHAIN" -p tcp -d "$LOCAL_PUBLIC/32" --dport "$PHT_PORT" -j DROP
fi

UNIT="/etc/systemd/system/$SERVICE"
if [[ "$ROLE" == "iran" ]]; then
  cat >"$UNIT" <<EOF
[Unit]
Description=TP27 GOST PHT TCP Fallback (${ID}, Iran)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/gost -L=tun://127.0.0.1:${INTERNAL_PORT}?net=${LOCAL_TUN}\&name=${IFACE}\&mtu=1300 -L=relay+pht://${LOCAL_PUBLIC}:${PHT_PORT}?bind=true
Restart=always
RestartSec=2
User=root
Group=root
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
else
  cat >"$UNIT" <<EOF
[Unit]
Description=TP27 GOST PHT TCP Fallback (${ID}, Exit)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/gost -L=tun://:0/127.0.0.1:${INTERNAL_PORT}?net=${LOCAL_TUN}\&name=${IFACE}\&mtu=1300\&keepAlive=true\&ttl=5s -F=relay+pht://${REMOTE_PUBLIC}:${PHT_PORT}
Restart=always
RestartSec=2
User=root
Group=root
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
fi

systemctl daemon-reload
systemctl enable --now "$SERVICE"

for _ in $(seq 1 20); do
  ip link show "$IFACE" >/dev/null 2>&1 && break
  sleep 0.5
done

if ! systemctl is-active --quiet "$SERVICE"; then
  systemctl status "$SERVICE" --no-pager -l || true
  journalctl -u "$SERVICE" -n 80 --no-pager || true
  echo "ERROR: fallback service failed" >&2
  exit 7
fi

if ! ip link show "$IFACE" >/dev/null 2>&1; then
  systemctl status "$SERVICE" --no-pager -l || true
  journalctl -u "$SERVICE" -n 80 --no-pager || true
  echo "ERROR: GOST TUN interface $IFACE was not created" >&2
  exit 8
fi

# Match the existing TP27 interface characteristics used by the mapping layer.
ip link set dev "$IFACE" mtu 1300 txqueuelen 1000 2>/dev/null || true
sysctl -q -w "net.ipv4.conf.${IFACE}.rp_filter=0" 2>/dev/null || true

sleep 2

echo "============================================================"
echo " TP27 GOST PHT FALLBACK INSTALLED"
echo " Role: $ROLE"
echo " ID: $ID"
echo " Interface: $IFACE"
echo " Local tunnel: $LOCAL_TUN"
echo " Peer tunnel: $PEER_TUN"
echo " PHT TCP port: $PHT_PORT"
echo " Service: $SERVICE"
echo " Backup: $BACKUP"
echo "============================================================"
systemctl is-active "$SERVICE" || true
ip -br -4 addr show dev "$IFACE" || true
ip -s link show dev "$IFACE" || true

echo
echo "===== PEER TEST ====="
if ping -I "$IFACE" -c 3 -W 2 "$PEER_TUN"; then
  echo "TP27_PHT_PEER_OK"
else
  echo "TP27_PHT_WAITING_OR_FAILED"
fi
