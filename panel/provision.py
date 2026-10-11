import asyncio
import ipaddress
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

import asyncssh

from portplan import apply_plan, validate_plan

ROOT = Path('/etc/tp27')
CORE_CONFIG = ROOT / 'config.json'
EXITS = ROOT / 'exits'
CERTS = ROOT / 'certs'
JOB_DIR = Path('/var/log/tp27/panel-jobs')
RAW = os.environ.get('TP27_REPO_RAW', 'https://raw.githubusercontent.com/matin111/tp27-core/main')
JOBS = {}


def run(cmd, check=True):
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or 'command failed').strip())
    return p.returncode, (p.stdout or '').strip(), (p.stderr or '').strip()


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {} if default is None else default


def atomic_json(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def validate_id(value):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,24}', value):
        raise ValueError('شناسه نامعتبر است')
    return value


def iface_for(eid):
    return ('t27' + re.sub(r'[^a-z0-9]', '', eid.lower()))[:15]


def next_values(cfg):
    used = {e.get('id') for e in cfg.get('exits', [])}
    n = 1
    while f'exit{n:02d}' in used:
        n += 1
    subnets = list(ipaddress.ip_network('10.254.250.0/24').subnets(new_prefix=30))
    if n > len(subnets):
        raise RuntimeError('فضای آدرس TP27 پر شده است')
    net = subnets[n - 1]
    hosts = list(net.hosts())
    return f'exit{n:02d}', f'{hosts[1]}/30', f'{hosts[0]}/30', 44329 + n


def ensure_client_cert(eid, iran_ip):
    cert = CERTS / f'{eid}-iran.crt'
    key = CERTS / f'{eid}-iran.key'
    CERTS.mkdir(parents=True, exist_ok=True)
    if not cert.exists() or not key.exists():
        run(['openssl','req','-x509','-newkey','rsa:2048','-sha256','-nodes','-days','3650',
             '-subj',f'/CN={iran_ip}','-addext',f'subjectAltName=IP:{iran_ip}',
             '-keyout',str(key),'-out',str(cert)])
        os.chmod(key, 0o600)
        os.chmod(cert, 0o644)
    return cert, key


def job_set(job_id, state=None, message=None, error=None, result=None):
    j = JOBS.setdefault(job_id, {'id': job_id, 'state': 'queued', 'messages': []})
    if state:
        j['state'] = state
    if message:
        j['messages'].append({'ts': int(time.time()), 'text': message})
        JOB_DIR.mkdir(parents=True, exist_ok=True)
        with (JOB_DIR / f'{job_id}.log').open('a') as f:
            f.write(message + '\n')
    if error:
        j['error'] = error
    if result:
        j['result'] = result
    return j


def ssh_error_text(exc, tcp_ok=False, banner_ok=False):
    name = exc.__class__.__name__
    raw = str(exc).strip()
    low = raw.lower()

    if name == 'PermissionDenied' or 'permission denied' in low:
        return 'احراز هویت SSH رد شد؛ نام کاربری/رمز یا Private Key را بررسی کنید.'
    if name in {'ConnectionLost', 'DisconnectError'} or 'connection closed' in low or 'connection lost' in low:
        return 'سرور خارج اتصال SSH را قبل از تکمیل ورود بست. sshd/MaxStartups/ACL و لاگ SSH سرور خارج را بررسی کنید.'
    if name == 'ConnectionRefusedError' or 'connection refused' in low:
        return 'پورت SSH روی سرور خارج اتصال را رد کرد.'
    if name == 'TimeoutError' or 'timed out' in low or 'timeout' in low:
        if tcp_ok and banner_ok:
            return 'TCP و SSH Banner سالم است اما مرحله احراز هویت Timeout شد؛ مشکل از Firewall مسیر نیست. sshd/PAM/MaxStartups یا روش Authentication سرور خارج را بررسی کنید.'
        if tcp_ok:
            return 'TCP وصل شد اما SSH Banner دریافت نشد؛ سرویس یا لایه ورودی SSH سرور خارج پاسخ کامل نمی‌دهد.'
        return 'اتصال TCP به پورت SSH Timeout شد؛ IP، پورت و Firewall مسیر را بررسی کنید.'
    if 'no matching' in low:
        return f'الگوریتم SSH مشترک پیدا نشد: {raw}'
    if isinstance(exc, OSError):
        detail = raw or getattr(exc, 'strerror', '') or name
        return f'خطای شبکه SSH: {detail}'
    return f'{name}: {raw}' if raw else f'{name}: اتصال SSH ناموفق بود و سرور جزئیات بیشتری برنگرداند.'


