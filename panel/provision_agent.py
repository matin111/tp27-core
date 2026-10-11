#!/usr/bin/env python3
import asyncio
import hashlib
import ipaddress
import os
import re
import tempfile
from pathlib import Path

from agent_control import get_agent, queue_command, wait_command
from portplan import apply_plan, validate_plan
from provision import (
    CERTS,
    CORE_CONFIG,
    EXITS,
    atomic_json,
    iface_for,
    job_set,
    load_json,
    run,
    validate_id,
)

FAST_CHANNELS = 4
MIN_FAST_CHANNELS = 2
# Reverse data-plane ports live on the Iran node. 443 is intentionally not
# used because the panel/browser endpoint may own it through nginx.
FAST_DEFAULT_PORTS = [44330, 44331, 44332, 44333, 8443, 2053, 2083, 9443, 10443]
MAX_PORT_TRIES_PER_CHANNEL = 3


def _version_tuple(value):
    nums = [int(x) for x in re.findall(r'\d+', str(value or '0'))[:3]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _local_link_log(eid: str):
    rc, out, err = run([
        'journalctl', '-u', f'tp27-link@{eid}.service',
        '-n', '28', '--no-pager', '-o', 'cat'
    ], check=False)
    text = (out or err or '').strip()
    return text[-3500:]


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


def _local_tcp_ports():
    _, out, _ = run(['ss', '-lntH'], check=False)
    ports = set()
    for line in (out or '').splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        m = re.search(r':(\d+)$', local)
        if m:
            ports.add(int(m.group(1)))
    return ports


def _candidate_ports(requested, channel_index, already_used):
    values = []
    if requested:
        p = int(requested) + channel_index - 1
        if 1 <= p <= 65535:
            values.append(p)
    for p in FAST_DEFAULT_PORTS:
        if p not in values:
            values.append(p)
    busy = _local_tcp_ports()
    return [p for p in values if p not in already_used and p not in busy]


def _openssl(args):
    rc, out, err = run(['openssl', *args], check=False)
    if rc != 0:
        raise RuntimeError(err or out or 'OpenSSL failed')


def _generate_channel_pki(cid: str, iran_ip: str):
    CERTS.mkdir(parents=True, exist_ok=True)
    ca_cert = CERTS / f'{cid}-reverse-ca.crt'
    ca_key = CERTS / f'{cid}-reverse-ca.key'
    server_cert = CERTS / f'{cid}-reverse-server.crt'
    server_key = CERTS / f'{cid}-reverse-server.key'
    client_cert = CERTS / f'{cid}-reverse-client.crt'
    client_key = CERTS / f'{cid}-reverse-client.key'

    for p in (ca_cert, ca_key, server_cert, server_key, client_cert, client_key):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass

    _openssl([
        'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '3650',
        '-subj', f'/CN=TP27-{cid}-CA',
        '-addext', 'basicConstraints=critical,CA:TRUE',
        '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
        '-keyout', str(ca_key), '-out', str(ca_cert),
    ])

    with tempfile.TemporaryDirectory(prefix='tp27-pki-') as td:
        td = Path(td)
        server_csr = td / 'server.csr'
        client_csr = td / 'client.csr'
        server_ext = td / 'server.ext'
        client_ext = td / 'client.ext'
        server_ext.write_text(
            f'basicConstraints=critical,CA:FALSE\n'
            f'keyUsage=critical,digitalSignature,keyEncipherment\n'
            f'extendedKeyUsage=serverAuth\n'
            f'subjectAltName=IP:{iran_ip}\n',
            encoding='utf-8',
        )
        client_ext.write_text(
            'basicConstraints=critical,CA:FALSE\n'
            'keyUsage=critical,digitalSignature,keyEncipherment\n'
            'extendedKeyUsage=clientAuth\n',
            encoding='utf-8',
        )

        _openssl([
            'req', '-new', '-newkey', 'rsa:2048', '-nodes',
            '-subj', f'/CN={iran_ip}',
            '-keyout', str(server_key), '-out', str(server_csr),
        ])
        _openssl([
            'x509', '-req', '-in', str(server_csr),
            '-CA', str(ca_cert), '-CAkey', str(ca_key), '-CAcreateserial',
            '-days', '825', '-sha256', '-extfile', str(server_ext),
            '-out', str(server_cert),
        ])
        _openssl([
            'req', '-new', '-newkey', 'rsa:2048', '-nodes',
            '-subj', f'/CN=TP27-{cid}-EXIT',
            '-keyout', str(client_key), '-out', str(client_csr),
        ])
        _openssl([
            'x509', '-req', '-in', str(client_csr),
            '-CA', str(ca_cert), '-CAkey', str(ca_key), '-CAcreateserial',
            '-days', '825', '-sha256', '-extfile', str(client_ext),
            '-out', str(client_cert),
        ])

    os.chmod(ca_key, 0o600)
    os.chmod(server_key, 0o600)
    os.chmod(client_key, 0o600)
    for p in (ca_cert, server_cert, client_cert):
        os.chmod(p, 0o644)
    return ca_cert, server_cert, server_key, client_cert, client_key


def _write_local_reverse_channel(cid, endpoint, port, local_ip, remote_ip, mtu,
                                 ca_cert, server_cert, server_key, agent_id):
    iface = iface_for(cid)
    remote_addr = str(ipaddress.ip_interface(remote_ip).ip)
    cfg = {
        'role': 'iran-listen',
        'id': cid,
        'interface': iface,
        'tunnel_ip': local_ip,
        'remote_ip': remote_addr,
        'endpoint': endpoint,
        'listen_port': int(port),
        'mtu': int(mtu),
        'cert': str(server_cert),
        'key': str(server_key),
        'client_ca': str(ca_cert),
        'agent_id': agent_id,
        'logical_parent': True,
        'transport_direction': 'exit-to-iran',
    }
    atomic_json(EXITS / f'{cid}.json', cfg)
    return cfg


async def _wait_channel_ping(iface, remote_ip, seconds=15):
    for _ in range(seconds):
        await asyncio.sleep(1)
        rc, _, _ = run(['ping', '-I', iface, '-c', '1', '-W', '1', remote_ip], check=False)
        if rc == 0:
            return True
    return False


async def _remote_delete(agent_id, cid):
    try:
        cmd = queue_command(agent_id, 'delete_exit', {'id': cid})
        await wait_command(cmd['id'], timeout=45)
    except Exception:
        pass


def _local_delete(cid):
    run(['systemctl', 'disable', '--now', f'tp27-link@{cid}.service'], check=False)
    run(['systemctl', 'reset-failed', f'tp27-link@{cid}.service'], check=False)
    try:
        (EXITS / f'{cid}.json').unlink(missing_ok=True)
    except Exception:
        pass


async def _provision_reverse_channel(agent_id, logical_id, index, endpoint, port_candidates,
                                     local_ip, remote_ip, mtu, iran_ip, used_ports, job_id):
    cid = _channel_id(logical_id, index)
    last_error = ''

    for port in list(port_candidates)[:MAX_PORT_TRIES_PER_CHANNEL]:
        job_set(job_id, message=f'کانال {index}/{FAST_CHANNELS}: Reverse TLS روی پورت ایران {port}')
        _local_delete(cid)
        await _remote_delete(agent_id, cid)
        try:
            ca_cert, server_cert, server_key, client_cert, client_key = _generate_channel_pki(cid, iran_ip)
            cfg = _write_local_reverse_channel(
                cid, endpoint, port, local_ip, remote_ip, mtu,
                ca_cert, server_cert, server_key, agent_id,
            )

            run(['systemctl', 'daemon-reload'])
            rc, out, err = run(['systemctl', 'enable', '--now', f'tp27-link@{cid}.service'], check=False)
            if rc != 0:
                raise RuntimeError(err or out or 'Listener ایران Start نشد')
            await asyncio.sleep(1)

            command = queue_command(agent_id, 'provision_reverse_exit', {
                'id': cid,
                'iran_endpoint': iran_ip,
                'listen_port': int(port),
                'tunnel_ip': remote_ip,
                'mtu': int(mtu),
                'ca_pem': ca_cert.read_text(encoding='utf-8'),
                'client_cert_pem': client_cert.read_text(encoding='utf-8'),
                'client_key_pem': client_key.read_text(encoding='utf-8'),
            })
            result_cmd = await wait_command(command['id'], timeout=60)
            if result_cmd.get('state') != 'done' or not result_cmd.get('ok'):
                raise RuntimeError(result_cmd.get('error') or 'Reverse Agent provisioning failed')

            result = result_cmd.get('result') or {}
            if not result.get('service_active'):
                raise RuntimeError('Reverse Link خارج Active نشد')

            remote_addr = str(ipaddress.ip_interface(remote_ip).ip)
            if not await _wait_channel_ping(cfg['interface'], remote_addr, seconds=15):
                log = _local_link_log(cid)
                raise RuntimeError('Reverse Health Check رد شد' + (f'\n{log}' if log else ''))

            used_ports.add(port)
            job_set(job_id, message=f'کانال {index} ONLINE · Exit→Iran {iran_ip}:{port}')
            return {
                'id': cid,
                'index': index,
                'interface': cfg['interface'],
                'local_ip': local_ip,
                'remote_ip': remote_addr,
                'port': int(port),
                'healthy': True,
                'direction': 'exit-to-iran',
            }
        except Exception as exc:
            last_error = str(exc).strip()
            _local_delete(cid)
            await _remote_delete(agent_id, cid)
            await asyncio.sleep(1)

    raise RuntimeError(f'کانال {index} ساخته نشد: {last_error or "پورت مناسب پیدا نشد"}')


async def _cleanup_failed(agent_id, eid, channels):
    for ch in channels or []:
        cid = ch.get('id')
        if cid:
            _local_delete(cid)
            await _remote_delete(agent_id, cid)
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
        if _version_tuple(agent.get('version')) < (1, 2, 0):
            raise RuntimeError(
                f"Agent سرور خارج قدیمی است (v{agent.get('version','?')}). "
                "برای Reverse FAST نسخه 1.2.0 یا جدیدتر لازم است."
            )

        endpoint = agent.get('public_ip')
        if not endpoint:
            raise RuntimeError('IP سرور خارج در Agent ثبت نشده است')
        ipaddress.ip_address(endpoint)

        job_set(job_id, state='running', message=f'Agent {agent_id} آنلاین است')
        job_set(job_id, message='REVERSE FAST: اتصال Data Plane از خارج به ایران')

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
        ipaddress.ip_address(iran_ip)

        port_plan = validate_plan(mappings or [])
        addresses = _allocate_channel_addresses(core, FAST_CHANNELS)
        used_ports = set()
        errors = []

        for index, (local_ip, remote_ip) in enumerate(addresses, start=1):
            candidates = _candidate_ports(tunnel_port, index, used_ports)
            try:
                ch = await _provision_reverse_channel(
                    agent_id, eid, index, endpoint, candidates,
                    local_ip, remote_ip, int(mtu), iran_ip, used_ports, job_id,
                )
                channels.append(ch)
            except Exception as exc:
                errors.append(str(exc))
                job_set(job_id, message=f'کانال {index} رد شد')
                # If channel 1 cannot establish after several local ports, the
                # problem is architectural/cert/agent related. Stop immediately
                # instead of wasting minutes testing every channel.
                if index == 1:
                    raise RuntimeError(errors[-1])

        if len(channels) < MIN_FAST_CHANNELS:
            raise RuntimeError(
                f'Reverse Fast حداقل {MIN_FAST_CHANNELS} کانال سالم لازم دارد؛ '
                f'{len(channels)} کانال بالا آمد. ' + ' | '.join(errors[-2:])
            )

        primary = channels[0]
        rec = {
            'id': eid,
            'mode': 'reverse-fast-pool',
            'channel_count': len(channels),
            'interface': primary['interface'],
            'endpoint': endpoint,
            'port': primary['port'],
            'local_ip': primary['local_ip'],
            'remote_ip': primary['remote_ip'],
            'agent_id': agent_id,
            'transport_direction': 'exit-to-iran',
            'channels': channels,
        }
        core.setdefault('exits', [])
        core['exits'] = [x for x in core['exits'] if x.get('id') != eid] + [rec]
        atomic_json(CORE_CONFIG, core)

        applied = []
        if port_plan:
            job_set(job_id, message='اعمال Port Mapping روی Reverse Fast Pool')
            applied = apply_plan(eid, port_plan)

        final = {**rec, 'mappings': applied, 'degraded_channels': FAST_CHANNELS - len(channels)}
        job_set(
            job_id,
            state='done',
            message=f'{eid} REVERSE FAST ONLINE · {len(channels)}/{FAST_CHANNELS} کانال',
            result=final,
        )
    except Exception as exc:
        await _cleanup_failed(agent_id, eid, channels)
        detail = str(exc).strip() or 'Reverse Fast provisioning failed'
        job_set(job_id, state='failed', message=f'خطا: {detail}', error=detail)
