#!/usr/bin/env python3
import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import requests

VERSION = '1.5.0'
RAW = os.environ.get('TP27_REPO_RAW', 'https://raw.githubusercontent.com/matin111/tp27-core/main')
ROOT = Path('/etc/tp27')
CONFIG = ROOT / 'exit-agent.json'
CERTS = ROOT / 'certs'
EXITS = ROOT / 'exits'
LINK_BIN = Path('/usr/local/lib/tp27/tp27-link')
LINK_UNIT = Path('/etc/systemd/system/tp27-link@.service')
FRAMED_BIN = Path('/usr/local/lib/tp27/tp27-framed-tls.py')
FRAMED_UNIT = Path('/etc/systemd/system/tp27-framed@.service')
AGENT_FILE = Path('/opt/tp27-exit-agent/tp27-exit-agent.py')
PLAIN_AGENT_FILE = Path('/opt/tp27-exit-agent/tp27-exit-agent-plain.py')
AGENT_UNIT = Path('/etc/systemd/system/tp27-exit-agent.service')
PLAIN_AGENT_UNIT = Path('/etc/systemd/system/tp27-exit-agent-plain.service')


def atomic_json(path: Path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def atomic_text(path: Path, text: str, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
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


def _download(url, path, mode):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.new')
    tmp.write_bytes(r.content)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def refresh_runtime():
    files = [
        (RAW + '/bin/tp27-link-mtls', LINK_BIN, 0o755),
        (RAW + '/systemd/tp27-link@.service', LINK_UNIT, 0o644),
        (RAW + '/bin/tp27-framed-tls.py', FRAMED_BIN, 0o755),
        (RAW + '/systemd/tp27-framed@.service', FRAMED_UNIT, 0o644),
    ]
    for url, path, mode in files:
        _download(url, path, mode)
    run(['systemctl', 'daemon-reload'], timeout=20)


def ensure_runtime():
    missing = [p for p in (LINK_BIN, LINK_UNIT, FRAMED_BIN, FRAMED_UNIT) if not p.exists()]
    if missing:
        raise RuntimeError('TP27 runtime ناقص است؛ Agent را Upgrade کنید: ' + ', '.join(str(x) for x in missing))


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
        json=body, timeout=20, verify=not insecure,
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


def _pem(value, label, max_len=40000):
    value = str(value or '')
    if f'BEGIN {label}' not in value or len(value) > max_len:
        raise ValueError(f'invalid {label.lower()}')
    return value.strip() + '\n'


def _ipv4(value, name):
    addr = ipaddress.ip_address(str(value or '').strip())
    if addr.version != 4:
        raise ValueError(f'{name} must be IPv4')
    return str(addr)


def _iface(eid):
    return ('t27' + re.sub(r'[^a-z0-9]', '', eid.lower()))[:15]


def _service_for_exit(eid):
    cfg_path = EXITS / (eid + '.json')
    try:
        cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    except Exception:
        cfg = {}
    return ('tp27-framed@' + eid + '.service') if cfg.get('transport') == 'framed-tls' else ('tp27-link@' + eid + '.service')


def _stop_exit_services(eid):
    for svc in ('tp27-link@' + eid + '.service', 'tp27-framed@' + eid + '.service'):
        run(['systemctl', 'disable', '--now', svc], timeout=25, check=False)
        run(['systemctl', 'reset-failed', svc], timeout=15, check=False)
    run(['ip', 'link', 'del', _iface(eid)], timeout=10, check=False)


def _port_free(bind_ip, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((bind_ip, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _cert_pin(cert_path):
    pem = cert_path.read_text(encoding='utf-8')
    der = ssl.PEM_cert_to_DER_cert(pem)
    return hashlib.sha256(der).hexdigest()


def action_status(_args):
    rc, out, err = run(['tp27', 'status'], timeout=20, check=False)
    _, addr, _ = run(['ip', '-br', 'addr'], timeout=10, check=False)
    _, links, _ = run(['systemctl', '--no-pager', '--no-legend', '--plain', 'list-units', 'tp27-link@*.service', 'tp27-framed@*.service'], timeout=10, check=False)
    return {
        'tp27_rc': rc,
        'tp27': out or err,
        'addresses': addr[:12000],
        'links': links[:12000],
        'agent_version': VERSION,
    }


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
    client_ca_pem = _pem(args.get('client_ca_pem'), 'CERTIFICATE', 20000)
    CERTS.mkdir(parents=True, exist_ok=True)
    ca_path = CERTS / (eid + '-iran.crt')
    atomic_text(ca_path, client_ca_pem, 0o644)
    cmd = [
        'tp27', 'exit-provision', '--id', eid, '--listen-port', str(listen_port),
        '--tunnel-ip', tunnel_ip, '--mtu', str(mtu), '--client-ca', str(ca_path),
    ]
    rc, out, err = run(cmd, timeout=90, check=False)
    if rc != 0:
        raise RuntimeError(err or out or 'exit provisioning failed')
    cert_path = CERTS / (eid + '.crt')
    if not cert_path.exists():
        raise RuntimeError('exit certificate missing')
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'tp27-link@' + eid + '.service']).returncode == 0
    return {'id': eid, 'listen_port': listen_port, 'tunnel_ip': tunnel_ip, 'service_active': active,
            'exit_cert_pem': cert_path.read_text(encoding='utf-8'), 'output': out}


def action_provision_reverse_exit(args):
    eid = safe_id(str(args.get('id', '')))
    ensure_runtime()
    iran_endpoint = str(args.get('iran_endpoint', '')).strip()
    ipaddress.ip_address(iran_endpoint)
    listen_port = int(args.get('listen_port'))
    if not 1 <= listen_port <= 65535:
        raise ValueError('invalid listen_port')
    tunnel_ip = str(args.get('tunnel_ip', ''))
    ipaddress.ip_interface(tunnel_ip)
    mtu = int(args.get('mtu', 1280))
    if not 1000 <= mtu <= 1500:
        raise ValueError('invalid mtu')
    ca_pem = _pem(args.get('ca_pem'), 'CERTIFICATE')
    cert_pem = _pem(args.get('client_cert_pem'), 'CERTIFICATE')
    key_pem = _pem(args.get('client_key_pem'), 'PRIVATE KEY')
    CERTS.mkdir(parents=True, exist_ok=True)
    EXITS.mkdir(parents=True, exist_ok=True)
    ca_path = CERTS / (eid + '-reverse-ca.crt')
    cert_path = CERTS / (eid + '-reverse-client.crt')
    key_path = CERTS / (eid + '-reverse-client.key')
    atomic_text(ca_path, ca_pem, 0o644)
    atomic_text(cert_path, cert_pem, 0o644)
    atomic_text(key_path, key_pem, 0o600)
    cfg = {
        'role': 'exit-connect', 'id': eid, 'interface': _iface(eid), 'tunnel_ip': tunnel_ip,
        'endpoint': iran_endpoint, 'listen_port': listen_port, 'mtu': mtu,
        'ca_file': str(ca_path), 'cert': str(cert_path), 'key': str(key_path),
    }
    atomic_json(EXITS / (eid + '.json'), cfg)
    run(['systemctl', 'enable', 'tp27-link@' + eid + '.service'], timeout=20, check=False)
    rc, out, err = run(['systemctl', 'restart', 'tp27-link@' + eid + '.service'], timeout=30, check=False)
    if rc != 0:
        raise RuntimeError(err or out or 'reverse exit link start failed')
    time.sleep(1)
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'tp27-link@' + eid + '.service']).returncode == 0
    _, addr_out, _ = run(['ip', '-br', '-4', 'addr', 'show', 'dev', _iface(eid)], timeout=10, check=False)
    _, route_out, _ = run(['ip', '-4', 'route', 'show', 'dev', _iface(eid)], timeout=10, check=False)
    return {'id': eid, 'mode': 'reverse', 'iran_endpoint': iran_endpoint, 'listen_port': listen_port,
            'tunnel_ip': tunnel_ip, 'service_active': active, 'interface_state': addr_out,
            'route_state': route_out, 'agent_version': VERSION}


def action_provision_gre_exit(args):
    eid = safe_id(str(args.get('id', '')))
    ensure_runtime()
    iran_public = _ipv4(args.get('iran_public'), 'iran_public')
    exit_public = _ipv4(args.get('exit_public') or public_ip(), 'exit_public')
    tunnel_ip = str(args.get('tunnel_ip', '')).strip()
    peer_ip = _ipv4(args.get('peer_ip'), 'peer_ip')
    iface_addr = ipaddress.ip_interface(tunnel_ip)
    if iface_addr.version != 4:
        raise ValueError('tunnel_ip must be IPv4')
    mtu = int(args.get('mtu', 1400))
    if not 1000 <= mtu <= 1476:
        raise ValueError('GRE mtu must be between 1000 and 1476')
    gre_key = int(args.get('gre_key', 0))
    if not 1 <= gre_key <= 4294967295:
        raise ValueError('invalid GRE key')
    transport = str(args.get('transport') or 'gre').strip().lower()
    if transport not in {'gre', 'gre-fou'}:
        raise ValueError('invalid GRE transport')
    fou_port = int(args.get('fou_port') or 0)
    if transport == 'gre-fou' and not (fou_port == 443 or 1024 <= fou_port <= 65535):
        raise ValueError('invalid FOU port')
    EXITS.mkdir(parents=True, exist_ok=True)
    _stop_exit_services(eid)
    cfg = {
        'role': 'gre-exit', 'id': eid, 'interface': _iface(eid), 'tunnel_ip': tunnel_ip,
        'peer_ip': peer_ip, 'local_public': exit_public, 'remote_public': iran_public,
        'gre_key': gre_key, 'mtu': mtu, 'txqueuelen': int(args.get('txqueuelen', 1000)),
        'transport': transport, 'fou_port': fou_port,
    }
    atomic_json(EXITS / (eid + '.json'), cfg)
    run(['systemctl', 'enable', 'tp27-link@' + eid + '.service'], timeout=20, check=False)
    rc, out, err = run(['systemctl', 'restart', 'tp27-link@' + eid + '.service'], timeout=30, check=False)
    if rc != 0:
        raise RuntimeError(err or out or 'GRE exit link start failed')
    time.sleep(2)
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'tp27-link@' + eid + '.service']).returncode == 0
    ping_rc, ping_out, ping_err = run(['ping', '-I', _iface(eid), '-c', '2', '-W', '2', peer_ip], timeout=8, check=False)
    _, addr_out, _ = run(['ip', '-br', '-4', 'addr', 'show', 'dev', _iface(eid)], timeout=10, check=False)
    _, route_out, _ = run(['ip', '-4', 'route', 'show', 'dev', _iface(eid)], timeout=10, check=False)
    _, link_out, _ = run(['ip', '-s', 'link', 'show', 'dev', _iface(eid)], timeout=10, check=False)
    return {
        'id': eid, 'mode': 'gre-fou' if transport == 'gre-fou' else 'gre-native',
        'transport': transport, 'fou_port': fou_port or None, 'service_active': active,
        'health_ping': ping_rc == 0, 'ping_output': ping_out or ping_err,
        'interface': _iface(eid), 'interface_state': addr_out, 'route_state': route_out,
        'link_state': link_out, 'tunnel_ip': tunnel_ip, 'peer_ip': peer_ip,
        'gre_key': gre_key, 'agent_version': VERSION,
    }


