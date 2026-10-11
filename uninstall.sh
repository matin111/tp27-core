#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: run as root"
  exit 1
fi

echo "TP27 standalone uninstall"

systemctl disable --now tp27-agent.service 2>/dev/null || true
rm -f /etc/systemd/system/tp27-agent.service

if [[ -f /etc/tp27/config.json ]]; then
  mapfile -t ifaces < <(python3 - <<'PY'
import json
p="/etc/tp27/config.json"
try:
    c=json.load(open(p))
except Exception:
    c={}
for e in c.get("exits", []):
    if e.get("interface"):
        print(e["interface"])
if c.get("exit", {}).get("interface"):
    print(c["exit"]["interface"])
PY
)
  for i in "${ifaces[@]:-}"; do
    [[ -n "${i}" ]] || continue
    systemctl disable --now "wg-quick@${i}.service" 2>/dev/null || true
    ip link del "${i}" 2>/dev/null || true
    rm -f "/etc/wireguard/${i}.conf"
  done
fi

for table in filter nat mangle; do
  iptables -t "${table}" -D PREROUTING -j TP27_PRE 2>/dev/null || true
  iptables -t "${table}" -D POSTROUTING -j TP27_POST 2>/dev/null || true
  iptables -t "${table}" -D FORWARD -j TP27_FWD 2>/dev/null || true
done

iptables -t nat -F TP27_PRE 2>/dev/null || true
iptables -t nat -F TP27_POST 2>/dev/null || true
iptables -t filter -F TP27_FWD 2>/dev/null || true
iptables -t mangle -F TP27_FWD 2>/dev/null || true

iptables -t nat -X TP27_PRE 2>/dev/null || true
iptables -t nat -X TP27_POST 2>/dev/null || true
iptables -t filter -X TP27_FWD 2>/dev/null || true
iptables -t mangle -X TP27_FWD 2>/dev/null || true

rm -f /usr/local/bin/tp27ctl
rm -rf /usr/local/lib/tp27
rm -f /etc/sysctl.d/90-tp27.conf

systemctl daemon-reload

echo "TP27 binaries/services removed."
echo "Configuration is intentionally kept at /etc/tp27 for rollback."
