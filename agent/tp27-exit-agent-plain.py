#!/usr/bin/env python3
import argparse
import hashlib
import hmac
import importlib.util
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests

PLAIN_VERSION = '1.5.0'
SPEED_AGENT_VERSION = '1.7.0'
POLL_SECONDS = max(0.5, min(float(os.environ.get('TP27_AGENT_POLL_SECONDS', '0.5') or 0.5), 5.0))
REMOTE_GRE_READY_DEADLINE = 3.0
REMOTE_GRE_READY_POLL = 0.05
BASE_AGENT = Path('/opt/tp27-exit-agent/tp27-exit-agent.py')
GOST_ADDON = Path('/opt/tp27-exit-agent/tp27_gost_addon.py')
OPENVPN_ADDON = Path('/opt/tp27-exit-agent/tp27_openvpn_addon.py')
BENCHMARK_ADDON = Path('/opt/tp27-exit-agent/tp27_benchmark_addon.py')
CONFIG = Path('/etc/tp27/exit-agent.json')
SAFE_ACTIONS = {
    'status',
    'provision_gre_exit',
    'provision_tls_exit',
    'provision_gost_exit',
    'provision_openvpn_exit',
    'restart_link',
    'delete_exit',
    'upgrade_agent',
}
BENCHMARK_NET = ipaddress.ip_network('10.253.0.0/16')
BENCHMARK_PORT = 52701


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
# The universal runner owns the low-latency control loop. Advertising a new
# base generation lets the panel upgrade older agents without touching
# unrelated VPN services or already-running TP27 data planes.
BASE.VERSION = SPEED_AGENT_VERSION
if GOST_ADDON.exists():
    GOST = load_module(GOST_ADDON, 'tp27_gost_addon_plain')
    GOST.install(BASE)
if OPENVPN_ADDON.exists():
    OPENVPN = load_module(OPENVPN_ADDON, 'tp27_openvpn_addon_plain')
    OPENVPN.install(BASE)
BASE_VERSION = getattr(BASE, 'VERSION', 'unknown')


def _elapsed_ms(started):
    return max(0, int(round((time.monotonic() - started) * 1000)))


def _fast_stop_exit_services(eid):
    services = (
        'tp27-link@' + eid + '.service',
        'tp27-framed@' + eid + '.service',
    )
    # One systemd transaction per operation instead of four independent
    # round trips. Both instances are mutually exclusive for a tunnel ID.
    BASE.run(['systemctl', 'disable', '--now', *services], timeout=25, check=False)
    BASE.run(['systemctl', 'reset-failed', *services], timeout=15, check=False)
    BASE.run(['ip', 'link', 'del', BASE._iface(eid)], timeout=10, check=False)


