#!/usr/bin/env python3
import asyncio
import hashlib
import ipaddress
import re
import time

from agent_control import get_agent, queue_command, wait_command
from portplan import apply_plan, validate_plan
from provision import CORE_CONFIG, EXITS, atomic_json, iface_for, job_set, load_json, run, validate_id

MIN_AGENT_VERSION = (1, 5, 0)
GRE_POOL = ipaddress.ip_network('10.253.0.0/16')
DEFAULT_MTU = 1400
TLS_MTU = 1300
FOU_PORT_CANDIDATES = (443, 8443, 2053, 2083, 10443)
TLS_PORT_CANDIDATES = (443, 80, 8443, 2053, 2083, 10443, 8080)
HEALTH_PING_COUNT = 5
HEALTH_MAX_LOSS_PCT = 20.0
HEALTH_REQUIRED_STREAK = 2


def _version_tuple(value):
    nums = [int(x) for x in re.findall(r'\d+', str(value or '0'))[:3]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _used_networks(core):
    used = set()
    for item in core.get('exits', []):
        rows = item.get('channels') or [item]
        for row in rows:
            for key in ('local_ip', 'remote_ip'):
                value = row.get(key)
                if not value:
                    continue
                try:
                    if '/' in str(value):
                        used.add(str(ipaddress.ip_interface(str(value)).network))
                    else:
                        used.add(str(ipaddress.ip_network(str(value) + '/30', strict=False)))
                except Exception:
                    pass
    return used


def _allocate_pair(core):
    used = _used_networks(core)
    for net in GRE_POOL.subnets(new_prefix=30):
        if str(net) in used:
            continue
        hosts = list(net.hosts())
        return f'{hosts[1]}/30', f'{hosts[0]}/30'
    raise RuntimeError('فضای آدرس Tunnel در TP27 پر شده است')


def _gre_key(eid, iran_ip, exit_ip):
    raw = hashlib.sha256(f'{eid}|{iran_ip}|{exit_ip}'.encode()).digest()
    key = int.from_bytes(raw[:4], 'big')
    return key or 1


def _service_name(eid, transport):
    return f'tp27-framed@{eid}.service' if transport == 'framed-tls' else f'tp27-link@{eid}.service'


def _local_log(eid):
    chunks = []
    for svc in (f'tp27-link@{eid}.service', f'tp27-framed@{eid}.service'):
        _, out, err = run(['journalctl', '-u', svc, '-n', '35', '--no-pager', '-o', 'cat'], check=False)
        text = (out or err or '').strip()
        if text:
            chunks.append(f'[{svc}]\n{text}')
    return '\n'.join(chunks)[-7000:]


def _cleanup_local(eid):
    for svc in (f'tp27-link@{eid}.service', f'tp27-framed@{eid}.service'):
        run(['systemctl', 'disable', '--now', svc], check=False)
        run(['systemctl', 'reset-failed', svc], check=False)
    iface = iface_for(eid)
    run(['ip', 'link', 'del', iface], check=False)
    try:
        (EXITS / f'{eid}.json').unlink(missing_ok=True)
    except Exception:
        pass


async def _cleanup_remote(agent_id, eid):
    try:
        cmd = queue_command(agent_id, 'delete_exit', {'id': eid})
        await wait_command(cmd['id'], timeout=45)
    except Exception:
        pass


async def _ensure_agent(agent_id, job_id):
    agent = get_agent(agent_id)
    if not agent:
        raise RuntimeError('Agent پیدا نشد')
    if not agent.get('online'):
        raise RuntimeError('Agent آفلاین است')
    if _version_tuple(agent.get('version')) >= MIN_AGENT_VERSION:
        return agent

    job_set(job_id, message=f"ارتقای خودکار Agent از v{agent.get('version','?')} به v1.5.0")
    try:
        command = queue_command(agent_id, 'upgrade_agent', {})
        result = await wait_command(command['id'], timeout=90)
    except Exception as exc:
        raise RuntimeError(
            'Agent قدیمی است و ارتقای خودکار انجام نشد. یک بار Installer جدید Agent را روی سرور خارج اجرا کنید. '
            + str(exc)
        )
    if result.get('state') != 'done' or not result.get('ok'):
        raise RuntimeError(result.get('error') or 'ارتقای Agent ناموفق بود')

    deadline = time.monotonic() + 70
    while time.monotonic() < deadline:
        await asyncio.sleep(3)
        agent = get_agent(agent_id)
        if agent and agent.get('online') and _version_tuple(agent.get('version')) >= MIN_AGENT_VERSION:
            job_set(job_id, message=f"Agent v{agent.get('version')} آنلاین شد")
            return agent
    raise RuntimeError('Agent بعد از ارتقا به v1.5.0 برنگشت')


def _write_local_gre(eid, iface, local_ip, remote_addr, iran_ip, endpoint,
                     gre_key, mtu, agent_id, transport='gre', fou_port=0):
    cfg = {
        'role': 'gre-iran', 'transport': transport,
        'mode': 'gre-fou' if transport == 'gre-fou' else 'gre-native',
        'id': eid, 'interface': iface, 'tunnel_ip': local_ip, 'peer_ip': remote_addr,
        'local_public': iran_ip, 'remote_public': endpoint, 'gre_key': gre_key,
        'mtu': mtu, 'txqueuelen': 1000, 'agent_id': agent_id,
    }
    if transport == 'gre-fou':
        cfg['fou_port'] = int(fou_port)
    atomic_json(EXITS / f'{eid}.json', cfg)
    return cfg


def _write_local_tls(eid, iface, local_ip, remote_addr, iran_ip, endpoint,
                     listen_port, cert_pin, agent_id):
    cfg = {
        'role': 'framed-client', 'transport': 'framed-tls', 'mode': 'framed-tls',
        'id': eid, 'interface': iface, 'tunnel_ip': local_ip, 'peer_ip': remote_addr,
        'local_public': iran_ip, 'remote_public': endpoint, 'listen_port': int(listen_port),
        'cert_pin': cert_pin, 'mtu': TLS_MTU, 'agent_id': agent_id,
    }
    atomic_json(EXITS / f'{eid}.json', cfg)
    return cfg


def _start_local(eid, transport):
    svc = _service_name(eid, transport)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', svc], check=False)
    rc, out, err = run(['systemctl', 'restart', svc], check=False)
    if rc != 0:
        raise RuntimeError(err or out or f'{svc} Start نشد')


async def _provision_remote_gre(agent_id, eid, iran_ip, endpoint, remote_ip,
                                local_addr, gre_key, mtu, transport='gre', fou_port=0):
    payload = {
        'id': eid, 'iran_public': iran_ip, 'exit_public': endpoint,
        'tunnel_ip': remote_ip, 'peer_ip': local_addr, 'gre_key': gre_key,
        'mtu': mtu, 'txqueuelen': 1000, 'transport': transport,
    }
    if transport == 'gre-fou':
        payload['fou_port'] = int(fou_port)
    command = queue_command(agent_id, 'provision_gre_exit', payload)
    result_cmd = await wait_command(command['id'], timeout=80)
    if result_cmd.get('state') != 'done' or not result_cmd.get('ok'):
        raise RuntimeError(result_cmd.get('error') or 'ساخت GRE روی سرور خارج ناموفق بود')
    result = result_cmd.get('result') or {}
    if not result.get('service_active'):
        raise RuntimeError('سرویس GRE روی سرور خارج Active نشد')
    return result


async def _provision_remote_tls(agent_id, eid, iran_ip, endpoint, remote_ip,
                                local_addr, listen_port):
    payload = {
        'id': eid, 'iran_public': iran_ip, 'exit_public': endpoint,
        'tunnel_ip': remote_ip, 'peer_ip': local_addr,
        'listen_port': int(listen_port), 'mtu': TLS_MTU,
    }
    command = queue_command(agent_id, 'provision_tls_exit', payload)
    result_cmd = await wait_command(command['id'], timeout=80)
    if result_cmd.get('state') != 'done' or not result_cmd.get('ok'):
        raise RuntimeError(result_cmd.get('error') or f'TCP/{listen_port} fallback روی خارج ساخته نشد')
    result = result_cmd.get('result') or {}
    if not result.get('service_active'):
        raise RuntimeError('Framed TLS روی سرور خارج Active نشد')
    pin = str(result.get('cert_pin') or '').strip().lower()
    if not re.fullmatch(r'[0-9a-f]{64}', pin):
        raise RuntimeError('Certificate pin خارج نامعتبر است')
    return result


async def _health_peer(iface, remote_addr, tries=6):
    attempts = max(int(tries or 0), HEALTH_REQUIRED_STREAK)
    streak = 0
    samples = []

    for attempt in range(1, attempts + 1):
        rc, out, err = run([
            'ping', '-I', iface,
            '-c', str(HEALTH_PING_COUNT),
            '-W', '1', '-w', '6',
            remote_addr,
        ], check=False)
        text = out or err or ''
        match = re.search(r'(\d+(?:\.\d+)?)%\s+packet loss', text)
        loss = float(match.group(1)) if match else None
        loss_text = f'{loss:.1f}' if loss is not None else 'unknown'
        samples.append(
            f'HEALTH_WINDOW={attempt} rc={rc} loss={loss_text}%\n{text}'.strip()
        )

        if rc == 0 and loss is not None and loss <= HEALTH_MAX_LOSS_PCT:
            streak += 1
            if streak >= HEALTH_REQUIRED_STREAK:
                return True, '\n\n'.join(samples[-HEALTH_REQUIRED_STREAK:])
        else:
            streak = 0

        if attempt < attempts:
            await asyncio.sleep(1)

    return False, '\n\n'.join(samples[-3:])


def _diagnostics(eid, iface, remote_result, ping_text):
    _, addr_out, _ = run(['ip', '-br', '-4', 'addr', 'show', 'dev', iface], check=False)
    _, route_out, _ = run(['ip', '-4', 'route', 'show', 'dev', iface], check=False)
    _, link_out, _ = run(['ip', '-s', 'link', 'show', 'dev', iface], check=False)
    remote_diag = '\n'.join(filter(None, [
        str(remote_result.get('interface_state') or ''),
        str(remote_result.get('route_state') or ''),
        str(remote_result.get('link_state') or ''),
        str(remote_result.get('ping_output') or ''),
    ]))
    return (
        f'IRAN_IFACE: {addr_out}\nIRAN_ROUTE: {route_out}\nIRAN_LINK: {link_out[-1200:]}\n'
        f'IRAN_LOG: {_local_log(eid)[-1700:]}\nEXIT: {remote_diag[-1600:]}\nPING: {ping_text[-700:]}'
    )


async def _build_gre(agent_id, eid, iface, iran_ip, endpoint,
                     local_ip, remote_ip, local_addr, remote_addr,
                     gre_key, mtu, transport='gre', fou_port=0):
    _write_local_gre(eid, iface, local_ip, remote_addr, iran_ip, endpoint,
                     gre_key, mtu, agent_id, transport, fou_port)
    _start_local(eid, transport)
    await asyncio.sleep(2)
    remote_result = await _provision_remote_gre(
        agent_id, eid, iran_ip, endpoint, remote_ip, local_addr,
        gre_key, mtu, transport, fou_port,
    )
    healthy, ping_text = await _health_peer(iface, remote_addr, tries=5)
    return healthy, ping_text, remote_result


async def _build_tls(agent_id, eid, iface, iran_ip, endpoint,
                     local_ip, remote_ip, local_addr, remote_addr, listen_port):
    remote_result = await _provision_remote_tls(
        agent_id, eid, iran_ip, endpoint, remote_ip, local_addr, listen_port,
    )
    _write_local_tls(
        eid, iface, local_ip, remote_addr, iran_ip, endpoint,
        listen_port, remote_result['cert_pin'], agent_id,
    )
    _start_local(eid, 'framed-tls')
    await asyncio.sleep(4)
    healthy, ping_text = await _health_peer(iface, remote_addr, tries=6)
    return healthy, ping_text, remote_result


async def provision_gre_via_agent(job_id, agent_id, requested_id=None,
                                  mtu=DEFAULT_MTU, mappings=None):
    eid = None
    final_transport = None
    final_port = None
    final_remote_result = {}
    raw_diag = ''
    try:
        agent = await _ensure_agent(agent_id, job_id)
        endpoint = str(agent.get('public_ip') or '').strip()
        if ipaddress.ip_address(endpoint).version != 4:
            raise RuntimeError('TP27 به IPv4 عمومی سرور خارج نیاز دارد')

        core = load_json(CORE_CONFIG)
        iran_ip = str(core.get('public_ip') or '').strip()
        if ipaddress.ip_address(iran_ip).version != 4:
            raise RuntimeError('TP27 به IPv4 عمومی سرور ایران نیاز دارد')

        if requested_id:
            eid = validate_id(requested_id)
        else:
            used_ids = {x.get('id') for x in core.get('exits', [])}
            n = 1
            while f'exit{n:02d}' in used_ids:
                n += 1
            eid = f'exit{n:02d}'
        if any(x.get('id') == eid for x in core.get('exits', [])):
            raise RuntimeError(f'{eid} قبلاً وجود دارد')

        mtu = int(mtu or DEFAULT_MTU)
        if not 1000 <= mtu <= 1476:
            raise RuntimeError('MTU باید بین 1000 و 1476 باشد')
        if mtu == 1280:
            mtu = DEFAULT_MTU

        port_plan = validate_plan(mappings or [])
        local_ip, remote_ip = _allocate_pair(core)
        remote_addr = str(ipaddress.ip_interface(remote_ip).ip)
        local_addr = str(ipaddress.ip_interface(local_ip).ip)
        gre_key = _gre_key(eid, iran_ip, endpoint)
        iface = iface_for(eid)

        job_set(job_id, state='running', message='AUTO TRANSPORT: Raw GRE → GRE/UDP → Framed TLS/TCP')
        _cleanup_local(eid)
        await _cleanup_remote(agent_id, eid)

        job_set(job_id, message=f'مرحله 1: Raw GRE {iran_ip} ↔ {endpoint}')
        raw_remote = {}
        raw_ping = ''
        try:
            healthy, raw_ping, raw_remote = await _build_gre(
                agent_id, eid, iface, iran_ip, endpoint,
                local_ip, remote_ip, local_addr, remote_addr,
                gre_key, mtu, 'gre', 0,
            )
        except Exception as exc:
            healthy = False
            raw_ping = str(exc)

        if healthy:
            final_transport = 'gre'
            final_remote_result = raw_remote
            job_set(job_id, message='Raw GRE دوطرفه سالم است؛ سریع‌ترین مسیر انتخاب شد')
        else:
            raw_diag = _diagnostics(eid, iface, raw_remote, raw_ping)
            _cleanup_local(eid)
            await _cleanup_remote(agent_id, eid)
            await asyncio.sleep(1)

            fou_errors = []
            job_set(job_id, message='مرحله 2: Raw GRE سالم نیست؛ تست GRE/UDP')
            for fou_port in FOU_PORT_CANDIDATES:
                job_set(job_id, message=f'GRE/UDP: تست UDP {fou_port}')
                remote = {}
                ping_text = ''
                try:
                    healthy, ping_text, remote = await _build_gre(
                        agent_id, eid, iface, iran_ip, endpoint,
                        local_ip, remote_ip, local_addr, remote_addr,
                        gre_key, mtu, 'gre-fou', fou_port,
                    )
                    if healthy:
                        final_transport = 'gre-fou'
                        final_port = fou_port
                        final_remote_result = remote
                        job_set(job_id, message=f'GRE/UDP روی UDP {fou_port} ONLINE شد')
                        break
                    fou_errors.append(f'UDP {fou_port}: Peer Ping نشد')
                except Exception as exc:
                    fou_errors.append(f'UDP {fou_port}: {exc}')
                _cleanup_local(eid)
                await _cleanup_remote(agent_id, eid)
                await asyncio.sleep(1)

            if not final_transport:
                job_set(job_id, message='مرحله 3: GRE/UDP فیلتر است؛ سوییچ خودکار به Framed TLS/TCP خروجی ایران')
                tls_errors = []
                for tcp_port in TLS_PORT_CANDIDATES:
                    job_set(job_id, message=f'Framed TLS: تست TCP {tcp_port} روی سرور خارج')
                    remote = {}
                    ping_text = ''
                    try:
                        healthy, ping_text, remote = await _build_tls(
                            agent_id, eid, iface, iran_ip, endpoint,
                            local_ip, remote_ip, local_addr, remote_addr, tcp_port,
                        )
                        if healthy:
                            final_transport = 'framed-tls'
                            final_port = tcp_port
                            final_remote_result = remote
                            job_set(job_id, message=f'Framed TLS روی TCP {tcp_port} دوطرفه ONLINE شد')
                            break
                        tls_errors.append(f'TCP {tcp_port}: Peer Ping نشد')
                    except Exception as exc:
                        tls_errors.append(f'TCP {tcp_port}: {exc}')
                    _cleanup_local(eid)
                    await _cleanup_remote(agent_id, eid)
                    await asyncio.sleep(1)

                if not final_transport:
                    raise RuntimeError(
                        'هیچ Transport دوطرفه ONLINE نشد. احتمال فیلتر/مشکل Route یا Provider وجود دارد.\n'
                        f'RAW: {raw_diag[-1400:]}\nFOU: ' + ' | '.join(fou_errors)[-1400:] +
                        '\nTLS: ' + ' | '.join(tls_errors)[-1800:]
                    )

        mode = {
            'gre': 'gre-native',
            'gre-fou': 'gre-fou',
            'framed-tls': 'framed-tls',
        }[final_transport]
        rec = {
            'id': eid, 'mode': mode, 'transport': final_transport,
            'interface': iface, 'endpoint': endpoint, 'port': final_port,
            'fou_port': final_port if final_transport == 'gre-fou' else None,
            'local_ip': local_ip, 'remote_ip': remote_addr,
            'agent_id': agent_id, 'gre_key': gre_key if final_transport != 'framed-tls' else None,
            'mtu': TLS_MTU if final_transport == 'framed-tls' else mtu,
            'public_local': iran_ip, 'public_remote': endpoint,
            'service': _service_name(eid, final_transport),
        }
        core.setdefault('exits', [])
        core['exits'] = [x for x in core['exits'] if x.get('id') != eid] + [rec]
        atomic_json(CORE_CONFIG, core)

        applied = []
        if port_plan:
            label = {'gre': 'GRE FAST', 'gre-fou': 'GRE/UDP FAST', 'framed-tls': 'TLS/TCP FALLBACK'}[final_transport]
            job_set(job_id, message=f'اعمال Port Mapping روی {label}')
            applied = apply_plan(eid, port_plan)

        if final_transport == 'framed-tls':
            done_message = f'{eid} TLS/TCP ONLINE · TCP {final_port} · {local_addr} ↔ {remote_addr}'
        elif final_transport == 'gre-fou':
            done_message = f'{eid} GRE/UDP FAST ONLINE · UDP {final_port} · {local_addr} ↔ {remote_addr}'
        else:
            done_message = f'{eid} GRE FAST ONLINE · {local_addr} ↔ {remote_addr}'
        job_set(job_id, state='done', message=done_message,
                result={**rec, 'mappings': applied, 'remote': final_remote_result})
    except Exception as exc:
        if eid:
            _cleanup_local(eid)
            try:
                await _cleanup_remote(agent_id, eid)
            except Exception:
                pass
        job_set(job_id, state='failed', message='خطا: ' + str(exc))