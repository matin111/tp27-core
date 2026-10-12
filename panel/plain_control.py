#!/usr/bin/env python3
import argparse
import hashlib
import hmac
import json
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, '/opt/tp27-panel')
import agent_control as ac

ROOT = Path('/etc/tp27')
KEYS_FILE = ROOT / 'plain-agent-keys.json'
STATE_LOCK = threading.RLock()
CHALLENGES = {}
SEEN_NONCES = {}
MAX_BODY = 512 * 1024
CLOCK_SKEW = 180
NONCE_TTL = 600


def _atomic_json(path: Path, data):
    ac._atomic_json(path, data, 0o600)


def _load_json(path: Path, default):
    return ac._load(path, default)


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def _derive_key(token: str, agent_id: str):
    return hmac.new(token.encode(), ('TP27-PLAIN-AGENT-KEY-V1\n' + agent_id).encode(), hashlib.sha256).digest()


def _response_sig(key: bytes, status: int, body: bytes):
    digest = hashlib.sha256(body).hexdigest()
    msg = f'TP27-RESPONSE-V1\n{status}\n{digest}'.encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _valid_agent_id(value: str):
    return ac._valid_agent_id(value)


def _keys():
    data = _load_json(KEYS_FILE, {'keys': {}})
    data.setdefault('keys', {})
    return data


def _get_key(agent_id: str):
    with ac.LOCK:
        value = _keys()['keys'].get(agent_id, '')
    if not re.fullmatch(r'[0-9a-f]{64}', value or ''):
        return None
    return bytes.fromhex(value)


def _store_key(agent_id: str, key: bytes):
    with ac.LOCK:
        data = _keys()
        data['keys'][agent_id] = key.hex()
        _atomic_json(KEYS_FILE, data)


def _cleanup_state(now=None):
    now = int(now or time.time())
    with STATE_LOCK:
        for aid, item in list(CHALLENGES.items()):
            if now - int(item.get('created_at', 0)) > NONCE_TTL:
                CHALLENGES.pop(aid, None)
        for token, created in list(SEEN_NONCES.items()):
            if now - int(created) > NONCE_TTL:
                SEEN_NONCES.pop(token, None)


def _claim_nonce(agent_id: str, nonce: str, now: int):
    if not re.fullmatch(r'[0-9a-f]{32}', nonce or ''):
        raise ValueError('invalid nonce')
    token = agent_id + ':' + nonce
    with STATE_LOCK:
        if token in SEEN_NONCES:
            raise ValueError('replayed request')
        SEEN_NONCES[token] = now