def _fast_action_provision_gre_exit(args):
    started = time.monotonic()
    eid = BASE.safe_id(str(args.get('id', '')))
    BASE.ensure_runtime()
    iran_public = BASE._ipv4(args.get('iran_public'), 'iran_public')
    exit_public = BASE._ipv4(args.get('exit_public') or BASE.public_ip(), 'exit_public')
    tunnel_ip = str(args.get('tunnel_ip', '')).strip()
    peer_ip = BASE._ipv4(args.get('peer_ip'), 'peer_ip')
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

    BASE.EXITS.mkdir(parents=True, exist_ok=True)

    cleanup_started = time.monotonic()
    _fast_stop_exit_services(eid)
    cleanup_ms = _elapsed_ms(cleanup_started)

    iface = BASE._iface(eid)
    cfg = {
        'role': 'gre-exit', 'id': eid, 'interface': iface, 'tunnel_ip': tunnel_ip,
        'peer_ip': peer_ip, 'local_public': exit_public, 'remote_public': iran_public,
        'gre_key': gre_key, 'mtu': mtu, 'txqueuelen': int(args.get('txqueuelen', 1000)),
        'transport': transport, 'fou_port': fou_port,
    }
    BASE.atomic_json(BASE.EXITS / (eid + '.json'), cfg)

    start_started = time.monotonic()
    svc = 'tp27-link@' + eid + '.service'
    rc, out, err = BASE.run(['systemctl', 'enable', '--now', svc], timeout=30, check=False)
    if rc != 0:
        raise RuntimeError(err or out or 'GRE exit link start failed')

    expected_ip = str(iface_addr.ip)
    ready = False
    addr_out = ''
    deadline = time.monotonic() + REMOTE_GRE_READY_DEADLINE
    while time.monotonic() < deadline:
        addr_rc, addr_out, _ = BASE.run(
            ['ip', '-br', '-4', 'addr', 'show', 'dev', iface],
            timeout=3,
            check=False,
        )
        if addr_rc == 0 and expected_ip in (addr_out or ''):
            ready = True
            break
        time.sleep(REMOTE_GRE_READY_POLL)

    active = subprocess.run(
        ['systemctl', 'is-active', '--quiet', svc],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0
    start_ready_ms = _elapsed_ms(start_started)

    if not active or not ready:
        _, logs, logs_err = BASE.run(
            ['journalctl', '-u', svc, '-n', '50', '--no-pager', '-o', 'cat'],
            timeout=15,
            check=False,
        )
        state = 'inactive' if not active else 'interface-not-ready'
        raise RuntimeError(
            'GRE exit readiness failed (' + state + '): ' +
            (logs or logs_err or addr_out or 'no diagnostics')[-3200:]
        )

    ping_started = time.monotonic()
    ping_rc, ping_out, ping_err = BASE.run(
        ['ping', '-I', iface, '-c', '1', '-W', '1', '-w', '2', peer_ip],
        timeout=3,
        check=False,
    )
    ping_ms = _elapsed_ms(ping_started)

    diag_started = time.monotonic()
    _, route_out, _ = BASE.run(
        ['ip', '-4', 'route', 'show', 'dev', iface], timeout=10, check=False,
    )
    _, link_out, _ = BASE.run(
        ['ip', '-s', 'link', 'show', 'dev', iface], timeout=10, check=False,
    )
    diagnostics_ms = _elapsed_ms(diag_started)

    return {
        'id': eid,
        'mode': 'gre-fou' if transport == 'gre-fou' else 'gre-native',
        'transport': transport,
        'fou_port': fou_port or None,
        'service_active': active,
        'health_ping': ping_rc == 0,
        'ping_output': ping_out or ping_err,
        'interface': iface,
        'interface_state': addr_out,
        'route_state': route_out,
        'link_state': link_out,
        'tunnel_ip': tunnel_ip,
        'peer_ip': peer_ip,
        'gre_key': gre_key,
        'agent_version': BASE.VERSION,
        'remote_timing_v1': {
            'total_ms': _elapsed_ms(started),
            'cleanup_ms': cleanup_ms,
            'start_ready_ms': start_ready_ms,
            'diagnostic_ping_ms': ping_ms,
            'diagnostics_ms': diagnostics_ms,
        },
    }


# Install the low-latency primitives after optional add-ons are registered.
# Add-on actions keep using BASE._stop_exit_services dynamically, so they gain
# the batched cleanup without changing their protocol-specific behavior.
BASE._stop_exit_services = _fast_stop_exit_services
BASE.ACTIONS['provision_gre_exit'] = _fast_action_provision_gre_exit


def _bench_ip(value, name):
    try:
        addr = ipaddress.ip_address(str(value or '').split('/')[0].strip())
    except Exception:
        raise ValueError(f'{name} نامعتبر است')
    if addr.version != 4 or addr not in BENCHMARK_NET:
        raise ValueError(f'{name} باید داخل شبکه داخلی TP27 باشد')
    return str(addr)


def _bench_iperf():
    path = BASE.shutil_which('iperf3')
    if path:
        return path
    env = dict(os.environ)
    env['DEBIAN_FRONTEND'] = 'noninteractive'
    for cmd in (['apt-get', 'update', '-qq'], ['apt-get', 'install', '-y', '-qq', 'iperf3']):
        p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=180, env=env)
        if p.returncode != 0:
            raise RuntimeError('نصب خودکار iperf3 ناموفق بود: ' + (p.stderr or p.stdout or 'apt failed')[-1000:])
    path = BASE.shutil_which('iperf3')
    if not path:
        raise RuntimeError('iperf3 پیدا نشد')
    return path


