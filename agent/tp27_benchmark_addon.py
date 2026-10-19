#!/usr/bin/env python3
import ipaddress
import json
import os
import re
import subprocess

BENCHMARK_NET = ipaddress.ip_network('10.253.0.0/16')
DEFAULT_PORT = 52701


def _ipv4_in_pool(value, name):
    try:
        addr = ipaddress.ip_address(str(value or '').split('/')[0].strip())
    except Exception:
        raise ValueError(f'{name} نامعتبر است')
    if addr.version != 4 or addr not in BENCHMARK_NET:
        raise ValueError(f'{name} باید داخل شبکه داخلی TP27 باشد')
    return str(addr)


def _ensure_iperf3(base):
    path = base.shutil_which('iperf3')
    if path:
        return path

    env = dict(os.environ)
    env['DEBIAN_FRONTEND'] = 'noninteractive'
    attempts = [
        ['apt-get', 'update', '-qq'],
        ['apt-get', 'install', '-y', '-qq', 'iperf3'],
    ]
    for cmd in attempts:
        p = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
            env=env,
        )
        if p.returncode != 0:
            raise RuntimeError(
                'iperf3 نصب نیست و نصب خودکار ناموفق بود: ' +
                (p.stderr or p.stdout or 'apt failed')[-1200:]
            )

    path = base.shutil_which('iperf3')
    if not path:
        raise RuntimeError('iperf3 بعد از نصب پیدا نشد')
    return path


def _parse_ping(text):
    loss = None
    avg = None
    jitter = None

    m = re.search(r'(\d+(?:\.\d+)?)%\s+packet loss', text or '')
    if m:
        loss = float(m.group(1))

    m = re.search(
        r'(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = '
        r'([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+)',
        text or '',
    )
    if m:
        avg = float(m.group(2))
        jitter = float(m.group(4))

    return {
        'ping_ms': avg,
        'jitter_ms': jitter,
        'loss_pct': loss,
    }


def _iperf(base, iperf, target, source, port, duration, parallel, reverse=False):
    cmd = [
        iperf,
        '-c', target,
        '-B', source,
        '-p', str(port),
        '-t', str(duration),
        '-O', '1',
        '-P', str(parallel),
        '-J',
        '--connect-timeout', '5000',
    ]
    if reverse:
        cmd.append('-R')

    rc, out, err = base.run(cmd, timeout=duration + 25, check=False)
    if rc != 0:
        raise RuntimeError((err or out or 'iperf3 failed')[-1800:])

    try:
        data = json.loads(out)
    except Exception:
        raise RuntimeError('خروجی JSON از iperf3 دریافت نشد')

    if data.get('error'):
        raise RuntimeError(str(data['error'])[:1600])

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
        'seconds': round(float(receiver.get('seconds') or sender.get('seconds') or duration), 2),
        'retransmits': int(sender.get('retransmits') or 0),
    }


def _quality(ping_ms, loss_pct, download_mbps, upload_mbps):
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


def benchmark(base, args):
    eid = base.safe_id(str(args.get('id', '')))
    target = _ipv4_in_pool(args.get('target_ip'), 'target_ip')
    source = _ipv4_in_pool(args.get('source_ip'), 'source_ip')
    duration = max(2, min(int(args.get('duration', 4) or 4), 10))
    parallel = max(1, min(int(args.get('parallel', 1) or 1), 4))
    port = int(args.get('port', DEFAULT_PORT) or DEFAULT_PORT)
    if not 20000 <= port <= 60999:
        raise ValueError('benchmark port نامعتبر است')

    iperf = _ensure_iperf3(base)

    rc, ping_out, ping_err = base.run([
        'ping', '-I', source,
        '-c', '6', '-i', '0.2', '-W', '1',
        target,
    ], timeout=10, check=False)
    ping_text = ping_out or ping_err or ''
    ping = _parse_ping(ping_text)
    if rc != 0 and ping.get('loss_pct') in (None, 100.0):
        raise RuntimeError('Peer Ping قبل از Benchmark شکست خورد')

    # Client روی Exit اجرا می‌شود و Server روی Iran است.
    # Normal: Exit -> Iran = دانلود از دید کاربران ایران.
    download = _iperf(
        base, iperf, target, source, port,
        duration, parallel, reverse=False,
    )
    # Reverse: Iran -> Exit = آپلود از دید کاربران ایران.
    upload = _iperf(
        base, iperf, target, source, port,
        duration, parallel, reverse=True,
    )

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
    result['quality'] = _quality(
        result.get('ping_ms'),
        result.get('loss_pct'),
        result.get('download_mbps'),
        result.get('upload_mbps'),
    )
    return result


def install(base):
    previous_status = base.ACTIONS.get('status')
    if previous_status is None:
        raise RuntimeError('base status action is missing')

    def status_or_benchmark(args):
        args = args or {}
        if args.get('benchmark_v1'):
            return benchmark(base, args)
        return previous_status(args)

    base.ACTIONS['status'] = status_or_benchmark
