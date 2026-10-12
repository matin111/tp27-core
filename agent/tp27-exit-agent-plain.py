#!/usr/bin/env python3
import argparse
import hashlib
import hmac
import importlib.util
import json
import os
import re
import secrets
import socket
import sys
import time
from pathlib import Path

import requests

PLAIN_VERSION = '1.2.0'
BASE_AGENT = Path('/opt/tp27-exit-agent/tp27-exit-agent.py')
GOST_ADDON = Path('/opt/tp27-exit-agent/tp27_gost_addon.py')
CONFIG = Path('/etc/tp27/exit-agent.json')
SAFE_ACTIONS = {
    'status',
    'provision_gre_exit',
    'provision_tls_exit',
    'provision_gost_exit',
    'restart_link',
    'delete_exit',
    'upgrade_agent',
}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load ' + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_base():
    return load_module(BASE_AGENT, 'tp27_exit_agent_base')


BASE = load_base()
if GOST_ADDON.exists():
    GOST = load_module(GOST_ADDON, 'tp27_gost_addon_plain')
    GOST.install(BASE)
BASE_VERSION = getattr(BASE, 'VERSION', 'unknown')


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def derive_key(token, agent_id):
    return hmac.new(token.encode(), ('TP27-PLAIN-AGENT-KEY-V1\n' + agent_id).encode(), hashlib.sha256).digest()


def verify_response(resp, key):
    digest = hashlib.sha256(resp.content).hexdigest()
    msg = f'TP27-RESPONSE-V1\n{resp.status_code}\n{digest}'.encode()
    expected = hmac.new(key, msg, hashlib.sha256).hexdigest()
    supplied = resp.headers.get('X-TP27-Response-Signature', '').strip().lower()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise RuntimeError('invalid controller response signature')


def load_config():
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    if cfg.get('transport') != 'plain-hmac-v1':
        raise RuntimeError('transport mismatch')
    return cfg


def signed_post(cfg, path, payload, timeout=30):
    raw = canonical(payload)
    key = bytes.fromhex(cfg['transport_key'])
    ts = int(time.time())
    nonce = secrets.token_hex(16)
    digest = hashlib.sha256(raw).hexdigest()
    msg = f'TP27-REQUEST-V1\nPOST\n{path}\n{ts}\n{nonce}\n{digest}'.encode()
    sig = hmac.new(key, msg, hashlib.sha256).hexdigest()
    resp = requests.post(cfg['controller_url'].rstrip('/') + path, data=raw, timeout=timeout, headers={
        'Content-Type': 'application/json',
        'User-Agent': 'TP27-Exit-Agent-Plain/' + PLAIN_VERSION,
        'X-TP27-Agent-ID': cfg['agent_id'],
        'X-TP27-Timestamp': str(ts),
        'X-TP27-Nonce': nonce,
        'X-TP27-Signature': sig,
    })
    verify_response(resp, key)
    data = resp.json()
    if resp.status_code >= 400:
        raise RuntimeError('%s HTTP %s: %s' % (path, resp.status_code, data))
    return data


def enroll(controller_url, token):
    if os.geteuid() != 0:
        raise RuntimeError('root required')
    if not controller_url.startswith('http://'):
        raise RuntimeError('plain HMAC controller must use http://')
    aid = BASE.machine_agent_id()
    challenge = requests.get(controller_url.rstrip('/') + '/v1/challenge', params={'agent_id': aid}, timeout=10)
    challenge.raise_for_status()
    nonce = str(challenge.json().get('nonce', ''))
    if not re.fullmatch(r'[A-Fa-f0-9]{48}', nonce):
        raise RuntimeError('invalid challenge')
    body = {
        'agent_id': aid,
        'hostname': socket.gethostname(),
        'public_ip': BASE.public_ip(),
        'version': BASE_VERSION + '/plain-' + PLAIN_VERSION,
        'nonce': nonce,
        'timestamp': int(time.time()),
    }
    raw = canonical(body)
    mac = hmac.new(token.encode(), b'TP27-ENROLL-V1\n' + raw, hashlib.sha256).hexdigest()
    key = derive_key(token, aid)
    resp = requests.post(controller_url.rstrip('/') + '/v1/enroll', data=raw, timeout=20, headers={
        'Content-Type': 'application/json',
        'X-TP27-Enroll-MAC': mac,
    })
    verify_response(resp, key)
    data = resp.json()
    if resp.status_code >= 400 or not data.get('ok'):
        raise RuntimeError('enrollment failed')
    BASE.atomic_json(CONFIG, {
        'controller_url': controller_url.rstrip('/'),
        'agent_id': aid,
        'transport': 'plain-hmac-v1',
        'transport_key': key.hex(),
        'poll_seconds': max(2, min(int(data.get('poll_seconds', 5)), 30)),
        'version': BASE_VERSION,
        'plain_version': PLAIN_VERSION,
    })
    print('AGENT_ID=' + aid)
    print('CONTROLLER=' + controller_url.rstrip('/'))
    print('TRANSPORT=plain-hmac-v1')


def post_result(cfg, command_id, ok, result=None, error=None):
    signed_post(cfg, '/v1/result', {'command_id': command_id, 'ok': bool(ok), 'result': result or {}, 'error': error})


def run_forever():
    cfg = load_config()
    poll_seconds = int(cfg.get('poll_seconds', 5))
    last_heartbeat = 0
    failures = 0
    while True:
        try:
            now = time.time()
            if now - last_heartbeat >= 20:
                signed_post(cfg, '/v1/heartbeat', BASE.heartbeat_payload(), timeout=25)
                last_heartbeat = now
            reply = signed_post(cfg, '/v1/poll', {}, timeout=30)
            command = reply.get('command')
            if command:
                cid = str(command.get('id', ''))
                action = str(command.get('action', ''))
                handler = BASE.ACTIONS.get(action)
                if action not in SAFE_ACTIONS or handler is None:
                    post_result(cfg, cid, False, error='unsupported action on plain-hmac transport')
                else:
                    try:
                        post_result(cfg, cid, True, result=handler(command.get('args') or {}))
                    except Exception as exc:
                        post_result(cfg, cid, False, error=str(exc)[:10000])
            failures = 0
            time.sleep(poll_seconds)
        except Exception as exc:
            failures += 1
            delay = min(60, max(5, failures * 5))
            print('plain agent error: %s; retry=%ss' % (exc, delay), file=sys.stderr, flush=True)
            time.sleep(delay)


def main():
    p = argparse.ArgumentParser(prog='tp27-exit-agent-plain')
    p.add_argument('--controller')
    p.add_argument('--enroll')
    p.add_argument('--run', action='store_true')
    args = p.parse_args()
    if args.enroll:
        if not args.controller:
            raise SystemExit('--controller required')
        enroll(args.controller, args.enroll)
    elif args.run:
        run_forever()
    else:
        p.print_help()


if __name__ == '__main__':
    main()
