#!/usr/bin/env python3
import asyncio
import hashlib
import ipaddress
import re
import time

from agent_control import get_agent, queue_command, wait_command
from portplan import apply_plan, validate_plan
from provision import CORE_CONFIG, EXITS, atomic_json, iface_for, job_set, load_json, run, validate_id

MIN_AGENT_VERSION = (1, 3, 0)
GRE_POOL = ipaddress.ip_network('10.253.0.0/16')
DEFAULT_MTU = 1400


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
        # Iran receives .2, Exit receives .1 to match TP27's existing convention.
        return f'{hosts[1]}/30', f'{hosts[0]}/30'
    raise RuntimeError('فضای آدرس GRE در TP27 پر شده است')


def _gre_key(eid, iran_ip, exit_ip):
    raw = hashlib.sha256(f'{eid}|{iran_ip}|{exit_ip}'.encode()).digest()
    key = int.from_bytes(raw[:4], 'big')
    return key or 1


def _local_log(eid):
    _, out, err = run([
        'journalctl', '-u', f'tp27-link@{eid}.service',
        '-n', '40', '--no-pager', '-o', 'cat'
    ], check=False)
    return (out or err or '').strip()[-5000:]


def _cleanup_local(eid):
    run(['systemctl', 'disable', '--now', f'tp27-link@{eid}.service'], check=False)
    run(['systemctl', 'reset-failed', f'tp27-link@{eid}.service'], check=False)
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

    job_set(job_id, message=f"ارتقای خودکار Agent از v{agent.get('version','?')} به هسته GRE جدید")
    command = queue_command(agent_id, 'upgrade_agent', {})
    result = await wait_command(command['id'], timeout=60)
    if result.get('state') != 'done' or not result.get('ok'):
        raise RuntimeError(result.get('error') or 'ارتقای Agent ناموفق بود')

    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        await asyncio.sleep(3)
        agent = get_agent(agent_id)
        if agent and agent.get('online') and _version_tuple(agent.get('version')) >= MIN_AGENT_VERSION:
            job_set(job_id, message=f"Agent v{agent.get('version')} آنلاین شد")
            return agent
    raise RuntimeError('Agent بعد از ارتقا به نسخه GRE جدید برنگشت')


