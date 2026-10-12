#!/usr/bin/env python3
import hashlib
import ipaddress
import os
import re
import socket
import subprocess
import time
from pathlib import Path

GOST_CORE = Path('/usr/local/lib/tp27/tp27-gost-core.py')
GOST_INSTALLER = Path('/usr/local/lib/tp27/tp27-gost-install.sh')
GOST_DISPATCHER = Path('/usr/local/lib/tp27/tp27-link-dispatcher')
GOST_BIN = Path('/usr/local/bin/tp27-gost')


def _udp_port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(('127.0.0.1', int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _internal_port(eid):
    seed = int.from_bytes(hashlib.sha256(eid.encode()).digest()[:2], 'big')
    start = 22000 + (seed % 18000)
    for offset in range(64):
        port = 22000 + ((start - 22000 + offset) % 18000)
        if _udp_port_free(port):
            return port
    raise RuntimeError('هیچ UDP loopback port آزاد برای GOST پیدا نشد')


def _ensure_runtime(base):
    missing = [p for p in (GOST_CORE, GOST_INSTALLER, GOST_DISPATCHER) if not p.exists()]
    if missing:
        raise RuntimeError('GOST runtime نصب نیست؛ Agent را با Installer/Upgrade جدید TP27 ارتقا دهید: ' + ', '.join(str(x) for x in missing))
    if not GOST_BIN.exists():
        rc, out, err = base.run([str(GOST_INSTALLER)], timeout=220, check=False)
        if rc != 0:
            raise RuntimeError('نصب خودکار GOST v3.3.0 ناموفق بود: ' + (err or out or 'download failed'))
    rc, out, err = base.run([str(GOST_BIN), '-V'], timeout=10, check=False)
    if rc != 0 or '3.3.0' not in (out + '\n' + err):
        raise RuntimeError('GOST v3.3.0 در /usr/local/bin/tp27-gost آماده نیست')


def make_action(base):
    def action_provision_gost_exit(args):
        eid = base.safe_id(str(args.get('id', '')))
        _ensure_runtime(base)
        iran_public = base._ipv4(args.get('iran_public'), 'iran_public')
        exit_public = base._ipv4(args.get('exit_public') or base.public_ip(), 'exit_public')
        tunnel_ip = str(args.get('tunnel_ip', '')).strip()
        peer_ip = base._ipv4(args.get('peer_ip'), 'peer_ip')
        iface_addr = ipaddress.ip_interface(tunnel_ip)
        if iface_addr.version != 4 or ipaddress.ip_address(peer_ip) not in iface_addr.network:
            raise ValueError('invalid GOST tunnel addressing')
        listen_port = int(args.get('listen_port') or 443)
        if not 1 <= listen_port <= 65535:
            raise ValueError('invalid listen_port')
        mtu = int(args.get('mtu') or 1300)
        if not 1000 <= mtu <= 1400:
            raise ValueError('GOST mtu must be between 1000 and 1400')
        username = str(args.get('gost_username') or '')
        password = str(args.get('gost_password') or '')
        ws_path = str(args.get('ws_path') or '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{3,64}', username):
            raise ValueError('invalid GOST username')
        if not re.fullmatch(r'[A-Za-z0-9_-]{12,128}', password):
            raise ValueError('invalid GOST password')
        if not re.fullmatch(r'/[A-Za-z0-9/_-]{4,160}', ws_path):
            raise ValueError('invalid GOST websocket path')

        base._stop_exit_services(eid)
        if not base._port_free(exit_public, listen_port):
            raise RuntimeError(f'TCP/{listen_port} روی سرور خارج اشغال است')

        base.CERTS.mkdir(parents=True, exist_ok=True)
        base.EXITS.mkdir(parents=True, exist_ok=True)
        cert = base.CERTS / (eid + '-gost.crt')
        key = base.CERTS / (eid + '-gost.key')
        if not cert.exists() or not key.exists():
            rc, out, err = base.run([
                'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '3650',
                '-subj', '/CN=' + exit_public,
                '-addext', 'subjectAltName=IP:' + exit_public,
                '-keyout', str(key), '-out', str(cert),
            ], timeout=40, check=False)
            if rc != 0:
                raise RuntimeError(err or out or 'GOST certificate generation failed')
            os.chmod(key, 0o600)
            os.chmod(cert, 0o644)

        internal_port = _internal_port(eid)
        cfg = {
            'role': 'gost-server', 'transport': 'gost-wss', 'mode': 'gost-wss',
            'engine': 'gost', 'gost_version': '3.3.0',
            'id': eid, 'interface': base._iface(eid), 'tunnel_ip': tunnel_ip, 'peer_ip': peer_ip,
            'local_public': exit_public, 'remote_public': iran_public,
            'listen_port': listen_port, 'internal_port': internal_port, 'mtu': mtu,
            'gost_username': username, 'gost_password': password, 'ws_path': ws_path,
            'cert': str(cert), 'key': str(key),
        }
        base.atomic_json(base.EXITS / (eid + '.json'), cfg)
        base.run(['systemctl', 'daemon-reload'], timeout=20, check=False)
        base.run(['systemctl', 'enable', 'tp27-link@' + eid + '.service'], timeout=20, check=False)
        rc, out, err = base.run(['systemctl', 'restart', 'tp27-link@' + eid + '.service'], timeout=35, check=False)
        if rc != 0:
            raise RuntimeError(err or out or 'GOST exit service start failed')
        time.sleep(3)
        active = subprocess.run(['systemctl', 'is-active', '--quiet', 'tp27-link@' + eid + '.service']).returncode == 0
        _, addr_out, _ = base.run(['ip', '-br', '-4', 'addr', 'show', 'dev', base._iface(eid)], timeout=10, check=False)
        _, route_out, _ = base.run(['ip', '-4', 'route', 'show', 'dev', base._iface(eid)], timeout=10, check=False)
        _, link_out, _ = base.run(['ip', '-s', 'link', 'show', 'dev', base._iface(eid)], timeout=10, check=False)
        if not active:
            _, logs, logs_err = base.run(['journalctl', '-u', 'tp27-link@' + eid + '.service', '-n', '40', '--no-pager', '-o', 'cat'], timeout=15, check=False)
            raise RuntimeError('GOST exit service Active نشد: ' + (logs or logs_err)[-3000:])
        return {
            'id': eid, 'mode': 'gost-wss', 'transport': 'gost-wss', 'engine': 'gost',
            'gost_version': '3.3.0', 'listen_port': listen_port, 'internal_port': internal_port,
            'service_active': active, 'interface': base._iface(eid), 'interface_state': addr_out,
            'route_state': route_out, 'link_state': link_out, 'tunnel_ip': tunnel_ip, 'peer_ip': peer_ip,
            'server_cert_pem': cert.read_text(encoding='utf-8'), 'ws_path': ws_path,
            'agent_version': getattr(base, 'VERSION', 'unknown'),
        }

    return action_provision_gost_exit


def install(base):
    if getattr(base, '_tp27_gost_addon_installed', False):
        return base
    base.ACTIONS['provision_gost_exit'] = make_action(base)

    original_delete = base.ACTIONS.get('delete_exit')
    if original_delete:
        def delete_exit(args):
            eid = base.safe_id(str(args.get('id', '')))
            result = original_delete(args)
            removed = result.setdefault('removed', []) if isinstance(result, dict) else []
            for suffix in ('-gost.crt', '-gost.key'):
                path = base.CERTS / (eid + suffix)
                try:
                    if path.exists():
                        path.unlink()
                        removed.append(str(path))
                except Exception:
                    pass
            return result
        base.ACTIONS['delete_exit'] = delete_exit

    base._tp27_gost_addon_installed = True
    return base
