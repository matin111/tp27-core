#!/usr/bin/env python3
import hashlib
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get('TP27_RECV_HOST', '0.0.0.0')
PORT = int(os.environ.get('TP27_RECV_PORT', '80'))
ALLOWED = os.environ.get('TP27_RECV_ALLOWED', '')
TOKEN = os.environ.get('TP27_RECV_TOKEN', '')
OUT = os.environ.get('TP27_RECV_OUT', '/tmp/gost.upload')
TMP = OUT + '.part'
MAX = 64 * 1024 * 1024

class H(BaseHTTPRequestHandler):
    server_version = 'TP27-Recv/1'
    sys_version = ''

    def log_message(self, fmt, *args):
        print(f'{self.client_address[0]} - {fmt % args}', flush=True)

    def _send(self, code, body):
        data = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/health':
            self._send(200, 'TP27_RECV_READY\n')
        else:
            self._send(404, 'not found\n')

    def do_POST(self):
        if ALLOWED and self.client_address[0] != ALLOWED:
            self._send(403, 'forbidden\n')
            return
        if TOKEN and self.headers.get('X-TP27-Token') != TOKEN:
            self._send(403, 'forbidden\n')
            return
        if self.path != '/gost':
            self._send(404, 'not found\n')
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except Exception:
            self._send(400, 'bad length\n')
            return
        if size < 1 or size > MAX:
            self._send(413, 'bad size\n')
            return
        remain = size
        sha = hashlib.sha256()
        with open(TMP, 'wb') as f:
            while remain:
                chunk = self.rfile.read(min(1024 * 1024, remain))
                if not chunk:
                    self._send(400, 'short upload\n')
                    try: os.unlink(TMP)
                    except FileNotFoundError: pass
                    return
                f.write(chunk)
                sha.update(chunk)
                remain -= len(chunk)
        os.replace(TMP, OUT)
        digest = sha.hexdigest()
        self._send(200, f'OK SHA256={digest}\n')
        print(f'UPLOAD_OK size={size} sha256={digest}', flush=True)
        threading.Thread(target=self.server.shutdown, daemon=True).start()

class S(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

if __name__ == '__main__':
    print(f'LISTEN {HOST}:{PORT}', flush=True)
    S((HOST, PORT), H).serve_forever()
