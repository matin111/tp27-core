#!/usr/bin/env python3
import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests

VERSION = '1.1.0'
ROOT = Path('/etc/tp27')
CONFIG = ROOT / 'exit-agent.json'
CERTS = ROOT / 'certs'
EXITS = ROOT / 'exits'


def atomic_json(path: Path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def load_config():
    if not CONFIG.exists():
        raise RuntimeError('Agent is not enrolled')
    return json.loads(CONFIG.read_text(encoding='utf-8'))


def run(cmd, timeout=120, check=True):
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or 'command failed').strip())
    return p.returncode, (p.stdout or '').strip(), (p.stderr or '').strip()


def machine_agent_id():
    try:
        machine = Path('/etc/machine-id').read_text().strip()
    except Exception:
        machine = socket.gethostname()
    return 'exit-' + hashlib.sha256(machine.encode()).hexdigest()[:16]


def public_ip():
    try:
        r = requests.get('https://ifconfig.me/ip', timeout=8)
        r.raise_for_status()
        return r.text.strip()
    except Exception:
        try:
            _, out, _ = run(['ip', '-4', 'route', 'get', '1.1.1.1'], timeout=5)
            m = re.search(r'\bsrc\s+(\d+\.\d+\.\d+\.\d+)', out)
            return m.group(1) if m else ''
        except Exception:
            return ''


def api_url(cfg, path):
    return cfg['controller_url'].rstrip('/') + path


def api_session(cfg):
    s = requests.Session()
    s.verify = bool(cfg.get('verify_tls', True))
    if cfg.get('agent_secret'):
        s.headers.update({
            'Authorization': 'Bearer ' + cfg['agent_secret'],
            'X-TP27-Agent-ID': cfg['agent_id'],
            'User-Agent': 'TP27-Exit-Agent/' + VERSION,
        })
    return s


def enroll(controller_url, token, insecure=False):
    if os.geteuid() != 0:
        raise RuntimeError('root required')
    body = {
        'enrollment_token': token,
        'agent_id': machine_agent_id(),
        'hostname': socket.gethostname(),
        'public_ip': public_ip(),
        'version': VERSION,
    }
    r = requests.post(
        controller_url.rstrip('/') + '/api/agent/enroll',
        json=body,
        timeout=20,
        verify=not insecure,
        headers={'User-Agent': 'TP27-Exit-Agent/' + VERSION},
    )
    if r.status_code >= 400:
        raise RuntimeError('enroll HTTP %s: %s' % (r.status_code, r.text[:500]))
    data = r.json()
    if not data.get('ok') or not data.get('agent_secret'):
        raise RuntimeError('invalid enrollment response')
    cfg = {
        'controller_url': controller_url.rstrip('/'),
        'agent_id': body['agent_id'],
        'agent_secret': data['agent_secret'],
        'verify_tls': not insecure,
        'poll_seconds': max(2, min(int(data.get('poll_seconds', 5)), 30)),
        'version': VERSION,
    }
    atomic_json(CONFIG, cfg)
    print('AGENT_ID=' + cfg['agent_id'])
    print('CONTROLLER=' + cfg['controller_url'])
    print('TLS_VERIFY=' + str(cfg['verify_tls']).lower())


def safe_id(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,24}', value or ''):
        raise ValueError('invalid exit id')
    return value


def action_status(_args):
    rc, out, err = run(['tp27', 'status'], timeout=20, check=False)
    _, addr, _ = run(['ip', '-br', 'addr'], timeout=10, check=False)
    return {'tp27_rc': rc, 'tp27': out or err, 'addresses': addr[:12000]}


def action_provision_exit(args):
    eid = safe_id(str(args.get('id', '')))
    listen_port = int(args.get('listen_port'))
    if not 1 <= listen_port <= 65535:
        raise ValueError('invalid listen_port')
    tunnel_ip = str(args.get('tunnel_ip', ''))
    ipaddress.ip_interface(tunnel_ip)
    mtu = int(args.get('mtu', 1280))
    if not 1000 <= mtu <= 1500:
        raise ValueError('invalid mtu')
    client_ca_pem = str(args.get('client_ca_pem', ''))
    if 'BEGIN CERTIFICATE' not in client_ca_pem or len(client_ca_pem) > 20000:
        raise ValueError('invalid client certificate')

    CERTS.mkdir(parents=True, exist_ok=True)
    ca_path = CERTS / (eid + '-iran.crt')
    ca_path.write_text(client_ca_pem.strip() + '\n', encoding='utf-8')
    os.chmod(ca_path, 0o644)

    cmd = [
        'tp27', 'exit-provision',
        '--id', eid,
        '--listen-port', str(listen_port),
        '--tunnel-ip', tunnel_ip,
        '--mtu', str(mtu),
        '--client-ca', str(ca_path),
    ]
    rc, out, err = run(cmd, timeout=90, check=False)
    if rc != 0:
        raise RuntimeError(err or out or 'exit provisioning failed')
    cert_path = CERTS / (eid + '.crt')
    if not cert_path.exists():
        raise RuntimeError('exit certificate missing')
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'tp27-link@' + eid + '.service']).returncode == 0
    return {
        'id': eid,
        'listen_port': listen_port,
        'tunnel_ip': tunnel_ip,
        'service_active': active,
        'exit_cert_pem': cert_path.read_text(encoding='utf-8'),
        'output': out,
    }


