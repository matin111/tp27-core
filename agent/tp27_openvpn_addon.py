#!/usr/bin/env python3
import ipaddress
import os
import re
import subprocess
import time
from pathlib import Path

OPENVPN_CORE = Path('/usr/local/lib/tp27/tp27-openvpn-core')


def _valid_static_key(value):
    text = str(value or '').strip() + '\n'
    if len(text) > 12000:
        raise ValueError('OpenVPN static key too large')
    if '-----BEGIN OpenVPN Static key V1-----' not in text or '-----END OpenVPN Static key V1-----' not in text:
        raise ValueError('invalid OpenVPN static key')
    body = text.split('-----BEGIN OpenVPN Static key V1-----', 1)[1].split('-----END OpenVPN Static key V1-----', 1)[0]
    hexdata = ''.join(x.strip() for x in body.splitlines() if x.strip())
    if not re.fullmatch(r'[0-9A-Fa-f]{512}', hexdata):
        raise ValueError('OpenVPN static key must contain 2048 bits')
    return text


def _ensure_runtime(base):
    if not OPENVPN_CORE.exists():
        raise RuntimeError('OpenVPN TP27 runtime نصب نیست؛ Installer جدید TP27 را اجرا کنید')
    if not base.shutil_which('openvpn'):
        raise RuntimeError('OpenVPN binary نصب نیست؛ Installer جدید TP27 را اجرا کنید')


def make_action(base):
    def action_provision_openvpn_exit(args):
        eid = base.safe_id(str(args.get('id', '')))
        _ensure_runtime(base)
        iran_public = base._ipv4(args.get('iran_public'), 'iran_public')
        exit_public = base._ipv4(args.get('exit_public') or base.public_ip(), 'exit_public')
        tunnel_ip = str(args.get('tunnel_ip', '')).strip()
        peer_ip = base._ipv4(args.get('peer_ip'), 'peer_ip')
        iface_addr = ipaddress.ip_interface(tunnel_ip)
        if iface_addr.version != 4 or ipaddress.ip_address(peer_ip) not in iface_addr.network:
            raise ValueError('invalid OpenVPN tunnel addressing')
        listen_port = int(args.get('listen_port') or 28443)
        if not 1 <= listen_port <= 65535:
            raise ValueError('invalid listen_port')
        mtu = int(args.get('mtu') or 1300)
        if not 1000 <= mtu <= 1400:
            raise ValueError('OpenVPN mtu must be between 1000 and 1400')
        key_text = _valid_static_key(args.get('static_key'))

        base._stop_exit_services(eid)
        if not base._port_free(exit_public, listen_port):
            raise RuntimeError(f'TCP/{listen_port} روی سرور خارج اشغال است')

        base.CERTS.mkdir(parents=True, exist_ok=True)
        base.EXITS.mkdir(parents=True, exist_ok=True)
        secret = base.CERTS / (eid + '-openvpn.key')
        base.atomic_text(secret, key_text, 0o600)

        cfg = {
            'role': 'openvpn-server', 'transport': 'openvpn-tcp', 'mode': 'openvpn-tcp',
            'engine': 'openvpn', 'id': eid, 'interface': base._iface(eid),
            'tunnel_ip': tunnel_ip, 'peer_ip': peer_ip,
            'local_public': exit_public, 'remote_public': iran_public,
            'listen_port': listen_port, 'mtu': mtu, 'secret_file': str(secret),
        }
        base.atomic_json(base.EXITS / (eid + '.json'), cfg)
        base.run(['systemctl', 'daemon-reload'], timeout=20, check=False)
        base.run(['systemctl', 'enable', 'tp27-link@' + eid + '.service'], timeout=20, check=False)
        rc, out, err = base.run(['systemctl', 'restart', 'tp27-link@' + eid + '.service'], timeout=35, check=False)
        if rc != 0:
            raise RuntimeError(err or out or 'OpenVPN exit service start failed')

        time.sleep(2)
        active = subprocess.run(['systemctl', 'is-active', '--quiet', 'tp27-link@' + eid + '.service']).returncode == 0
        _, addr_out, _ = base.run(['ip', '-br', '-4', 'addr', 'show', 'dev', base._iface(eid)], timeout=10, check=False)
        _, route_out, _ = base.run(['ip', '-4', 'route', 'show', 'dev', base._iface(eid)], timeout=10, check=False)
        _, link_out, _ = base.run(['ip', '-s', 'link', 'show', 'dev', base._iface(eid)], timeout=10, check=False)
        if not active:
            _, logs, logs_err = base.run(['journalctl', '-u', 'tp27-link@' + eid + '.service', '-n', '60', '--no-pager', '-o', 'cat'], timeout=15, check=False)
            raise RuntimeError('OpenVPN exit service Active نشد: ' + (logs or logs_err)[-3500:])
        return {
            'id': eid, 'mode': 'openvpn-tcp', 'transport': 'openvpn-tcp', 'engine': 'openvpn',
            'listen_port': listen_port, 'service_active': active,
            'interface': base._iface(eid), 'interface_state': addr_out,
            'route_state': route_out, 'link_state': link_out,
            'tunnel_ip': tunnel_ip, 'peer_ip': peer_ip,
            'agent_version': getattr(base, 'VERSION', 'unknown'),
        }

    return action_provision_openvpn_exit


def install(base):
    if getattr(base, '_tp27_openvpn_addon_installed', False):
        return base
    base.ACTIONS['provision_openvpn_exit'] = make_action(base)

    original_delete = base.ACTIONS.get('delete_exit')
    if original_delete:
        def delete_exit(args):
            eid = base.safe_id(str(args.get('id', '')))
            result = original_delete(args)
            removed = result.setdefault('removed', []) if isinstance(result, dict) else []
            path = base.CERTS / (eid + '-openvpn.key')
            try:
                if path.exists():
                    path.unlink()
                    removed.append(str(path))
            except Exception:
                pass
            return result
        base.ACTIONS['delete_exit'] = delete_exit

    base._tp27_openvpn_addon_installed = True
    return base
