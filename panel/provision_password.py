#!/usr/bin/env python3
import asyncio
import ipaddress
import os
import shlex
import subprocess
from pathlib import Path

from portplan import apply_plan, validate_plan
from provision import (
    CORE_CONFIG, CERTS, EXITS, JOBS, RAW,
    atomic_json, ensure_client_cert, iface_for, job_set,
    load_json, next_values, run, validate_id,
)

KNOWN_HOSTS = Path('/etc/tp27/known_hosts')


def _env(password):
    env = os.environ.copy()
    env['SSHPASS'] = password
    return env


def _ssh_opts(port):
    return [
        '-p', str(port),
        '-o', 'PreferredAuthentications=password',
        '-o', 'PubkeyAuthentication=no',
        '-o', 'KbdInteractiveAuthentication=no',
        '-o', 'PasswordAuthentication=yes',
        '-o', 'GSSAPIAuthentication=no',
        '-o', 'NumberOfPasswordPrompts=1',
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', f'UserKnownHostsFile={KNOWN_HOSTS}',
        '-o', 'ConnectTimeout=10',
        '-o', 'ServerAliveInterval=10',
        '-o', 'ServerAliveCountMax=3',
        '-o', 'LogLevel=ERROR',
    ]


def _scp_opts(port):
    return [
        '-P', str(port),
        '-o', 'PreferredAuthentications=password',
        '-o', 'PubkeyAuthentication=no',
        '-o', 'KbdInteractiveAuthentication=no',
        '-o', 'PasswordAuthentication=yes',
        '-o', 'GSSAPIAuthentication=no',
        '-o', 'NumberOfPasswordPrompts=1',
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', f'UserKnownHostsFile={KNOWN_HOSTS}',
        '-o', 'ConnectTimeout=10',
        '-o', 'LogLevel=ERROR',
    ]


def _run_sync(argv, password, timeout=60):
    try:
        p = subprocess.run(
            argv,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_env(password),
            timeout=timeout,
        )
        return p.returncode, (p.stdout or '').strip(), (p.stderr or '').strip()
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or '')
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or '')
        return 124, out.strip(), (err.strip() or 'timeout')


async def ssh_run(host, port, username, password, command, timeout=60):
    target = f'{username}@{host}'
    argv = ['sshpass', '-e', 'ssh', *_ssh_opts(port), target, command]
    return await asyncio.to_thread(_run_sync, argv, password, timeout)


async def scp_put(host, port, username, password, local_path, remote_path, timeout=60):
    target = f'{username}@{host}:{remote_path}'
    argv = ['sshpass', '-e', 'scp', *_scp_opts(port), str(local_path), target]
    return await asyncio.to_thread(_run_sync, argv, password, timeout)


async def scp_get(host, port, username, password, remote_path, local_path, timeout=60):
    source = f'{username}@{host}:{remote_path}'
    argv = ['sshpass', '-e', 'scp', *_scp_opts(port), source, str(local_path)]
    return await asyncio.to_thread(_run_sync, argv, password, timeout)


def _friendly(rc, out, err):
    text = (err or out or '').strip()
    low = text.lower()
    if rc == 5 or 'permission denied' in low:
        return 'احراز هویت SSH رد شد؛ نام کاربری یا رمز را بررسی کنید.'
    if rc == 124 or 'timed out' in low or 'timeout' in low:
        return 'احراز هویت SSH با OpenSSH هم Timeout شد.'
    if 'connection refused' in low:
        return 'پورت SSH اتصال را رد کرد.'
    if 'connection closed' in low or 'connection reset' in low:
        return 'سرور خارج اتصال SSH را بست.'
    return text or f'OpenSSH exit={rc}'


async def preflight_password(host, port, username, password):
    if not password:
        return {'ok': False, 'error': 'رمز SSH لازم است.', 'auth': 'openssh-password'}
    KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
    rc, out, err = await ssh_run(
        host, port, username, password,
        'printf "TP27_OK\\n"; id -u; hostname',
        timeout=30,
    )
    lines = [x.strip() for x in out.splitlines() if x.strip()]
    ok = rc == 0 and len(lines) >= 3 and lines[0] == 'TP27_OK' and lines[1] == '0'
    if not ok:
        return {
            'ok': False,
            'auth': 'openssh-password',
            'error': _friendly(rc, out, err),
            'stdout': out,
            'stderr': err,
            'exit_code': rc,
        }
    return {
        'ok': True,
        'auth': 'openssh-password',
        'stdout': out,
        'stderr': err,
        'hostname': lines[2],
    }