def _bench_ping(text):
    loss = None
    avg = None
    jitter = None
    m = re.search(r'(\d+(?:\.\d+)?)%\s+packet loss', text or '')
    if m:
        loss = float(m.group(1))
    m = re.search(r'(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+)', text or '')
    if m:
        avg = float(m.group(2))
        jitter = float(m.group(4))
    return {'ping_ms': avg, 'jitter_ms': jitter, 'loss_pct': loss}


def _bench_iperf_run(iperf, target, source, port, duration, parallel, reverse=False):
    cmd = [iperf, '-c', target, '-B', source, '-p', str(port), '-t', str(duration),
           '-O', '1', '-P', str(parallel), '-J', '--connect-timeout', '5000']
    if reverse:
        cmd.append('-R')
    rc, out, err = BASE.run(cmd, timeout=duration + 25, check=False)
    if rc != 0:
        raise RuntimeError((err or out or 'iperf3 failed')[-1600:])
    try:
        data = json.loads(out)
    except Exception:
        raise RuntimeError('خروجی JSON از iperf3 دریافت نشد')
    if data.get('error'):
        raise RuntimeError(str(data['error'])[:1400])
    end = data.get('end') or {}
    receiver = end.get('sum_received') or {}
    sender = end.get('sum_sent') or {}
    bps = receiver.get('bits_per_second')
    if bps is None:
        bps = sender.get('bits_per_second')
    if bps is None:
        raise RuntimeError('Throughput از iperf3 قابل خواندن نیست')
    return {
        'mbps': round(float(bps) / 1_000_000.0, 2),
        'retransmits': int(sender.get('retransmits') or 0),
    }


def _bench_quality(ping_ms, loss_pct, download_mbps, upload_mbps):
    ping_ms = 9999.0 if ping_ms is None else float(ping_ms)
    loss_pct = 100.0 if loss_pct is None else float(loss_pct)
    floor = min(float(download_mbps or 0), float(upload_mbps or 0))
    if loss_pct <= 1 and ping_ms <= 90 and floor >= 80:
        return 'excellent'
    if loss_pct <= 2 and ping_ms <= 150 and floor >= 30:
        return 'good'
    if loss_pct <= 5 and ping_ms <= 220 and floor >= 10:
        return 'fair'
    return 'poor'


def _embedded_benchmark(args):
    eid = BASE.safe_id(str(args.get('id', '')))
    target = _bench_ip(args.get('target_ip'), 'target_ip')
    source = _bench_ip(args.get('source_ip'), 'source_ip')
    duration = max(2, min(int(args.get('duration', 4) or 4), 10))
    parallel = max(1, min(int(args.get('parallel', 1) or 1), 4))
    port = int(args.get('port', BENCHMARK_PORT) or BENCHMARK_PORT)
    if not 20000 <= port <= 60999:
        raise ValueError('benchmark port نامعتبر است')
    iperf = _bench_iperf()
    rc, pout, perr = BASE.run(['ping', '-I', source, '-c', '6', '-i', '0.2', '-W', '1', target],
                              timeout=10, check=False)
    ping = _bench_ping(pout or perr or '')
    if rc != 0 and ping.get('loss_pct') in (None, 100.0):
        raise RuntimeError('Peer Ping قبل از Benchmark شکست خورد')
    download = _bench_iperf_run(iperf, target, source, port, duration, parallel, reverse=False)
    upload = _bench_iperf_run(iperf, target, source, port, duration, parallel, reverse=True)
    result = {
        'benchmark_v1': True,
        'id': eid,
        'target_ip': target,
        'source_ip': source,
        'duration': duration,
        'parallel': parallel,
        **ping,
        'download_mbps': download['mbps'],
        'upload_mbps': upload['mbps'],
        'download_retransmits': download['retransmits'],
        'upload_retransmits': upload['retransmits'],
    }
    result['quality'] = _bench_quality(result.get('ping_ms'), result.get('loss_pct'),
                                       result.get('download_mbps'), result.get('upload_mbps'))
    return result