async def ssh_banner_probe(host, port):
    writer = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=6)
        line = await asyncio.wait_for(reader.readline(), timeout=6)
        banner = line.decode('utf-8', 'replace').strip()
        return {'tcp_ok': True, 'banner_ok': banner.startswith('SSH-'), 'banner': banner}
    except Exception as exc:
        return {'tcp_ok': False, 'banner_ok': False, 'banner': '', 'exception': exc}
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


def ssh_options(host, port, username, password=None, private_key=None):
    kwargs = dict(
        host=host,
        port=port,
        username=username,
        known_hosts=None,
        connect_timeout=10,
        login_timeout=35,
        keepalive_interval=10,
        keepalive_count_max=3,
        gss_auth=False,
        gss_kex=False,
        host_based_auth=False,
        agent_path=None,
    )

    if password and not private_key:
        kwargs.update(
            password=password,
            client_keys=[],
            public_key_auth=False,
            kbdint_auth=False,
            password_auth=True,
            preferred_auth=['password'],
        )
    elif private_key:
        kwargs.update(
            client_keys=[asyncssh.import_private_key(private_key)],
            public_key_auth=True,
            preferred_auth=['publickey', 'password'] if password else ['publickey'],
        )
        if password:
            kwargs['password'] = password
            kwargs['password_auth'] = True
            kwargs['kbdint_auth'] = False
    elif password:
        kwargs.update(password=password, preferred_auth=['password'])

    return kwargs


async def open_ssh(host, port, username, password=None, private_key=None):
    last = None
    for attempt in range(2):
        try:
            return await asyncssh.connect(**ssh_options(host, port, username, password, private_key))
        except asyncssh.PermissionDenied:
            raise
        except Exception as exc:
            last = exc
            if attempt == 0:
                await asyncio.sleep(2)
    raise last


async def preflight(host, port, username, password=None, private_key=None):
    if not password and not private_key:
        return {'ok': False, 'error': 'رمز SSH یا Private Key لازم است.', 'error_type': 'MissingCredential'}

    stage = await ssh_banner_probe(host, port)
    if not stage.get('tcp_ok'):
        exc = stage.get('exception') or TimeoutError('TCP probe failed')
        return {
            'ok': False,
            'stage': 'tcp',
            'error': ssh_error_text(exc, tcp_ok=False, banner_ok=False),
            'error_type': exc.__class__.__name__,
        }
    if not stage.get('banner_ok'):
        return {
            'ok': False,
            'stage': 'banner',
            'error': f"TCP وصل شد ولی پاسخ SSH معتبر نبود: {stage.get('banner') or 'بدون Banner'}",
            'banner': stage.get('banner', ''),
        }

    conn = None
    try:
        conn = await open_ssh(host, port, username, password, private_key)
        key = conn.get_server_host_key().get_fingerprint()
        r = await conn.run('printf "TP27_OK\\n"; id -u; hostname', check=False, timeout=15)
        lines = [x.strip() for x in r.stdout.splitlines() if x.strip()]
        is_root = len(lines) >= 2 and lines[1] == '0'
        if r.exit_status != 0:
            return {
                'ok': False,
                'stage': 'command',
                'error': (r.stderr or 'فرمان تست SSH اجرا نشد').strip(),
                'fingerprint': key,
                'stdout': r.stdout.strip(),
                'stderr': r.stderr.strip(),
                'banner': stage.get('banner', ''),
            }
        if not is_root:
            return {
                'ok': False,
                'stage': 'privilege',
                'error': 'اتصال SSH برقرار شد اما کاربر دسترسی root ندارد.',
                'fingerprint': key,
                'stdout': r.stdout.strip(),
                'stderr': r.stderr.strip(),
                'banner': stage.get('banner', ''),
            }
        return {
            'ok': True,
            'stage': 'ready',
            'fingerprint': key,
            'stdout': r.stdout.strip(),
            'stderr': r.stderr.strip(),
            'auth': 'private-key' if private_key else 'password',
            'banner': stage.get('banner', ''),
        }
    except Exception as exc:
        return {
            'ok': False,
            'stage': 'auth',
            'error': ssh_error_text(exc, tcp_ok=True, banner_ok=True),
            'error_type': exc.__class__.__name__,
            'banner': stage.get('banner', ''),
        }
    finally:
        if conn:
            conn.close()
            try:
                await conn.wait_closed()
            except Exception:
                pass