def _verify_signed_request(handler, raw: bytes, path: str):
    aid = handler.headers.get('X-TP27-Agent-ID', '').strip()
    _valid_agent_id(aid)
    key = _get_key(aid)
    if key is None:
        raise PermissionError('unknown agent')
    try:
        ts = int(handler.headers.get('X-TP27-Timestamp', '0'))
    except Exception:
        raise ValueError('invalid timestamp')
    now = int(time.time())
    if abs(now - ts) > CLOCK_SKEW:
        raise PermissionError('stale request')
    nonce = handler.headers.get('X-TP27-Nonce', '').strip().lower()
    supplied = handler.headers.get('X-TP27-Signature', '').strip().lower()
    digest = hashlib.sha256(raw).hexdigest()
    msg = f'TP27-REQUEST-V1\nPOST\n{path}\n{ts}\n{nonce}\n{digest}'.encode()
    expected = hmac.new(key, msg, hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise PermissionError('bad signature')
    _claim_nonce(aid, nonce, now)
    return aid, key


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = 'TP27-Control/1'
    sys_version = ''

    def log_message(self, fmt, *args):
        sys.stdout.write('%s - %s\n' % (self.client_address[0], fmt % args))
        sys.stdout.flush()

    def _send(self, status, obj, key=None):
        body = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if key is not None:
            self.send_header('X-TP27-Response-Signature', _response_sig(key, status, body))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            n = int(self.headers.get('Content-Length', '0'))
        except Exception:
            raise ValueError('bad content length')
        if n < 0 or n > MAX_BODY:
            raise ValueError('body too large')
        raw = self.rfile.read(n)
        try:
            obj = json.loads(raw.decode('utf-8'))
        except Exception:
            raise ValueError('invalid json')
        if not isinstance(obj, dict):
            raise ValueError('json object required')
        return raw, obj

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == '/health':
            self._send(200, {'ok': True, 'service': 'TP27 plain control', 'version': 1, 'transport': 'plain-hmac-v1'})
            return
        if parsed.path != '/v1/challenge':
            self._send(404, {'ok': False, 'error': 'not found'})
            return
        try:
            aid = parse_qs(parsed.query).get('agent_id', [''])[0].strip()
            _valid_agent_id(aid)
            _cleanup_state()
            nonce = secrets.token_hex(24)
            with STATE_LOCK:
                CHALLENGES[aid] = {'nonce': nonce, 'created_at': int(time.time())}
            self._send(200, {'ok': True, 'nonce': nonce, 'expires_in': NONCE_TTL})
        except Exception as exc:
            self._send(400, {'ok': False, 'error': str(exc)[:300]})

    def do_POST(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        try:
            raw, body = self._read_json()
            if path == '/v1/enroll':
                self._enroll(raw, body)
                return
            if path not in {'/v1/heartbeat', '/v1/poll', '/v1/result'}:
                self._send(404, {'ok': False, 'error': 'not found'})
                return
            aid, key = _verify_signed_request(self, raw, path)
            if path == '/v1/heartbeat':
                self._heartbeat(aid, key, body)
            elif path == '/v1/poll':
                self._poll(aid, key)
            else:
                self._result(aid, key, body)
        except PermissionError as exc:
            self._send(401, {'ok': False, 'error': str(exc)[:300]})
        except Exception as exc:
            self._send(400, {'ok': False, 'error': str(exc)[:300]})

    def _enroll(self, raw, body):
        aid = str(body.get('agent_id', '')).strip()
        _valid_agent_id(aid)
        nonce = str(body.get('nonce', '')).strip().lower()
        try:
            ts = int(body.get('timestamp', 0))
        except Exception:
            raise ValueError('invalid timestamp')
        now = int(time.time())
        if abs(now - ts) > CLOCK_SKEW:
            raise PermissionError('stale enrollment')
        with STATE_LOCK:
            item = CHALLENGES.get(aid)
            if not item or item.get('nonce') != nonce or now - int(item.get('created_at', 0)) > NONCE_TTL:
                raise PermissionError('invalid challenge')
        token = ac.ensure_enrollment_token()
        supplied = self.headers.get('X-TP27-Enroll-MAC', '').strip().lower()
        expected = hmac.new(token.encode(), b'TP27-ENROLL-V1\n' + raw, hashlib.sha256).hexdigest()
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise PermissionError('invalid enrollment proof')
        key = _derive_key(token, aid)
        hostname = str(body.get('hostname', '')).strip()[:255]
        public_ip = str(body.get('public_ip', '')).strip()[:64]
        version = str(body.get('version', '1')).strip()[:64]
        if not hostname or not public_ip:
            raise ValueError('hostname/public_ip required')
        with ac.LOCK:
            data = ac._agents()
            old = data['agents'].get(aid, {})
            data['agents'][aid] = {
                'hostname': hostname,
                'public_ip': public_ip,
                'version': version,
                'created_at': int(old.get('created_at', now) or now),
                'last_seen': now,
                'status': old.get('status') or {},
                'disabled': False,
                'transport': 'plain-hmac-v1',
            }
            ac._atomic_json(ac.AGENTS_FILE, data)
        _store_key(aid, key)
        with STATE_LOCK:
            CHALLENGES.pop(aid, None)
        self._send(200, {'ok': True, 'agent_id': aid, 'poll_seconds': 5, 'transport': 'plain-hmac-v1'}, key)

    def _heartbeat(self, aid, key, body):
        now = int(time.time())
        with ac.LOCK:
            data = ac._agents()
            rec = data['agents'].get(aid)
            if not rec or rec.get('disabled'):
                raise PermissionError('unknown agent')
            rec['hostname'] = str(body.get('hostname', rec.get('hostname', '')))[:255]
            rec['public_ip'] = str(body.get('public_ip', rec.get('public_ip', '')))[:64]
            rec['version'] = str(body.get('version', rec.get('version', '')))[:64]
            rec['status'] = body.get('status') if isinstance(body.get('status'), dict) else {}
            rec['last_seen'] = now
            rec['transport'] = 'plain-hmac-v1'
            ac._atomic_json(ac.AGENTS_FILE, data)
        self._send(200, {'ok': True, 'server_time': now}, key)

    def _poll(self, aid, key):
        now = int(time.time())
        command = None
        with ac.LOCK:
            data = ac._agents()
            rec = data['agents'].get(aid)
            if not rec or rec.get('disabled'):
                raise PermissionError('unknown agent')
            rec['last_seen'] = now
            rec['transport'] = 'plain-hmac-v1'
            ac._atomic_json(ac.AGENTS_FILE, data)
            commands = ac._commands()
            for item in commands['commands']:
                if item.get('agent_id') == aid and item.get('state') == 'queued':
                    item['state'] = 'delivered'
                    item['delivered_at'] = now
                    command = {'id': item['id'], 'action': item['action'], 'args': item.get('args') or {}}
                    ac._atomic_json(ac.COMMANDS_FILE, commands)
                    break
        self._send(200, {'ok': True, 'command': command}, key)

    def _result(self, aid, key, body):
        cid = str(body.get('command_id', '')).strip()
        if len(cid) < 8:
            raise ValueError('invalid command_id')
        now = int(time.time())
        with ac.LOCK:
            commands = ac._commands()
            found = False
            for item in commands['commands']:
                if item.get('id') == cid and item.get('agent_id') == aid:
                    item['state'] = 'done' if bool(body.get('ok')) else 'failed'
                    item['ok'] = bool(body.get('ok'))
                    item['result'] = body.get('result') if isinstance(body.get('result'), dict) else {}
                    err = body.get('error')
                    item['error'] = None if err is None else str(err)[:10000]
                    item['finished_at'] = now
                    found = True
                    break
            if not found:
                raise ValueError('command not found')
            ac._atomic_json(ac.COMMANDS_FILE, commands)
        self._send(200, {'ok': True}, key)


def main():
    p = argparse.ArgumentParser(prog='tp27-plain-control')
    p.add_argument('--host', default='0.0.0.0')
    p.add_argument('--port', type=int, default=443)
    args = p.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    print(f'TP27 plain control listening on {args.host}:{args.port}', flush=True)
    Server((args.host, args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
