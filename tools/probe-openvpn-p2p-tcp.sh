#!/usr/bin/env bash
set -Eeuo pipefail

ROLE="${1:-}"
EXIT_IP="152.89.253.169"
IRAN_IP="193.39.9.220"
PORT="29443"
PROBE_SECONDS="${TP27_PROBE_SECONDS:-900}"
EXIT_TUN="t27ovE"
IRAN_TUN="t27ovI"
EXIT_TUN_IP="10.254.252.1"
IRAN_TUN_IP="10.254.252.2"
KEY="/tmp/tp27-openvpn-probe.key"
CHAIN="TP27_OVPN_PROBE"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ "$ROLE" == "exit" || "$ROLE" == "iran" ]] || {
  echo "Usage: $0 {exit|iran}"
  exit 2
}
[[ "$PROBE_SECONDS" =~ ^[0-9]+$ ]] && (( PROBE_SECONDS >= 60 && PROBE_SECONDS <= 3600 )) || {
  echo "ERROR: TP27_PROBE_SECONDS must be 60..3600"
  exit 2
}
command -v openvpn >/dev/null 2>&1 || {
  echo "ERROR: openvpn is not installed"
  exit 3
}

write_probe_key() {
  python3 - "$KEY" <<'PY'
from pathlib import Path
import hashlib, sys
p=Path(sys.argv[1])
seed=b'TP27-OPENVPN-TCP-PROBE-ONLY|193.39.9.220|152.89.253.169|2026-10-12'
raw=b''.join(hashlib.sha256(seed + bytes([i])).digest() for i in range(8))
hexdata=raw.hex()
lines=[hexdata[i:i+32] for i in range(0,len(hexdata),32)]
text=(
    '#\n# TP27 TEST-ONLY deterministic 2048-bit OpenVPN static key\n'
    '# NEVER use this key for production.\n#\n'
    '-----BEGIN OpenVPN Static key V1-----\n' +
    '\n'.join(lines) + '\n' +
    '-----END OpenVPN Static key V1-----\n'
)
p.write_text(text, encoding='ascii')
p.chmod(0o600)
PY
}

maybe_allow_static_crypto() {
  local version minor
  version="$(openvpn --version 2>/dev/null | head -1 | awk '{print $2}')"
  minor="$(printf '%s' "$version" | awk -F. '{print $2+0}')"
  if [[ "$minor" -ge 7 ]]; then
    echo "allow-deprecated-insecure-static-crypto"
  fi
}

cleanup_probe_chain() {
  if command -v iptables >/dev/null 2>&1; then
    while iptables -w 5 -C INPUT -j "$CHAIN" >/dev/null 2>&1; do
      iptables -w 5 -D INPUT -j "$CHAIN" >/dev/null 2>&1 || break
    done
    iptables -w 5 -F "$CHAIN" >/dev/null 2>&1 || true
    iptables -w 5 -X "$CHAIN" >/dev/null 2>&1 || true
  fi
}

cleanup_old_gost_probe_exit() {
  local pid=""
  pid="$(cat /tmp/tp27-gost-probe.pid 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  pkill -f '/usr/local/bin/tp27-gost -C /tmp/tp27-gost-direct-server.json' 2>/dev/null || true
  ip link del t27probeE 2>/dev/null || true
  if command -v iptables >/dev/null 2>&1; then
    while iptables -w 5 -C INPUT -p tcp -s "$IRAN_IP/32" --dport 28443 -m comment --comment TP27-GOST-PROBE -j ACCEPT >/dev/null 2>&1; do
      iptables -w 5 -D INPUT -p tcp -s "$IRAN_IP/32" --dport 28443 -m comment --comment TP27-GOST-PROBE -j ACCEPT >/dev/null 2>&1 || break
    done
  fi
}

write_probe_key
STATIC_COMPAT="$(maybe_allow_static_crypto)"

