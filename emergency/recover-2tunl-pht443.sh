#!/usr/bin/env bash
set -Eeuo pipefail

MODE="${1:-}"
IRAN_IP="193.39.9.220"
EXIT_IP="212.74.39.90"
ID="2tunl"
IFACE="t272tunl"
PORT=443
TOKEN="TP27-GOST-7f4d2e91c803"

case "$MODE" in
  iran-receiver)
    ip -4 addr show | grep -qw "$IRAN_IP" || { echo "wrong server" >&2; exit 10; }
    systemctl disable --now tp27-gost-pht-${ID}.service 2>/dev/null || true
    systemctl reset-failed tp27-gost-pht-${ID}.service 2>/dev/null || true
    systemctl stop tp27-plain-control.service 2>/dev/null || true
    pkill -f tp27-recv-gost.py 2>/dev/null || true
    cat >/tmp/tp27-recv-gost.py <<'PY'
#!/usr/bin/env python3
import hashlib, os, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HOST='193.39.9.220'; PORT=443; ALLOWED='212.74.39.90'; TOKEN='TP27-GOST-7f4d2e91c803'
OUT='/tmp/gost.upload'; TMP=OUT+'.part'; MAX=64*1024*1024
class H(BaseHTTPRequestHandler):
    server_version='TP27-Recv/1'; sys_version=''
    def log_message(self, fmt,*args): print(f'{self.client_address[0]} - {fmt%args}', flush=True)
    def sendt(self,code,text):
        b=text.encode(); self.send_response(code); self.send_header('Content-Type','text/plain'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): self.sendt(200,'TP27_RECV_READY\n') if self.path=='/health' else self.sendt(404,'not found\n')
    def do_POST(self):
        if self.client_address[0] != ALLOWED: return self.sendt(403,'forbidden source\n')
        if self.headers.get('X-TP27-Token') != TOKEN: return self.sendt(403,'bad token\n')
        if self.path != '/gost': return self.sendt(404,'not found\n')
        try: size=int(self.headers.get('Content-Length','0'))
        except Exception: return self.sendt(400,'bad content length\n')
        if size<1 or size>MAX: return self.sendt(413,'bad size\n')
        remain=size; sha=hashlib.sha256()
        with open(TMP,'wb') as f:
            while remain:
                chunk=self.rfile.read(min(1024*1024,remain))
                if not chunk: return self.sendt(400,'short upload\n')
                f.write(chunk); sha.update(chunk); remain-=len(chunk)
        os.replace(TMP,OUT); digest=sha.hexdigest(); self.sendt(200,f'OK SHA256={digest}\n'); print(f'UPLOAD_OK size={size} sha256={digest}',flush=True)
        threading.Thread(target=self.server.shutdown,daemon=True).start()
class S(ThreadingHTTPServer): daemon_threads=True; allow_reuse_address=True
print(f'LISTEN {HOST}:{PORT}',flush=True); S((HOST,PORT),H).serve_forever()
PY
    chmod 700 /tmp/tp27-recv-gost.py
    rm -f /tmp/gost.upload /tmp/gost.upload.part
    timeout 900 python3 /tmp/tp27-recv-gost.py >/tmp/tp27-recv-gost.log 2>&1 &
    ;;
  iran-pht)
    ip -4 addr show | grep -qw "$IRAN_IP" || { echo "wrong server" >&2; exit 10; }
    [[ -x /usr/local/bin/gost ]] || { echo "gost missing" >&2; exit 11; }
    systemctl stop tp27-plain-control.service 2>/dev/null || true
    ip link del "$IFACE" 2>/dev/null || true
    cat >/etc/systemd/system/tp27-gost-pht-${ID}.service <<EOF
[Unit]
Description=TP27 GOST PHT TCP Fallback (${ID}, Iran)
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=/usr/local/bin/gost "-L=tun://:18500?net=10.253.0.6/30&name=${IFACE}&mtu=1300" "-L=relay+pht://${IRAN_IP}:${PORT}?bind=true"
Restart=always
RestartSec=2
User=root
Group=root
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload; systemctl enable --now tp27-gost-pht-${ID}.service
    ;;
  exit-pht)
    ip -4 addr show | grep -qw "$EXIT_IP" || { echo "wrong server" >&2; exit 10; }
    [[ -x /usr/local/bin/gost ]] || { echo "gost missing" >&2; exit 11; }
    systemctl stop tp27-gost-pht-${ID}.service 2>/dev/null || true
    ip link del "$IFACE" 2>/dev/null || true
    cat >/etc/systemd/system/tp27-gost-pht-${ID}.service <<EOF
[Unit]
Description=TP27 GOST PHT TCP Fallback (${ID}, Exit)
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=/usr/local/bin/gost "-L=tun://:0/:18500?net=10.253.0.5/30&name=${IFACE}&mtu=1300&keepAlive=true&ttl=5s" "-F=relay+pht://${IRAN_IP}:${PORT}"
Restart=always
RestartSec=2
User=root
Group=root
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload; systemctl enable --now tp27-gost-pht-${ID}.service
    ;;
  *)
    echo "usage: $0 iran-receiver|iran-pht|exit-pht" >&2; exit 2;;
esac