def action_provision_tls_exit(args):
    eid = safe_id(str(args.get('id', '')))
    ensure_runtime()
    iran_public = _ipv4(args.get('iran_public'), 'iran_public')
    exit_public = _ipv4(args.get('exit_public') or public_ip(), 'exit_public')
    tunnel_ip = str(args.get('tunnel_ip', '')).strip()
    peer_ip = _ipv4(args.get('peer_ip'), 'peer_ip')
    iface_addr = ipaddress.ip_interface(tunnel_ip)
    if iface_addr.version != 4:
        raise ValueError('tunnel_ip must be IPv4')
    listen_port = int(args.get('listen_port') or 443)
    if not 1 <= listen_port <= 65535:
        raise ValueError('invalid listen_port')
    mtu = int(args.get('mtu', 1300))
    if not 1000 <= mtu <= 1400:
        raise ValueError('TLS mtu must be between 1000 and 1400')

    _stop_exit_services(eid)
    if not _port_free(exit_public, listen_port):
        raise RuntimeError(f'TCP/{listen_port} روی سرور خارج اشغال است')

    CERTS.mkdir(parents=True, exist_ok=True)
    EXITS.mkdir(parents=True, exist_ok=True)
    cert = CERTS / (eid + '-framed.crt')
    key = CERTS / (eid + '-framed.key')
    if not cert.exists() or not key.exists():
        rc, out, err = run([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '3650',
            '-subj', '/CN=tp27-' + eid,
            '-addext', 'subjectAltName=IP:' + exit_public,
            '-keyout', str(key), '-out', str(cert),
        ], timeout=40, check=False)
        if rc != 0:
            raise RuntimeError(err or out or 'certificate generation failed')
        os.chmod(key, 0o600)
        os.chmod(cert, 0o644)

    cfg = {
        'role': 'framed-server', 'transport': 'framed-tls', 'mode': 'framed-tls',
        'id': eid, 'interface': _iface(eid), 'tunnel_ip': tunnel_ip, 'peer_ip': peer_ip,
        'local_public': exit_public, 'remote_public': iran_public, 'allowed_peer': iran_public,
        'listen_port': listen_port, 'mtu': mtu, 'cert': str(cert), 'key': str(key),
    }
    atomic_json(EXITS / (eid + '.json'), cfg)
    run(['systemctl', 'enable', 'tp27-framed@' + eid + '.service'], timeout=20, check=False)
    rc, out, err = run(['systemctl', 'restart', 'tp27-framed@' + eid + '.service'], timeout=30, check=False)
    if rc != 0:
        raise RuntimeError(err or out or 'Framed TLS exit start failed')
    time.sleep(2)
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'tp27-framed@' + eid + '.service']).returncode == 0
    _, addr_out, _ = run(['ip', '-br', '-4', 'addr', 'show', 'dev', _iface(eid)], timeout=10, check=False)
    _, link_out, _ = run(['ip', '-s', 'link', 'show', 'dev', _iface(eid)], timeout=10, check=False)
    return {
        'id': eid, 'mode': 'framed-tls', 'transport': 'framed-tls', 'listen_port': listen_port,
        'service_active': active, 'interface': _iface(eid), 'interface_state': addr_out,
        'link_state': link_out, 'tunnel_ip': tunnel_ip, 'peer_ip': peer_ip,
        'cert_pin': _cert_pin(cert), 'agent_version': VERSION,
    }


