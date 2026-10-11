#!/usr/bin/env bash
set -Eeuo pipefail

IRAN_IP="${1:-}"
CONTROL_PORT="${2:-22027}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ -n "$IRAN_IP" ]] || { echo "Usage: $0 <IRAN_IP> [CONTROL_PORT]"; exit 2; }

python3 - "$IRAN_IP" "$CONTROL_PORT" <<'PY'
import ipaddress, sys
ipaddress.ip_address(sys.argv[1])
p=int(sys.argv[2])
if not 1 <= p <= 65535:
    raise SystemExit('invalid port')
PY

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y openssh-server iptables

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/tp27-control-ssh-backup-$TS"
install -d -m 700 "$BACKUP"
iptables-save >"$BACKUP/iptables-save.txt"
cp -a /etc/ssh/sshd_config "$BACKUP/sshd_config"
[[ -f /etc/systemd/system/tp27-control-sshd.service ]] && \
  cp -a /etc/systemd/system/tp27-control-sshd.service "$BACKUP/" || true

install -d -m 755 /usr/local/lib/tp27

cat >/usr/local/lib/tp27/control-firewall.sh <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
IRAN_IP='$IRAN_IP'
PORT='$CONTROL_PORT'
CHAIN='TP27_SSH_IN'

iptables -N \"\$CHAIN\" 2>/dev/null || true
iptables -F \"\$CHAIN\"
iptables -A \"\$CHAIN\" -p tcp -s \"\$IRAN_IP\" --dport \"\$PORT\" -j ACCEPT
iptables -A \"\$CHAIN\" -p tcp --dport \"\$PORT\" -j DROP
iptables -C INPUT -p tcp --dport \"\$PORT\" -j \"\$CHAIN\" 2>/dev/null || \
  iptables -I INPUT 1 -p tcp --dport \"\$PORT\" -j \"\$CHAIN\"
EOF
chmod 700 /usr/local/lib/tp27/control-firewall.sh

cat >/etc/systemd/system/tp27-control-firewall.service <<'EOF'
[Unit]
Description=TP27 Control SSH firewall
After=network-pre.target
Before=tp27-control-sshd.service

[Service]
Type=oneshot
ExecStart=/usr/local/lib/tp27/control-firewall.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/tp27-control-sshd.service <<EOF
[Unit]
Description=TP27 isolated SSH control service
After=network-online.target tp27-control-firewall.service
Wants=network-online.target
Requires=tp27-control-firewall.service

[Service]
Type=simple
RuntimeDirectory=tp27-sshd
RuntimeDirectoryMode=0755
ExecStartPre=/usr/sbin/sshd -t -f /etc/ssh/sshd_config
ExecStart=/usr/sbin/sshd -D -e -f /etc/ssh/sshd_config -p $CONTROL_PORT -o PidFile=/run/tp27-sshd/sshd.pid -o PermitRootLogin=yes -o PasswordAuthentication=yes -o KbdInteractiveAuthentication=no -o PubkeyAuthentication=yes -o GSSAPIAuthentication=no -o MaxStartups=50:30:200 -o LoginGraceTime=30
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now tp27-control-firewall.service
systemctl enable --now tp27-control-sshd.service
sleep 1

if ! systemctl is-active --quiet tp27-control-sshd.service; then
  echo "ERROR: TP27 control sshd failed"
  systemctl status tp27-control-sshd.service --no-pager -l || true
  exit 3
fi

if ! ss -lntp | grep -q ":${CONTROL_PORT} "; then
  echo "ERROR: control port is not listening: $CONTROL_PORT"
  ss -lntp | grep sshd || true
  exit 4
fi

echo "============================================================"
echo " TP27 CONTROL SSH READY"
echo " IRAN IP: $IRAN_IP"
echo " PORT: $CONTROL_PORT"
echo " ORIGINAL SSH PORT 22: UNCHANGED"
echo " BACKUP: $BACKUP"
echo "============================================================"