def action_restart_link(args):
    eid = safe_id(str(args.get('id', '')))
    svc = 'tp27-link@' + eid + '.service'
    rc, out, err = run(['systemctl', 'restart', svc], timeout=30, check=False)
    if rc != 0:
        raise RuntimeError(err or out or 'restart failed')
    active = subprocess.run(['systemctl', 'is-active', '--quiet', svc]).returncode == 0
    return {'id': eid, 'active': active}


def action_delete_exit(args):
    eid = safe_id(str(args.get('id', '')))
    svc = 'tp27-link@' + eid + '.service'
    run(['systemctl', 'disable', '--now', svc], timeout=30, check=False)
    run(['systemctl', 'reset-failed', svc], timeout=15, check=False)

    cfg = EXITS / (eid + '.json')
    try:
        cfg.unlink(missing_ok=True)
    except Exception:
        pass

    removed = []
    for path in (
        CERTS / (eid + '.crt'),
        CERTS / (eid + '.key'),
        CERTS / (eid + '-iran.crt'),
        CERTS / (eid + '-iran.key'),
    ):
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except Exception:
            pass

    return {'id': eid, 'deleted': True, 'removed': removed}


ACTIONS = {
    'status': action_status,
    'provision_exit': action_provision_exit,
    'restart_link': action_restart_link,
    'delete_exit': action_delete_exit,
}


def heartbeat_payload():
    status = {}
    try:
        rc, out, err = run(['tp27', 'status'], timeout=15, check=False)
        status['tp27_rc'] = rc
        status['tp27'] = (out or err)[:8000]
    except Exception as exc:
        status['tp27_error'] = str(exc)
    return {
        'hostname': socket.gethostname(),
        'public_ip': public_ip(),
        'version': VERSION,
        'status': status,
    }


def post_result(s, cfg, command_id, ok, result=None, error=None):
    r = s.post(api_url(cfg, '/api/agent/result'), json={
        'command_id': command_id,
        'ok': bool(ok),
        'result': result or {},
        'error': error,
    }, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError('result HTTP %s: %s' % (r.status_code, r.text[:300]))


def run_forever():
    cfg = load_config()
    s = api_session(cfg)
    poll_seconds = int(cfg.get('poll_seconds', 5))
    last_heartbeat = 0
    failures = 0

    while True:
        try:
            now = time.time()
            if now - last_heartbeat >= 20:
                r = s.post(api_url(cfg, '/api/agent/heartbeat'), json=heartbeat_payload(), timeout=25)
                if r.status_code >= 400:
                    raise RuntimeError('heartbeat HTTP %s: %s' % (r.status_code, r.text[:300]))
                last_heartbeat = now

            r = s.post(api_url(cfg, '/api/agent/poll'), json={}, timeout=30)
            if r.status_code >= 400:
                raise RuntimeError('poll HTTP %s: %s' % (r.status_code, r.text[:300]))
            command = r.json().get('command')
            if command:
                cid = command.get('id', '')
                handler = ACTIONS.get(command.get('action', ''))
                if not handler:
                    post_result(s, cfg, cid, False, error='unsupported action')
                else:
                    try:
                        result = handler(command.get('args') or {})
                        post_result(s, cfg, cid, True, result=result)
                    except Exception as exc:
                        post_result(s, cfg, cid, False, error=str(exc)[:10000])
            failures = 0
            time.sleep(poll_seconds)
        except requests.exceptions.SSLError as exc:
            print('TLS ERROR: ' + str(exc), file=sys.stderr, flush=True)
            time.sleep(30)
        except Exception as exc:
            failures += 1
            delay = min(60, max(5, failures * 5))
            print('agent error: %s; retry=%ss' % (exc, delay), file=sys.stderr, flush=True)
            time.sleep(delay)


def main():
    p = argparse.ArgumentParser(prog='tp27-exit-agent')
    p.add_argument('--controller')
    p.add_argument('--enroll')
    p.add_argument('--insecure', action='store_true')
    p.add_argument('--run', action='store_true')
    args = p.parse_args()
    try:
        if args.enroll:
            if not args.controller:
                raise RuntimeError('--controller required with --enroll')
            enroll(args.controller, args.enroll, insecure=args.insecure)
        elif args.run:
            run_forever()
        else:
            p.print_help()
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