async def provision_gre_via_agent(job_id, agent_id, requested_id=None,
                                  mtu=DEFAULT_MTU, mappings=None):
    eid = None
    try:
        agent = await _ensure_agent(agent_id, job_id)
        endpoint = str(agent.get('public_ip') or '').strip()
        exit_public = ipaddress.ip_address(endpoint)
        if exit_public.version != 4:
            raise RuntimeError('GRE فعلی TP27 به IPv4 عمومی سرور خارج نیاز دارد')

        core = load_json(CORE_CONFIG)
        iran_ip = str(core.get('public_ip') or '').strip()
        iran_public = ipaddress.ip_address(iran_ip)
        if iran_public.version != 4:
            raise RuntimeError('GRE فعلی TP27 به IPv4 عمومی سرور ایران نیاز دارد')

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
            raise RuntimeError('MTU برای GRE باید بین 1000 و 1476 باشد')
        # Older UI builds submit 1280. Use 1400 for the GRE fast path unless the
        # operator explicitly supplied something else in the future.
        if mtu == 1280:
            mtu = DEFAULT_MTU

        port_plan = validate_plan(mappings or [])
        local_ip, remote_ip = _allocate_pair(core)
        remote_addr = str(ipaddress.ip_interface(remote_ip).ip)
        local_addr = str(ipaddress.ip_interface(local_ip).ip)
        gre_key = _gre_key(eid, iran_ip, endpoint)
        iface = iface_for(eid)

        job_set(job_id, state='running', message='GRE FAST: راه‌اندازی Data Plane مستقیم در Kernel Linux')
        job_set(job_id, message=f'GRE {iran_ip} ↔ {endpoint} · MTU {mtu}')

        _cleanup_local(eid)
        await _cleanup_remote(agent_id, eid)

        local_cfg = {
            'role': 'gre-iran',
            'transport': 'gre',
            'mode': 'gre-native',
            'id': eid,
            'interface': iface,
            'tunnel_ip': local_ip,
            'peer_ip': remote_addr,
            'local_public': iran_ip,
            'remote_public': endpoint,
            'gre_key': gre_key,
            'mtu': mtu,
            'txqueuelen': 1000,
            'agent_id': agent_id,
        }
        atomic_json(EXITS / f'{eid}.json', local_cfg)
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'enable', f'tp27-link@{eid}.service'], check=False)
        rc, out, err = run(['systemctl', 'restart', f'tp27-link@{eid}.service'], check=False)
        if rc != 0:
            raise RuntimeError(err or out or 'GRE ایران Start نشد')
        await asyncio.sleep(2)

        command = queue_command(agent_id, 'provision_gre_exit', {
            'id': eid,
            'iran_public': iran_ip,
            'exit_public': endpoint,
            'tunnel_ip': remote_ip,
            'peer_ip': local_addr,
            'gre_key': gre_key,
            'mtu': mtu,
            'txqueuelen': 1000,
        })
        result_cmd = await wait_command(command['id'], timeout=75)
        if result_cmd.get('state') != 'done' or not result_cmd.get('ok'):
            raise RuntimeError(result_cmd.get('error') or 'ساخت GRE روی سرور خارج ناموفق بود')
        remote_result = result_cmd.get('result') or {}
        if not remote_result.get('service_active'):
            raise RuntimeError('سرویس GRE روی سرور خارج Active نشد')

        job_set(job_id, message='GRE دو طرف ساخته شد؛ تست مستقیم Peer')
        healthy = False
        ping_text = ''
        for _ in range(8):
            rc, out, err = run([
                'ping', '-I', iface, '-c', '2', '-W', '2', remote_addr
            ], check=False)
            ping_text = out or err or ''
            if rc == 0:
                healthy = True
                break
            await asyncio.sleep(2)

        if not healthy:
            _, addr_out, _ = run(['ip', '-br', '-4', 'addr', 'show', 'dev', iface], check=False)
            _, route_out, _ = run(['ip', '-4', 'route', 'show', 'dev', iface], check=False)
            local_log = _local_log(eid)
            remote_diag = '\n'.join(filter(None, [
                str(remote_result.get('interface_state') or ''),
                str(remote_result.get('route_state') or ''),
                str(remote_result.get('ping_output') or ''),
            ]))
            raise RuntimeError(
                'GRE ساخته شد ولی Peer Ping نشد. احتمال Block بودن IP protocol 47 در مسیر/فایروال وجود دارد.\n'
                f'IRAN_IFACE: {addr_out}\nIRAN_ROUTE: {route_out}\n'
                f'IRAN_LOG: {local_log[-1600:]}\nEXIT: {remote_diag[-1600:]}\nPING: {ping_text[-800:]}'
            )

        rec = {
            'id': eid,
            'mode': 'gre-native',
            'transport': 'gre',
            'interface': iface,
            'endpoint': endpoint,
            'port': None,
            'local_ip': local_ip,
            'remote_ip': remote_addr,
            'agent_id': agent_id,
            'gre_key': gre_key,
            'mtu': mtu,
            'public_local': iran_ip,
            'public_remote': endpoint,
        }
        core.setdefault('exits', [])
        core['exits'] = [x for x in core['exits'] if x.get('id') != eid] + [rec]
        atomic_json(CORE_CONFIG, core)

        applied = []
        if port_plan:
            job_set(job_id, message='اعمال Port Mapping روی GRE FAST')
            applied = apply_plan(eid, port_plan)

        job_set(
            job_id,
            state='done',
            message=f'{eid} GRE FAST ONLINE · {local_addr} ↔ {remote_addr}',
            result={**rec, 'mappings': applied, 'remote': remote_result},
        )
    except Exception as exc:
        if eid:
            _cleanup_local(eid)
            await _cleanup_remote(agent_id, eid)
        detail = str(exc).strip() or 'GRE provisioning failed'
        job_set(job_id, state='failed', message=f'خطا: {detail}', error=detail)