def action_restart_link(args):
    eid = safe_id(str(args.get('id', '')))
    svc = _service_for_exit(eid)
    rc, out, err = run(['systemctl', 'restart', svc], timeout=30, check=False)
    if rc != 0:
        raise RuntimeError(err or out or 'restart failed')
    active = subprocess.run(['systemctl', 'is-active', '--quiet', svc]).returncode == 0
    return {'id': eid, 'service': svc, 'active': active}


def action_delete_exit(args):
    eid = safe_id(str(args.get('id', '')))
    _stop_exit_services(eid)
    cfg = EXITS / (eid + '.json')
    try:
        cfg.unlink(missing_ok=True)
    except Exception:
        pass
    removed = []
    patterns = [
        eid + '.crt', eid + '.key', eid + '-iran.crt', eid + '-iran.key',
        eid + '-reverse-ca.crt', eid + '-reverse-client.crt', eid + '-reverse-client.key',
        eid + '-framed.crt', eid + '-framed.key',
    ]
    for name in patterns:
        path = CERTS / name
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except Exception:
            pass
    return {'id': eid, 'deleted': True, 'removed': removed}


def action_upgrade_agent(_args):
    targets = [
        (RAW + '/agent/tp27-exit-agent.py', AGENT_FILE, 0o700),
        (RAW + '/agent/tp27-exit-agent-plain.py', PLAIN_AGENT_FILE, 0o700),
        (RAW + '/systemd/tp27-exit-agent.service', AGENT_UNIT, 0o644),
        (RAW + '/systemd/tp27-exit-agent-plain.service', PLAIN_AGENT_UNIT, 0o644),
    ]
    for url, path, mode in targets:
        _download(url, path, mode)
    refresh_runtime()
    cfg = load_config()
    service = 'tp27-exit-agent-plain.service' if cfg.get('transport') == 'plain-hmac-v1' else 'tp27-exit-agent.service'
    run([
        'systemd-run', '--unit=tp27-agent-delayed-restart', '--on-active=5s',
        '/bin/systemctl', 'restart', service
    ], timeout=15, check=False)
    return {'upgrade_scheduled': True, 'target_version': VERSION, 'service': service}


ACTIONS = {
    'status': action_status,
    'provision_exit': action_provision_exit,
    'provision_reverse_exit': action_provision_reverse_exit,
    'provision_gre_exit': action_provision_gre_exit,
    'provision_tls_exit': action_provision_tls_exit,
    'restart_link': action_restart_link,
    'delete_exit': action_delete_exit,
    'upgrade_agent': action_upgrade_agent,
}


def heartbeat_payload():
    status = {}
    try:
        rc, out, err = run(['tp27', 'status'], timeout=15, check=False)
        status['tp27_rc'] = rc
        status['tp27'] = (out or err)[:8000]
        _, links, _ = run(['systemctl', '--no-pager', '--no-legend', '--plain', 'list-units', 'tp27-link@*.service', 'tp27-framed@*.service'], timeout=10, check=False)
        status['links'] = links[:8000]
    except Exception as exc:
        status['tp27_error'] = str(exc)
    return {'hostname': socket.gethostname(), 'public_ip': public_ip(), 'version': VERSION, 'status': status}


def post_result(s, cfg, command_id, ok, result=None, error=None):
    r = s.post(api_url(cfg, '/api/agent/result'), json={
        'command_id': command_id, 'ok': bool(ok), 'result': result or {}, 'error': error,
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