async def provision(job_id, host, ssh_port, username, password, private_key,
                    requested_id=None, tunnel_port=None, mtu=1280, mappings=None):
    conn = None
    try:
        job_set(job_id, state='running', message=f'اتصال SSH به {host}:{ssh_port}')
        conn = await open_ssh(host, ssh_port, username, password, private_key)
        key = conn.get_server_host_key().get_fingerprint()
        job_set(job_id, message=f'SSH برقرار شد؛ HostKey={key}')
        probe = await conn.run('id -u && hostname', check=False, timeout=15)
        lines = [x.strip() for x in probe.stdout.splitlines() if x.strip()]
        if probe.exit_status != 0 or not lines or lines[0] != '0':
            raise RuntimeError('Provisioning نیاز به دسترسی root دارد')

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
            job_set(job_id, message=f'{len(port_plan)} Port Mapping بررسی شد و تداخلی ندارد')

        job_set(job_id, message='نصب/به‌روزرسانی TP27 روی سرور خارج')
        cmd = f'curl -fsSL {shlex.quote(RAW + "/install.sh")} | bash -s -- exit'
        r = await conn.run(cmd, check=False, timeout=180)
        if r.exit_status != 0:
            raise RuntimeError((r.stderr or r.stdout or 'remote install failed').strip())

        job_set(job_id, message=f'ساخت هویت mTLS برای {eid}')
        client_cert, client_key = ensure_client_cert(eid, iran_ip)
        remote_ca = f'/etc/tp27/certs/{eid}-iran.crt'
        async with conn.start_sftp_client() as sftp:
            await sftp.put(str(client_cert), remote_ca)

        job_set(job_id, message='ساخت Endpoint خارجی')
        remote_cmd = (f'tp27 exit-provision --id {shlex.quote(eid)} --listen-port {int(port)} '
                      f'--tunnel-ip {shlex.quote(remote_ip)} --mtu {int(mtu)} '
                      f'--client-ca {shlex.quote(remote_ca)}')
        r = await conn.run(remote_cmd, check=False, timeout=60)
        if r.exit_status != 0:
            raise RuntimeError((r.stderr or r.stdout or 'exit provisioning failed').strip())

        local_cert = CERTS / f'{eid}.crt'
        async with conn.start_sftp_client() as sftp:
            await sftp.get(f'/etc/tp27/certs/{eid}.crt', str(local_cert))
        os.chmod(local_cert, 0o644)

        remote_addr = str(ipaddress.ip_interface(remote_ip).ip)
        exit_cfg = {
            'role':'iran','id':eid,'interface':iface,'tunnel_ip':local_ip,
            'remote_ip':remote_addr,'endpoint':host,'listen_port':port,'mtu':mtu,
            'ca_file':str(local_cert),'cert':str(client_cert),'key':str(client_key)
        }
        atomic_json(EXITS / f'{eid}.json', exit_cfg)
        rec = {
            'id':eid,'interface':iface,'endpoint':host,'port':port,
            'local_ip':local_ip,'remote_ip':remote_addr
        }
        core.setdefault('exits', [])
        core['exits'] = [x for x in core['exits'] if x.get('id') != eid] + [rec]
        atomic_json(CORE_CONFIG, core)

        job_set(job_id, message='راه‌اندازی سرویس محلی')
        run(['systemctl','daemon-reload'])
        rc, out, err = run(['systemctl','enable','--now',f'tp27-link@{eid}.service'], check=False)
        if rc != 0:
            raise RuntimeError(err or out or 'local service failed')

        job_set(job_id, message='Health Check مسیر L3')
        online = False
        for _ in range(20):
            await asyncio.sleep(1)
            rc, _, _ = run(['ping','-I',iface,'-c','1','-W','1',remote_addr], check=False)
            if rc == 0:
                online = True
                break
        if not online:
            raise RuntimeError('سرویس ساخته شد اما Ping تونل پاسخ نداد')

        applied = []
        if port_plan:
            job_set(job_id, message='اعمال Port Mapping های انتخاب‌شده روی IP ایران')
            applied = apply_plan(eid, port_plan)
            job_set(job_id, message=f'{len(applied)} Port Mapping فعال شد')

        result = {
            'id':eid,'interface':iface,'local_ip':local_ip,
            'remote_ip':remote_addr,'endpoint':host,'port':port,
            'mappings': applied
        }
        job_set(job_id, state='done', message=f'{eid} ONLINE شد', result=result)
    except Exception as exc:
        detail = ssh_error_text(exc) if exc.__class__.__name__ in {
            'PermissionDenied','ConnectionLost','DisconnectError','ConnectionRefusedError','TimeoutError'
        } else (str(exc).strip() or f'{exc.__class__.__name__}: عملیات ناموفق بود')
        job_set(job_id, state='failed', message=f'خطا: {detail}', error=detail)
    finally:
        if conn:
            conn.close()
            try:
                await conn.wait_closed()
            except Exception:
                pass