if [[ "$ROLE" == "exit" ]]; then
  ip -4 addr show | grep -qw "$EXIT_IP" || { echo "ERROR: wrong server; expected $EXIT_IP"; exit 4; }
  cleanup_old_gost_probe_exit

  ss -lntH | awk '{print $4}' | grep -qE "(:|\\])${PORT}$" && {
    echo "ERROR: TCP/$PORT is already occupied"
    exit 5
  }

  BACKUP="/tmp/tp27-ovpn-probe-iptables-$(date +%Y%m%d-%H%M%S).save"
  command -v iptables-save >/dev/null 2>&1 && iptables-save >"$BACKUP" || true

  if command -v iptables >/dev/null 2>&1; then
    cleanup_probe_chain
    iptables -w 5 -N "$CHAIN"
    iptables -w 5 -A "$CHAIN" -p tcp -s "$IRAN_IP/32" -d "$EXIT_IP/32" --dport "$PORT" -j ACCEPT
    iptables -w 5 -A "$CHAIN" -j RETURN
    iptables -w 5 -I INPUT 1 -j "$CHAIN"
  fi

  CONF="/tmp/tp27-openvpn-probe-exit.conf"
  LOG="/tmp/tp27-openvpn-probe-exit.log"
  PIDFILE="/tmp/tp27-openvpn-probe-exit.pid"

  cat >"$CONF" <<EOF
local $EXIT_IP
port $PORT
proto tcp-server
dev $EXIT_TUN
dev-type tun
topology p2p
ifconfig $EXIT_TUN_IP $IRAN_TUN_IP
secret $KEY
cipher AES-256-CBC
auth SHA256
tun-mtu 1300
persist-key
persist-tun
ping 5
ping-restart 25
ping-timer-rem
verb 4
$STATIC_COMPAT
EOF

  rm -f "$LOG"
  nohup timeout "$PROBE_SECONDS" openvpn --config "$CONF" >"$LOG" 2>&1 &
  echo $! >"$PIDFILE"

  (
    sleep "$((PROBE_SECONDS + 5))"
    cleanup_probe_chain
    ip link del "$EXIT_TUN" 2>/dev/null || true
    rm -f "$CONF" "$KEY" "$PIDFILE"
  ) >/dev/null 2>&1 &

  sleep 3
  echo "===== OPENVPN EXIT PROBE ====="
  echo "VERSION=$(openvpn --version | head -1)"
  echo "IPTABLES_BACKUP=$BACKUP"
  echo "PROBE_SECONDS=$PROBE_SECONDS"
  ss -lntp | grep ":$PORT" || true
  ip -br -4 addr show "$EXIT_TUN" 2>/dev/null || true
  echo "===== EXIT LOG ====="
  tail -60 "$LOG" || true
  echo "===== READY ====="
  echo "Run the same pinned script with argument 'iran' on $IRAN_IP within $PROBE_SECONDS seconds."
  exit 0
fi

ip -4 addr show | grep -qw "$IRAN_IP" || { echo "ERROR: wrong server; expected $IRAN_IP"; exit 4; }

CONF="/tmp/tp27-openvpn-probe-iran.conf"
LOG="/tmp/tp27-openvpn-probe-iran.log"
rm -f "$LOG"
ip link del "$IRAN_TUN" 2>/dev/null || true

cat >"$CONF" <<EOF
remote $EXIT_IP $PORT
proto tcp-client
nobind
dev $IRAN_TUN
dev-type tun
topology p2p
ifconfig $IRAN_TUN_IP $EXIT_TUN_IP
secret $KEY
cipher AES-256-CBC
auth SHA256
tun-mtu 1300
persist-key
persist-tun
connect-retry 1 3
connect-retry-max 3
ping 5
ping-restart 20
verb 4
$STATIC_COMPAT
EOF

echo "===== TCP PATH ====="
if timeout 4 bash -c "</dev/tcp/$EXIT_IP/$PORT"; then
  echo "TCP_${PORT}=OK"
else
  echo "TCP_${PORT}=FAILED"
  rm -f "$CONF" "$KEY"
  exit 6
fi

timeout 50 openvpn --config "$CONF" >"$LOG" 2>&1 &
PID=$!

cleanup_client() {
  kill "$PID" 2>/dev/null || true
  wait "$PID" 2>/dev/null || true
  ip link del "$IRAN_TUN" 2>/dev/null || true
  rm -f "$CONF" "$KEY"
}
trap cleanup_client EXIT

sleep 6

echo
echo "===== IRAN OPENVPN INTERFACE ====="
ip -br -4 addr show "$IRAN_TUN" 2>/dev/null || true
ip -4 route show dev "$IRAN_TUN" 2>/dev/null || true

echo
echo "===== OPENVPN P2P PING ====="
ping -I "$IRAN_TUN" -c 10 -W 2 "$EXIT_TUN_IP" || true

echo
echo "===== IRAN OPENVPN LOG ====="
tail -100 "$LOG" || true
