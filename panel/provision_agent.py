#!/usr/bin/env python3
import asyncio
import hashlib
import ipaddress
import os
from pathlib import Path

from agent_control import get_agent, queue_command, wait_command
from portplan import apply_plan, validate_plan
from provision import (
    CERTS,
    CORE_CONFIG,
    EXITS,
    atomic_json,
    ensure_client_cert,
    iface_for,
    job_set,
    load_json,
    run,
    validate_id,
)

FAST_CHANNELS = 4
MIN_FAST_CHANNELS = 2
FAST_DEFAULT_PORTS = [443, 8443, 2053, 2083, 9443, 10443, 44330, 44331, 44332, 44333]


def _local_link_log(eid: str):
    rc, out, err = run([
        'journalctl', '-u', f'tp27-link@{eid}.service',
        '-n', '24', '--no-pager', '-o', 'cat'
    ], check=False)
    text = (out or err or '').strip()
    if len(text) > 3000:
        text = text[-3000:]
    return text


def _channel_id(eid: str, index: int):
    digest = hashlib.sha1(eid.encode()).hexdigest()[:4]
    base = eid[:15]
    return f'{base}-{digest}c{index}'


def _used_networks(core):
    used = set()
    for item in core.get('exits', []):
        rows = item.get('channels') or [item]
        for row in rows:
            value = row.get('local_ip') or row.get('remote_ip')
            if not value:
                continue
            try:
                used.add(str(ipaddress.ip_interface(value).network))
            except Exception:
                try:
                    used.add(str(ipaddress.ip_network(str(value) + '/30', strict=False)))
                except Exception:
                    pass
    return used


def _allocate_channel_addresses(core, count):
    used = _used_networks(core)
    result = []
    for net in ipaddress.ip_network('10.254.250.0/24').subnets(new_prefix=30):
        if str(net) in used:
            continue
        hosts = list(net.hosts())
        result.append((f'{hosts[1]}/30', f'{hosts[0]}/30'))
        if len(result) >= count:
            return result
    raise RuntimeError('فضای آدرس Fast Pool TP27 پر شده است')


def _candidate_ports(requested, channel_index, already_used):
    values = []
    if requested:
        base = int(requested)
        p = base + channel_index - 1
        if 1 <= p <= 65535:
            values.append(p)
    for p in FAST_DEFAULT_PORTS:
        if p not in values:
            values.append(p)
    return [p for p in values if p not in already_used]


def _write_local_channel(cid, endpoint, port, local_ip, remote_ip, mtu,
                         local_cert, client_cert, client_key, agent_id):
    iface = iface_for(cid)
    remote_addr = str(ipaddress.ip_interface(remote_ip).ip)
    cfg = {
        'role': 'iran',
        'id': cid,
        'interface': iface,
        'tunnel_ip': local_ip,
        'remote_ip': remote_addr,
        'endpoint': endpoint,
        'listen_port': int(port),
        'mtu': int(mtu),
        'ca_file': str(local_cert),
        'cert': str(client_cert),
        'key': str(client_key),
        'agent_id': agent_id,
        'logical_parent': True,
    }
    atomic_json(EXITS / f'{cid}.json', cfg)
    return cfg


async def _wait_channel_ping(iface, remote_ip, seconds=10):
    for _ in range(seconds):
        await asyncio.sleep(1)
        rc, _, _ = run(['ping', '-I', iface, '-c', '1', '-W', '1', remote_ip], check=False)
        if rc == 0:
            return True
    return False


async def _provision_channel(agent_id, logical_id, index, endpoint, port_candidates,
                             local_ip, remote_ip, mtu, iran_ip, used_ports, job_id):
    cid = _channel_id(logical_id, index)
    client_cert, client_key = ensure_client_cert(cid, iran_ip)
    client_ca_pem = client_cert.read_text(encoding='utf-8')
    last_error = ''

    for port in port_candidates:
        if port in used_ports:
            continue
        job_set(job_id, message=f'کانال {index}/{FAST_CHANNELS}: تست پورت {port}')
        try:
            command = queue_command(agent_id, 'provision_exit', {
                'id': cid,
                'listen_port': int(port),
                'tunnel_ip': remote_ip,
                'mtu': int(mtu),
                'client_ca_pem': client_ca_pem,
            })
            result_cmd = await wait_command(command['id'], timeout=120)
            if result_cmd.get('state') != 'done' or not result_cmd.get('ok'):
                raise RuntimeError(result_cmd.get('error') or 'Agent provisioning failed')

            result = result_cmd.get('result') or {}
            if not result.get('service_active'):
                raise RuntimeError('Link خارج Active نشد')
            exit_cert_pem = result.get('exit_cert_pem', '')
            if 'BEGIN CERTIFICATE' not in exit_cert_pem:
                raise RuntimeError('Certificate خروج دریافت نشد')

            local_cert = CERTS / f'{cid}.crt'
            local_cert.parent.mkdir(parents=True, exist_ok=True)
            local_cert.write_text(exit_cert_pem.strip() + '\n', encoding='utf-8')
            os.chmod(local_cert, 0o644)

            cfg = _write_local_channel(
                cid, endpoint, port, local_ip, remote_ip, mtu,
                local_cert, client_cert, client_key, agent_id,
            )

            run(['systemctl', 'daemon-reload'])
            rc, out, err = run(['systemctl', 'enable', '--now', f'tp27-link@{cid}.service'], check=False)
            if rc != 0:
                raise RuntimeError(err or out or 'Link ایران Start نشد')

            remote_addr = str(ipaddress.ip_interface(remote_ip).ip)
            if not await _wait_channel_ping(cfg['interface'], remote_addr, seconds=10):
                log = _local_link_log(cid)
                raise RuntimeError('Health Check کانال رد شد' + (f'\n{log}' if log else ''))

            used_ports.add(port)
            job_set(job_id, message=f'کانال {index} ONLINE · {endpoint}:{port}')
            return {
                'id': cid,
                'index': index,
                'interface': cfg['interface'],
                'local_ip': local_ip,
                'remote_ip': remote_addr,
                'port': int(port),
                'healthy': True,
            }
        except Exception as exc:
            last_error = str(exc).strip()
            run(['systemctl', 'disable', '--now', f'tp27-link@{cid}.service'], check=False)
            try:
                (EXITS / f'{cid}.json').unlink(missing_ok=True)
            except Exception:
                pass
            await asyncio.sleep(1)

    raise RuntimeError(f'کانال {index} ساخته نشد: {last_error or "پورت مناسب پیدا نشد"}')