def install_benchmark_action():
    if BENCHMARK_ADDON.exists():
        BENCHMARK = load_module(BENCHMARK_ADDON, 'tp27_benchmark_addon_plain')
        BENCHMARK.install(BASE)
        return
    previous = BASE.ACTIONS.get('status')
    if previous is None:
        raise RuntimeError('base status action is missing')

    def status_or_benchmark(args):
        args = args or {}
        if args.get('benchmark_v1'):
            return _embedded_benchmark(args)
        return previous(args)

    BASE.ACTIONS['status'] = status_or_benchmark


install_benchmark_action()


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
        'poll_seconds': max(1, min(int(data.get('poll_seconds', 1)), 30)),
        'version': BASE_VERSION,
        'plain_version': PLAIN_VERSION,
    })
    print('AGENT_ID=' + aid)
    print('CONTROLLER=' + controller_url.rstrip('/'))
    print('TRANSPORT=plain-hmac-v1')


def post_result(cfg, command_id, ok, result=None, error=None):
    signed_post(cfg, '/v1/result', {'command_id': command_id, 'ok': bool(ok), 'result': result or {}, 'error': error})


def run_https_fast():
    cfg = BASE.load_config()
    s = BASE.api_session(cfg)
    last_heartbeat = 0
    failures = 0
    while True:
        try:
            # Poll first so provisioning commands are never queued behind the
            # comparatively expensive heartbeat/status collection path.
            r = s.post(BASE.api_url(cfg, '/api/agent/poll'), json={}, timeout=30)
            if r.status_code >= 400:
                raise RuntimeError('poll HTTP %s: %s' % (r.status_code, r.text[:300]))
            command = r.json().get('command')
            if command:
                cid = command.get('id', '')
                handler = BASE.ACTIONS.get(command.get('action', ''))
                if not handler:
                    BASE.post_result(s, cfg, cid, False, error='unsupported action')
                else:
                    try:
                        result = handler(command.get('args') or {})
                        BASE.post_result(s, cfg, cid, True, result=result)
                    except Exception as exc:
                        BASE.post_result(s, cfg, cid, False, error=str(exc)[:10000])
                failures = 0
                time.sleep(POLL_SECONDS)
                continue

            now = time.time()
            if now - last_heartbeat >= 20:
                r = s.post(BASE.api_url(cfg, '/api/agent/heartbeat'), json=BASE.heartbeat_payload(), timeout=25)
                if r.status_code >= 400:
                    raise RuntimeError('heartbeat HTTP %s: %s' % (r.status_code, r.text[:300]))
                last_heartbeat = now
            failures = 0
            time.sleep(POLL_SECONDS)
        except requests.exceptions.SSLError as exc:
            print('TLS ERROR: ' + str(exc), file=sys.stderr, flush=True)
            time.sleep(30)
        except Exception as exc:
            failures += 1
            delay = min(60, max(5, failures * 5))
            print('agent error: %s; retry=%ss' % (exc, delay), file=sys.stderr, flush=True)
            time.sleep(delay)


def run_forever():
    raw_cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    if raw_cfg.get('transport') != 'plain-hmac-v1':
        return run_https_fast()

    cfg = load_config()
    last_heartbeat = 0
    failures = 0
    while True:
        try:
            # Same low-latency order as HTTPS: commands first, heartbeat only
            # while idle. The poll request itself keeps last_seen fresh.
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
                time.sleep(POLL_SECONDS)
                continue

            now = time.time()
            if now - last_heartbeat >= 20:
                signed_post(cfg, '/v1/heartbeat', BASE.heartbeat_payload(), timeout=25)
                last_heartbeat = now
            failures = 0
            time.sleep(POLL_SECONDS)
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
