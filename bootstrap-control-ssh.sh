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

# This bootstrap belongs on the FOREIGN/EXIT server only.
CURRENT_IP="$(curl -4 -fsS --max-time 5 https://ifconfig.me 2>/dev/null || true)"
if [[ -z "$CURRENT_IP" ]]; then
  CURRENT_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
fi
if [[ "$CURRENT_IP" == "$IRAN_IP" ]]; then
  echo "ERROR: this script must run on the FOREIGN/EXIT server, not on Iran ($IRAN_IP)."
  exit 5
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y openssh-server iptables curl

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/tp27-control-ssh-backup-$TS"
install -d -m 700 "$BACKUP"
iptables-save >"$BACKUP/iptables-save.txt"
cp -a /etc/ssh/sshd_config "$BACKUP/sshd_config"
[[ -f /etc/ssh/tp27_control_sshd_config ]] && cp -a /etc/ssh/tp27_control_sshd_config "$BACKUP/" || true
[[ -f /etc/systemd/system/tp27-control-sshd.service ]] && cp -a /etc/systemd/system/tp27-control-sshd.service "$BACKUP/" || true
[[ -f /etc/systemd/system/tp27-control-firewall.service ]] && cp -a /etc/systemd/system/tp27-control-firewall.service "$BACKUP/" || true
[[ -f /usr/local/lib/tp27/control-firewall.sh ]] && cp -a /usr/local/lib/tp27/control-firewall.sh "$BACKUP/" || true

install -d -m 755 /usr/local/lib/tp27
install -d -m 700 /etc/tp27

cat >/etc/tp27/control-ssh.env <<EOF
IRAN_IP=$IRAN_IP
CONTROL_PORT=$CONTROL_PORT
EOF
chmod 600 /etc/tp27/control-ssh.env

cat >/usr/local/lib/tp27/control-firewall.sh <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
source /etc/tp27/control-ssh.env
CHAIN='TP27_SSH_IN'
PORT="$CONTROL_PORT"

# Only our dedicated TP27 chain/hook is reconciled. No global flush.
iptables -w 5 -N "$CHAIN" 2>/dev/null || true

# Remove stale INPUT hooks to this TP27-only chain, then rebuild one clean hook.
while iptables -w 5 -C INPUT -j "$CHAIN" 2>/dev/null; do
  iptables -w 5 -D INPUT -j "$CHAIN"
done
# Also remove older port-specific hooks created by previous TP27 bootstrap versions.
while iptables -w 5 -C INPUT -p tcp --dport "$PORT" -j "$CHAIN" 2>/dev/null; do
  iptables -w 5 -D INPUT -p tcp --dport "$PORT" -j "$CHAIN"
done

iptables -w 5 -F "$CHAIN"
iptables -w 5 -A "$CHAIN" -p tcp -s "$IRAN_IP" --dport "$PORT" -j ACCEPT
iptables -w 5 -A "$CHAIN" -p tcp --dport "$PORT" -j DROP
iptables -w 5 -I INPUT 1 -p tcp --dport "$PORT" -j "$CHAIN"
EOF
chmod 700 /usr/local/lib/tp27/control-firewall.sh

cat >/etc/ssh/tp27_control_sshd_config <<EOF
Port $CONTROL_PORT
Protocol 2
ListenAddress 0.0.0.0
HostKey /etc/ssh/ssh_host_ed25519_key
HostKey /etc/ssh/ssh_host_rsa_key
PidFile /run/tp27-sshd/sshd.pid
PermitRootLogin yes
AllowUsers root
PasswordAuthentication yes
PermitEmptyPasswords no
PubkeyAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods password
UsePAM no
UseDNS no
GSSAPIAuthentication no
X11Forwarding no
AllowTcpForwarding no
PermitTunnel no
GatewayPorts no
LoginGraceTime 30
MaxAuthTries 3
MaxSessions 4
MaxStartups 50:30:200
ClientAliveInterval 30
ClientAliveCountMax 3
PrintMotd no
PrintLastLog no
LogLevel VERBOSE
Subsystem sftp internal-sftp
EOF
chmod 600 /etc/ssh/tp27_control_sshd_config

/usr/sbin/sshd -t -f /etc/ssh/tp27_control_sshd_config

cat >/etc/systemd/system/tp27-control-firewall.service <<'EOF'
[Unit]
Description=TP27 Control SSH firewall
After=network-online.target
Wants=network-online.target
Before=tp27-control-sshd.service

[Service]
Type=oneshot
ExecStart=/usr/local/lib/tp27/control-firewall.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/tp27-control-sshd.service <<'EOF'
[Unit]
Description=TP27 isolated SSH control service
After=network-online.target tp27-control-firewall.service
Wants=network-online.target
Requires=tp27-control-firewall.service

[Service]
Type=simple
RuntimeDirectory=tp27-sshd
RuntimeDirectoryMode=0755
ExecStartPre=/usr/sbin/sshd -t -f /etc/ssh/tp27_control_sshd_config
ExecStart=/usr/sbin/sshd -D -e -f /etc/ssh/tp27_control_sshd_config
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl reset-failed tp27-control-firewall.service tp27-control-sshd.service 2>/dev/null || true
systemctl enable tp27-control-firewall.service >/dev/null
systemctl restart tp27-control-firewall.service
systemctl enable tp27-control-sshd.service >/dev/null
systemctl restart tp27-control-sshd.service
sleep 1

if ! systemctl is-active --quiet tp27-control-firewall.service; then
  echo "ERROR: TP27 control firewall failed"
  systemctl status tp27-control-firewall.service --no-pager -l || true
  journalctl -u tp27-control-firewall.service -n 50 --no-pager || true
  exit 3
fi

if ! systemctl is-active --quiet tp27-control-sshd.service; then
  echo "ERROR: TP27 control sshd failed"
  systemctl status tp27-control-sshd.service --no-pager -l || true
  journalctl -u tp27-control-sshd.service -n 50 --no-pager || true
  exit 4
fi

if ! ss -lntp | grep -q ":${CONTROL_PORT} "; then
  echo "ERROR: control port is not listening: $CONTROL_PORT"
  ss -lntp | grep sshd || true
  exit 6
fi

echo "============================================================"
echo " TP27 CONTROL SSH READY"
echo " EXIT IP: ${CURRENT_IP:-unknown}"
echo " IRAN IP: $IRAN_IP"
echo " PORT: $CONTROL_PORT"
echo " AUTH: direct-password (PAM disabled)"
echo " ORIGINAL SSH PORT 22: UNCHANGED"
echo " BACKUP: $BACKUP"
echo "============================================================"