async def provision_password(job_id, host, ssh_port, username, password,
                             requested_id=None, tunnel_port=None, mtu=1280,
                             mappings=None):
    try:
        job_set(job_id, state='running', message=f'اتصال SSH به {host}:{ssh_port}')
        probe = await preflight_password(host, ssh_port, username, password)
        if not probe.get('ok'):
            raise RuntimeError(probe.get('error') or 'SSH ناموفق')
        job_set(job_id, message=f"SSH برقرار شد؛ {probe.get('hostname', host)}")

        core = load_json(CORE_CONFIG)
        eid, local_ip, remote_ip, auto_port = next_values(core)
        if requested_id:
            eid = validate_id(requested_id)
            if any(x.get('id') == eid for x in core.get('exits', [])):
                raise RuntimeError(f'{eid} قبلاً وجود دارد')
        port = tunnel_port or auto_port
        iface = iface_for(eid)
        iran_ip = core.get('public_ip')
        if not iran_ip:
            raise RuntimeError('public_ip ایران در config نیست')

        port_plan = validate_plan(mappings or [])
        if port_plan:
            job_set(job_id, message=f'{len(port_plan)} Port Mapping بررسی شد')

        job_set(job_id, message='نصب/به‌روزرسانی TP27 روی سرور خارج')
        install_cmd = f'curl -fsSL {shlex.quote(RAW + "/install.sh")} | bash -s -- exit'
        rc, out, err = await ssh_run(host, ssh_port, username, password, install_cmd, timeout=240)
        if rc != 0:
            raise RuntimeError(_friendly(rc, out, err))

        job_set(job_id, message=f'ساخت هویت mTLS برای {eid}')
        client_cert, client_key = ensure_client_cert(eid, iran_ip)
        remote_ca = f'/etc/tp27/certs/{eid}-iran.crt'

        rc, out, err = await scp_put(
            host, ssh_port, username, password,
            client_cert, remote_ca, timeout=60,
        )
        if rc != 0:
            raise RuntimeError(f'SCP certificate: {_friendly(rc, out, err)}')

        job_set(job_id, message='ساخت Endpoint خارجی')
        remote_cmd = (
            f'tp27 exit-provision --id {shlex.quote(eid)} '
            f'--listen-port {int(port)} '
            f'--tunnel-ip {shlex.quote(remote_ip)} '
            f'--mtu {int(mtu)} '
            f'--client-ca {shlex.quote(remote_ca)}'
        )
        rc, out, err = await ssh_run(host, ssh_port, username, password, remote_cmd, timeout=90)
        if rc != 0:
            raise RuntimeError(_friendly(rc, out, err))

        local_cert = CERTS / f'{eid}.crt'
        rc, out, err = await scp_get(
            host, ssh_port, username, password,
            f'/etc/tp27/certs/{eid}.crt', local_cert, timeout=60,
        )
        if rc != 0:
            raise RuntimeError(f'SCP exit certificate: {_friendly(rc, out, err)}')
        os.chmod(local_cert, 0o644)

        remote_addr = str(ipaddress.ip_interface(remote_ip).ip)
        exit_cfg = {
            'role': 'iran', 'id': eid, 'interface': iface,
            'tunnel_ip': local_ip, 'remote_ip': remote_addr,
            'endpoint': host, 'listen_port': port, 'mtu': mtu,
            'ca_file': str(local_cert), 'cert': str(client_cert),
            'key': str(client_key),
        }
        atomic_json(EXITS / f'{eid}.json', exit_cfg)

        rec = {
            'id': eid, 'interface': iface, 'endpoint': host,
            'port': port, 'local_ip': local_ip, 'remote_ip': remote_addr,
        }
        core.setdefault('exits', [])
        core['exits'] = [x for x in core['exits'] if x.get('id') != eid] + [rec]
        atomic_json(CORE_CONFIG, core)

        job_set(job_id, message='راه‌اندازی سرویس محلی')
        run(['systemctl', 'daemon-reload'])
        rc, out, err = run(['systemctl', 'enable', '--now', f'tp27-link@{eid}.service'], check=False)
        if rc != 0:
            raise RuntimeError(err or out or 'local service failed')

        job_set(job_id, message='Health Check')
        online = False
        for _ in range(20):
            await asyncio.sleep(1)
            rc, _, _ = run(['ping', '-I', iface, '-c', '1', '-W', '1', remote_addr], check=False)
            if rc == 0:
                online = True
                break
        if not online:
            raise RuntimeError('تونل ساخته شد اما Health Check پاسخ نداد')

        applied = []
        if port_plan:
            job_set(job_id, message='اعمال Port Mapping')
            applied = apply_plan(eid, port_plan)

        result = {
            'id': eid, 'interface': iface, 'local_ip': local_ip,
            'remote_ip': remote_addr, 'endpoint': host, 'port': port,
            'mappings': applied, 'ssh_backend': 'openssh-password',
        }
        job_set(job_id, state='done', message=f'{eid} ONLINE شد', result=result)
    except Exception as exc:
        detail = str(exc).strip() or f'{exc.__class__.__name__}: عملیات ناموفق بود'
        job_set(job_id, state='failed', message=f'خطا: {detail}', error=detail)