def _cleanup_local_failed_exit(eid: str | None, channels=None):
    for ch in channels or []:
        cid = ch.get('id')
        if cid:
            run(['systemctl', 'disable', '--now', f'tp27-link@{cid}.service'], check=False)
            try:
                (EXITS / f'{cid}.json').unlink(missing_ok=True)
            except Exception:
                pass
    if eid:
        try:
            core = load_json(CORE_CONFIG)
            core.setdefault('exits', [])
            core['exits'] = [x for x in core['exits'] if x.get('id') != eid]
            atomic_json(CORE_CONFIG, core)
        except Exception:
            pass


async def provision_via_agent(job_id, agent_id, requested_id=None,
                              tunnel_port=None, mtu=1280, mappings=None):
    eid = None
    channels = []
    try:
        agent = get_agent(agent_id)
        if not agent:
            raise RuntimeError('Agent پیدا نشد')
        if not agent.get('online'):
            raise RuntimeError('Agent آفلاین است')

        endpoint = agent.get('public_ip')
        if not endpoint:
            raise RuntimeError('IP سرور خارج در Agent ثبت نشده است')
        ipaddress.ip_address(endpoint)

        job_set(job_id, state='running', message=f'Agent {agent_id} آنلاین است')
        job_set(job_id, message='FAST POOL: ساخت 4 کانال مستقل با Flow Stickiness')

        core = load_json(CORE_CONFIG)
        if requested_id:
            eid = validate_id(requested_id)
        else:
            n = 1
            used_ids = {x.get('id') for x in core.get('exits', [])}
            while f'exit{n:02d}' in used_ids:
                n += 1
            eid = f'exit{n:02d}'
        if any(x.get('id') == eid for x in core.get('exits', [])):
            raise RuntimeError(f'{eid} قبلاً وجود دارد')

        if tunnel_port is not None and not 1 <= int(tunnel_port) <= 65535:
            raise RuntimeError('پورت Tunnel نامعتبر است')
        if not 1000 <= int(mtu) <= 1500:
            raise RuntimeError('MTU نامعتبر است')

        iran_ip = core.get('public_ip')
        if not iran_ip:
            raise RuntimeError('public_ip ایران در config نیست')

        port_plan = validate_plan(mappings or [])
        addresses = _allocate_channel_addresses(core, FAST_CHANNELS)
        used_ports = set()
        errors = []

        for index, (local_ip, remote_ip) in enumerate(addresses, start=1):
            candidates = _candidate_ports(tunnel_port, index, used_ports)
            try:
                ch = await _provision_channel(
                    agent_id, eid, index, endpoint, candidates,
                    local_ip, remote_ip, int(mtu), iran_ip, used_ports, job_id,
                )
                channels.append(ch)
            except Exception as exc:
                errors.append(str(exc))
                job_set(job_id, message=f'کانال {index} رد شد؛ ادامه با کانال‌های سالم')

        if len(channels) < MIN_FAST_CHANNELS:
            raise RuntimeError(
                f'Fast Pool حداقل {MIN_FAST_CHANNELS} کانال سالم لازم دارد؛ '
                f'{len(channels)} کانال بالا آمد. ' + ' | '.join(errors[-2:])
            )

        primary = channels[0]
        rec = {
            'id': eid,
            'mode': 'fast-pool',
            'channel_count': len(channels),
            'interface': primary['interface'],
            'endpoint': endpoint,
            'port': primary['port'],
            'local_ip': primary['local_ip'],
            'remote_ip': primary['remote_ip'],
            'agent_id': agent_id,
            'channels': channels,
        }
        core.setdefault('exits', [])
        core['exits'] = [x for x in core['exits'] if x.get('id') != eid] + [rec]
        atomic_json(CORE_CONFIG, core)

        applied = []
        if port_plan:
            job_set(job_id, message='اعمال Port Mapping روی Fast Pool')
            applied = apply_plan(eid, port_plan)

        final = {
            **rec,
            'mappings': applied,
            'degraded_channels': FAST_CHANNELS - len(channels),
        }
        job_set(
            job_id,
            state='done',
            message=f'{eid} FAST ONLINE شد · {len(channels)}/{FAST_CHANNELS} کانال',
            result=final,
        )
    except Exception as exc:
        _cleanup_local_failed_exit(eid, channels)
        detail = str(exc).strip() or 'Fast Pool provisioning failed'
        job_set(job_id, state='failed', message=f'خطا: {detail}', error=detail)
