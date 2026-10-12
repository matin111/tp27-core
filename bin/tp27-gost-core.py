#!/usr/bin/env python3
import argparse
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

RUNTIME_VERSION = '1.0.0'
GOST_BIN = Path('/usr/local/bin/tp27-gost')
RUN_DIR = Path('/run/tp27')
MAX_ID = 24


def run(cmd, check=False):
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or 'command failed').strip())
    return p.returncode, (p.stdout or '').strip(), (p.stderr or '').strip()


def load_cfg(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    eid = str(data.get('id') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,%d}' % MAX_ID, eid):
        raise RuntimeError('invalid id')
    if data.get('transport') != 'gost-wss':
        raise RuntimeError('invalid transport')
    role = str(data.get('role') or '')
    if role not in {'gost-server', 'gost-client'}:
        raise RuntimeError('invalid GOST role')
    iface = str(data.get('interface') or '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', iface):
        raise RuntimeError('invalid interface')
    tun = ipaddress.ip_interface(str(data.get('tunnel_ip') or ''))
    peer = ipaddress.ip_address(str(data.get('peer_ip') or ''))
    if tun.version != 4 or peer.version != 4:
        raise RuntimeError('GOST core currently requires IPv4 tunnel addresses')
    if peer not in tun.network or peer == tun.ip:
        raise RuntimeError('peer_ip must be the other address in the tunnel subnet')
    listen_port = int(data.get('listen_port') or 0)
    internal_port = int(data.get('internal_port') or 0)
    mtu = int(data.get('mtu') or 1300)
    if not 1 <= listen_port <= 65535:
        raise RuntimeError('invalid listen_port')
    if not 1024 <= internal_port <= 65535:
        raise RuntimeError('invalid internal_port')
    if not 1000 <= mtu <= 1400:
        raise RuntimeError('invalid mtu')
    ws_path = str(data.get('ws_path') or '')
    if not re.fullmatch(r'/[A-Za-z0-9/_-]{4,160}', ws_path):
        raise RuntimeError('invalid ws_path')
    username = str(data.get('gost_username') or '')
    password = str(data.get('gost_password') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{3,64}', username):
        raise RuntimeError('invalid GOST username')
    if not re.fullmatch(r'[A-Za-z0-9_-]{12,128}', password):
        raise RuntimeError('invalid GOST password')
    data['_local_ip'] = str(tun.ip)
    data['_peer_ip'] = str(peer)
    data['_net'] = str(tun)
    data['listen_port'] = listen_port
    data['internal_port'] = internal_port
    data['mtu'] = mtu
    return data


def ensure_chain(table, parent, chain):
    if not shutil_which('iptables'):
        return
    run(['iptables', '-w', '5', '-t', table, '-N', chain])
    rc, _, _ = run(['iptables', '-w', '5', '-t', table, '-C', parent, '-j', chain])
    if rc != 0:
        run(['iptables', '-w', '5', '-t', table, '-I', parent, '1', '-j', chain])


def ensure_rule(table, chain, args):
    if not shutil_which('iptables'):
        return
    rc, _, _ = run(['iptables', '-w', '5', '-t', table, '-C', chain] + args)
    if rc != 0:
        run(['iptables', '-w', '5', '-t', table, '-A', chain] + args)


def remove_rule(table, chain, args):
    if not shutil_which('iptables'):
        return
    while True:
        rc, _, _ = run(['iptables', '-w', '5', '-t', table, '-C', chain] + args)
        if rc != 0:
            break
        rc, _, _ = run(['iptables', '-w', '5', '-t', table, '-D', chain] + args)
        if rc != 0:
            break


def shutil_which(name):
    for directory in os.environ.get('PATH', '/usr/sbin:/usr/bin:/sbin:/bin').split(':'):
        path = Path(directory) / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def external_rules(cfg):
    if cfg['role'] != 'gost-server':
        return []
    local = str(ipaddress.ip_address(str(cfg.get('local_public') or '')))
    remote = str(ipaddress.ip_address(str(cfg.get('remote_public') or '')))
    port = str(cfg['listen_port'])
    return [
        ('filter', 'TP27_GOST_IN', ['-p', 'tcp', '-s', remote + '/32', '-d', local + '/32', '--dport', port, '-j', 'ACCEPT']),
        ('filter', 'TP27_GOST_OUT', ['-p', 'tcp', '-s', local + '/32', '-d', remote + '/32', '--sport', port, '-j', 'ACCEPT']),
    ]


def tun_rules(cfg):
    iface = cfg['interface']
    return [
        ('filter', 'TP27_GOST_IN', ['-i', iface, '-j', 'ACCEPT']),
        ('filter', 'TP27_GOST_OUT', ['-o', iface, '-j', 'ACCEPT']),
        ('filter', 'TP27_GOST_FWD', ['-i', iface, '-j', 'ACCEPT']),
        ('filter', 'TP27_GOST_FWD', ['-o', iface, '-j', 'ACCEPT']),
    ]


def prepare_chains():
    ensure_chain('filter', 'INPUT', 'TP27_GOST_IN')
    ensure_chain('filter', 'OUTPUT', 'TP27_GOST_OUT')
    ensure_chain('filter', 'FORWARD', 'TP27_GOST_FWD')


def apply_rules(rows):
    for table, chain, args in rows:
        ensure_rule(table, chain, args)


def cleanup_rules(rows):
    for table, chain, args in reversed(rows):
        remove_rule(table, chain, args)


def build_gost_config(cfg):
    common_tun_listener = {
        'type': 'tun',
        'metadata': {
            'name': cfg['interface'],
            'net': cfg['_net'],
            'mtu': cfg['mtu'],
        },
    }
    if cfg['role'] == 'gost-server':
        cert = str(cfg.get('cert') or '')
        key = str(cfg.get('key') or '')
        if not Path(cert).is_file() or not Path(key).is_file():
            raise RuntimeError('GOST server certificate/key missing')
        return {
            'services': [
                {
                    'name': 'tp27-tun-' + cfg['id'],
                    'addr': '127.0.0.1:%d' % cfg['internal_port'],
                    'handler': {'type': 'tun'},
                    'listener': common_tun_listener,
                },
                {
                    'name': 'tp27-wss-' + cfg['id'],
                    'addr': '%s:%d' % (cfg['local_public'], cfg['listen_port']),
                    'handler': {
                        'type': 'relay',
                        'auth': {
                            'username': cfg['gost_username'],
                            'password': cfg['gost_password'],
                        },
                        'metadata': {'bind': True, 'nodelay': True},
                    },
                    'listener': {
                        'type': 'wss',
                        'tls': {'certFile': cert, 'keyFile': key},
                        'metadata': {
                            'path': cfg['ws_path'],
                            'handshakeTimeout': '10s',
                            'readHeaderTimeout': '10s',
                        },
                    },
                },
            ],
            'log': {'level': 'info'},
        }

    ca_file = str(cfg.get('ca_file') or '')
    if not Path(ca_file).is_file():
        raise RuntimeError('GOST client CA file missing')
    return {
        'services': [
            {
                'name': 'tp27-tun-' + cfg['id'],
                'addr': ':0',
                'handler': {
                    'type': 'tun',
                    'chain': 'tp27-chain-' + cfg['id'],
                    'metadata': {'keepAlive': True, 'ttl': '10s'},
                },
                'listener': common_tun_listener,
                'forwarder': {
                    'nodes': [
                        {'name': 'tp27-target', 'addr': '127.0.0.1:%d' % cfg['internal_port']},
                    ]
                },
            },
        ],
        'chains': [
            {
                'name': 'tp27-chain-' + cfg['id'],
                'hops': [
                    {
                        'name': 'tp27-hop',
                        'nodes': [
                            {
                                'name': 'tp27-exit',
                                'addr': '%s:%d' % (cfg['remote_public'], cfg['listen_port']),
                                'connector': {
                                    'type': 'relay',
                                    'auth': {
                                        'username': cfg['gost_username'],
                                        'password': cfg['gost_password'],
                                    },
                                    'metadata': {'nodelay': True},
                                },
                                'dialer': {
                                    'type': 'wss',
                                    'tls': {
                                        'caFile': ca_file,
                                        'secure': True,
                                        'serverName': cfg['remote_public'],
                                    },
                                    'metadata': {
                                        'path': cfg['ws_path'],
                                        'keepAlive': True,
                                        'ttl': '10s',
                                        'handshakeTimeout': '10s',
                                    },
                                },
                            },
                        ],
                    },
                ],
            },
        ],
        'log': {'level': 'info'},
    }


def wait_interface(proc, iface, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError('GOST exited early rc=%s' % proc.returncode)
        rc, _, _ = run(['ip', 'link', 'show', iface])
        if rc == 0:
            run(['sysctl', '-q', '-w', 'net.ipv4.conf.%s.rp_filter=0' % iface])
            return
        time.sleep(0.25)
    raise RuntimeError('GOST TUN interface did not appear: ' + iface)


def main():
    parser = argparse.ArgumentParser(prog='tp27-gost-core')
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    cfg = load_cfg(args.config)

    if not GOST_BIN.is_file() or not os.access(GOST_BIN, os.X_OK):
        raise RuntimeError('TP27 GOST binary missing; run tp27-gost-install.sh')

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(RUN_DIR, 0o755)
    runtime_cfg = RUN_DIR / ('gost-' + cfg['id'] + '.json')
    tmp = runtime_cfg.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(build_gost_config(cfg), indent=2) + '\n', encoding='utf-8')
    os.chmod(tmp, 0o600)
    os.replace(tmp, runtime_cfg)

    prepare_chains()
    ext_rows = external_rules(cfg)
    apply_rules(ext_rows)
    tun_rows = []
    child = None
    stopping = False

    def stop_child(_signum=None, _frame=None):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        if child and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop_child)
    signal.signal(signal.SIGINT, stop_child)

    try:
        print(
            'TP27_GOST_START version=%s id=%s role=%s iface=%s port=%s path=%s' % (
                RUNTIME_VERSION, cfg['id'], cfg['role'], cfg['interface'],
                cfg['listen_port'], cfg['ws_path'],
            ),
            flush=True,
        )
        child = subprocess.Popen(
            [str(GOST_BIN), '-C', str(runtime_cfg)],
            stdout=None,
            stderr=None,
        )
        wait_interface(child, cfg['interface'])
        tun_rows = tun_rules(cfg)
        apply_rules(tun_rows)
        rc, addr, _ = run(['ip', '-br', '-4', 'addr', 'show', 'dev', cfg['interface']])
        rc2, route, _ = run(['ip', '-4', 'route', 'show', 'dev', cfg['interface']])
        print(
            'TP27_GOST_READY id=%s iface=%s local=%s peer=%s addr="%s" route="%s"' % (
                cfg['id'], cfg['interface'], cfg['_local_ip'], cfg['_peer_ip'], addr, route,
            ),
            flush=True,
        )
        while True:
            try:
                code = child.wait(timeout=1)
                return int(code)
            except subprocess.TimeoutExpired:
                if stopping:
                    try:
                        return int(child.wait(timeout=8))
                    except subprocess.TimeoutExpired:
                        child.kill()
                        return int(child.wait())
    finally:
        cleanup_rules(tun_rows)
        cleanup_rules(ext_rows)
        try:
            runtime_cfg.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print('TP27_GOST_FATAL %s: %s' % (type(exc).__name__, exc), file=sys.stderr, flush=True)
        raise SystemExit(1)
