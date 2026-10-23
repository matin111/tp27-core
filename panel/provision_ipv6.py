#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import re
import subprocess
import time
from pathlib import Path

import provision_multicore as fallback
import provision_resilient as legacy
from agent_control import get_agent, queue_command, wait_command
from portplan import apply_plan, validate_plan
from provision import CERTS, CORE_CONFIG, EXITS, atomic_json, iface_for, job_set, load_json, run, validate_id

IP6GRE_MTU = 1420
WIREGUARD_V6_MTU = 1380
WIREGUARD_PORTS = (51820, 51888, 443)
SIT_MTU = 1480
SIT_IP6GRE_MTU = 1400
IPV6_PROBE_TARGET = '2606:4700:4700::1111'
AGENT_UPGRADE_WAIT = 95


def _global_ipv6(value: str | None) -> str:
    try:
        addr = ipaddress.ip_address(str(value or '').strip().split('%', 1)[0])
    except Exception:
        return ''
    if addr.version != 6 or not addr.is_global:
        return ''
    return str(addr)


def _public_ipv4(value: str | None) -> str:
    try:
        addr = ipaddress.ip_address(str(value or '').strip())
    except Exception:
        return ''
    if addr.version != 4 or not addr.is_global:
        return ''
    return str(addr)


def _sit_iface(eid: str) -> str:
    return ('s27' + re.sub(r'[^a-z0-9]', '', str(eid).lower()))[:15]


def _sit_pair(eid: str, iran_v4: str, exit_v4: str):
    digest = hashlib.sha256(f'sit6in4|{eid}|{iran_v4}|{exit_v4}'.encode()).hexdigest()
    base = f'fd27:{digest[:4]}:{digest[4:8]}:{digest[8:12]}::/127'
    net = ipaddress.ip_network(base, strict=False)
    return str(net.network_address), str(net.network_address + 1)


def _module_supported(name: str) -> bool:
    if (Path('/sys/module') / name).exists():
        return True
    for candidate in ('/usr/sbin/modprobe', '/sbin/modprobe', '/usr/bin/modprobe', '/bin/modprobe'):
        if not Path(candidate).is_file():
            continue
        rc, _, _ = run([candidate, '-n', name], check=False)
        return rc == 0
    return False


def local_public_ipv6() -> str:
    rc, out, _ = run(['ip', '-6', 'route', 'get', IPV6_PROBE_TARGET], check=False)
    if rc == 0:
        match = re.search(r'\bsrc\s+([^\s]+)', out or '')
        addr = _global_ipv6(match.group(1) if match else '')
        if addr:
            return addr
    return ''


def _next_id(core: dict, requested_id: str | None) -> str:
    if requested_id:
        eid = validate_id(requested_id)
    else:
        used = {str(x.get('id') or '') for x in core.get('exits', [])}
        n = 1
        while f'exit{n:02d}' in used:
            n += 1
        eid = f'exit{n:02d}'
    if any(x.get('id') == eid for x in core.get('exits', [])):
        raise RuntimeError(f'{eid} قبلاً وجود دارد')
    return eid


def _write_secret(path: Path, value: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(value.strip() + '\n', encoding='utf-8')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _wg_keypair():
    rc, private, err = run(['wg', 'genkey'], check=False)
    if rc != 0 or not private.strip():
        raise RuntimeError('WireGuard tools روی سرور ایران آماده نیست: ' + (err or 'wg genkey failed'))
    proc = subprocess.run(
        ['wg', 'pubkey'], input=private.strip() + '\n', text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
    )
    public = (proc.stdout or '').strip()
    if proc.returncode != 0 or not public:
        raise RuntimeError((proc.stderr or 'wg pubkey failed').strip())
    return private.strip(), public


def _local_cfg_ip6gre(eid, iface, local_ip, peer_ip, local_v6, remote_v6, key, mtu, agent_id):
    cfg = {
        'role': 'ip6gre-iran', 'transport': 'ip6gre', 'mode': 'ip6gre',
        'id': eid, 'interface': iface, 'tunnel_ip': local_ip, 'peer_ip': peer_ip,
        'local_public_v6': local_v6, 'remote_public_v6': remote_v6,
        'gre_key': int(key), 'mtu': int(mtu), 'txqueuelen': 2000,
        'keepalive_interval': 4, 'keepalive_failures': 4, 'agent_id': agent_id,
    }
    atomic_json(EXITS / f'{eid}.json', cfg)
    return cfg


def _local_cfg_sit_ip6gre(eid, iface, sit_iface, local_ip, peer_ip,
                             iran_v4, exit_v4, local_v6, remote_v6,
                             key, agent_id):
    cfg = {
        'role': 'sit-ip6gre-iran', 'transport': 'sit-ip6gre', 'mode': 'sit-ip6gre',
        'id': eid, 'interface': iface, 'sit_interface': sit_iface,
        'tunnel_ip': local_ip, 'peer_ip': peer_ip,
        'local_public': iran_v4, 'remote_public': exit_v4,
        'sit_local_v6': local_v6, 'sit_remote_v6': remote_v6,
        'gre_key': int(key), 'mtu': SIT_IP6GRE_MTU, 'sit_mtu': SIT_MTU,
        'txqueuelen': 2000, 'keepalive_interval': 4, 'keepalive_failures': 4,
        'agent_id': agent_id,
    }
    atomic_json(EXITS / f'{eid}.json', cfg)
    return cfg


def _local_cfg_wg(eid, iface, local_ip, peer_ip, local_v6, remote_v6,
                  private_file, peer_public_key, port, mtu, agent_id):
    cfg = {
        'role': 'wireguard-v6-iran', 'transport': 'wireguard-v6', 'mode': 'wireguard-v6',
        'id': eid, 'interface': iface, 'tunnel_ip': local_ip, 'peer_ip': peer_ip,
        'local_public_v6': local_v6, 'remote_public_v6': remote_v6,
        'wg_private_key_file': str(private_file), 'wg_peer_public_key': peer_public_key,
        'listen_port': 0, 'remote_port': int(port), 'persistent_keepalive': 15,
        'mtu': int(mtu), 'txqueuelen': 2000, 'agent_id': agent_id,
    }
    atomic_json(EXITS / f'{eid}.json', cfg)
    return cfg


def _start_local(eid):
    rc, out, err = run(['systemctl', 'enable', '--now', f'tp27-link@{eid}.service'], check=False)
    if rc != 0:
        raise RuntimeError(err or out or f'tp27-link@{eid}.service Start نشد')


async def _remote_status(agent_id, iran_v6):
    command = queue_command(agent_id, 'status', {
        'ipv6_preflight_v1': True,
        'iran_public_ipv6': iran_v6,
    })
    item = await wait_command(command['id'], timeout=35)
    if item.get('state') != 'done' or not item.get('ok'):
        return None
    result = item.get('result') or {}
    return result.get('ipv6_preflight_v1')


async def _remote_sit_status(agent_id, iran_v4):
    command = queue_command(agent_id, 'status', {
        'sit6in4_preflight_v1': True,
        'iran_public_ipv4': iran_v4,
    })
    item = await wait_command(command['id'], timeout=35)
    if item.get('state') != 'done' or not item.get('ok'):
        return None
    result = item.get('result') or {}
    return result.get('sit6in4_preflight_v1')


async def _upgrade_agent_once(agent_id, job_id):
    job_set(job_id, message='IPv6 Core: ارتقای خودکار Agent برای پشتیبانی IPv6')
    command = queue_command(agent_id, 'upgrade_agent', {})
    try:
        await wait_command(command['id'], timeout=90)
    except TimeoutError:
        pass
    await asyncio.sleep(7)
    deadline = time.monotonic() + AGENT_UPGRADE_WAIT
    while time.monotonic() < deadline:
        agent = get_agent(agent_id)
        if agent and agent.get('online'):
            return True
        await asyncio.sleep(1)
    return False


async def _ipv6_preflight(agent_id, iran_v6, job_id):
    probe = await _remote_status(agent_id, iran_v6)
    if not isinstance(probe, dict) or probe.get('api') != 1:
        if not await _upgrade_agent_once(agent_id, job_id):
            return None
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                probe = await _remote_status(agent_id, iran_v6)
            except Exception:
                probe = None
            if isinstance(probe, dict) and probe.get('api') == 1:
                break
            await asyncio.sleep(1)
    if not isinstance(probe, dict) or probe.get('api') != 1:
        return None
    exit_v6 = _global_ipv6(probe.get('public_ipv6'))
    if not exit_v6 or not probe.get('route_ok'):
        return None
    return {**probe, 'public_ipv6': exit_v6}


async def _sit_preflight(agent_id, iran_v4, job_id):
    probe = await _remote_sit_status(agent_id, iran_v4)
    if not isinstance(probe, dict) or probe.get('api') != 1:
        if not await _upgrade_agent_once(agent_id, job_id):
            return None
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                probe = await _remote_sit_status(agent_id, iran_v4)
            except Exception:
                probe = None
            if isinstance(probe, dict) and probe.get('api') == 1:
                break
            await asyncio.sleep(1)
    if not isinstance(probe, dict) or probe.get('api') != 1:
        return None
    exit_v4 = _public_ipv4(probe.get('public_ipv4'))
    if not exit_v4 or not probe.get('sit_supported') or not probe.get('ip6gre_supported'):
        return None
    return {**probe, 'public_ipv4': exit_v4}


async def _remote_ip6gre(agent_id, eid, iran_v6, exit_v6, remote_ip, local_addr, key, mtu):
    command = queue_command(agent_id, 'provision_tls_exit', {
        'id': eid, 'transport': 'ip6gre',
        'iran_public_ipv6': iran_v6, 'exit_public_ipv6': exit_v6,
        'tunnel_ip': remote_ip, 'peer_ip': local_addr,
        'gre_key': int(key), 'mtu': int(mtu), 'txqueuelen': 2000,
    })
    item = await wait_command(command['id'], timeout=70)
    if item.get('state') != 'done' or not item.get('ok'):
        raise RuntimeError(item.get('error') or 'IP6GRE روی سرور خارج ساخته نشد')
    result = item.get('result') or {}
    if not result.get('service_active'):
        raise RuntimeError('IP6GRE خارج Active نشد')
    return result


async def _remote_sit_ip6gre(agent_id, eid, iran_v4, exit_v4, remote_ip,
                               local_addr, exit_sit_v6, iran_sit_v6, key):
    command = queue_command(agent_id, 'provision_tls_exit', {
        'id': eid, 'transport': 'sit-ip6gre',
        'iran_public': iran_v4, 'exit_public': exit_v4,
        'sit_local_v6': exit_sit_v6, 'sit_remote_v6': iran_sit_v6,
        'tunnel_ip': remote_ip, 'peer_ip': local_addr,
        'gre_key': int(key), 'mtu': SIT_IP6GRE_MTU, 'sit_mtu': SIT_MTU,
        'txqueuelen': 2000,
    })
    item = await wait_command(command['id'], timeout=80)
    if item.get('state') != 'done' or not item.get('ok'):
        raise RuntimeError(item.get('error') or 'SIT/6in4 + IP6GRE روی سرور خارج ساخته نشد')
    result = item.get('result') or {}
    if not result.get('service_active'):
        raise RuntimeError('SIT/6in4 خارج Active نشد')
    return result


async def _remote_wireguard(agent_id, eid, iran_v6, exit_v6, remote_ip,
                            local_addr, local_public_key, port, mtu):
    command = queue_command(agent_id, 'provision_tls_exit', {
        'id': eid, 'transport': 'wireguard-v6',
        'iran_public_ipv6': iran_v6, 'exit_public_ipv6': exit_v6,
        'tunnel_ip': remote_ip, 'peer_ip': local_addr,
        'peer_public_key': local_public_key, 'listen_port': int(port),
        'mtu': int(mtu), 'txqueuelen': 2000,
    })
    item = await wait_command(command['id'], timeout=150)
    if item.get('state') != 'done' or not item.get('ok'):
        raise RuntimeError(item.get('error') or f'WireGuard-v6 UDP/{port} روی خارج ساخته نشد')
    result = item.get('result') or {}
    public_key = str(result.get('wg_public_key') or '').strip()
    if not result.get('service_active') or len(public_key) < 40:
        raise RuntimeError('WireGuard-v6 خارج آماده نشد')
    return result


async def _cleanup(agent_id, eid, private_file=None):
    legacy._cleanup_local(eid)
    if private_file:
        try:
            Path(private_file).unlink(missing_ok=True)
        except Exception:
            pass
    await legacy._cleanup_remote(agent_id, eid)


async def _cleanup_sit(agent_id, eid, sit_iface):
    legacy._cleanup_local(eid)
    run(['ip', 'link', 'del', sit_iface], check=False)
    await legacy._cleanup_remote(agent_id, eid)


def _save_success(job_id, core, eid, iface, local_ip, peer_ip, agent_id,
                  transport, local_v6, exit_v6, mtu, port_plan, remote, port=None):
    rec = {
        'id': eid, 'mode': transport, 'transport': transport,
        'engine': 'kernel' if transport == 'ip6gre' else 'wireguard',
        'interface': iface, 'endpoint': exit_v6, 'port': int(port) if port else None,
        'local_ip': local_ip, 'remote_ip': peer_ip, 'agent_id': agent_id,
        'mtu': int(mtu), 'public_local': local_v6, 'public_remote': exit_v6,
        'underlay_family': 'ipv6', 'service': f'tp27-link@{eid}.service',
    }
    if transport == 'ip6gre':
        rec['gre_key'] = int(remote.get('gre_key') or 0)
    if transport == 'wireguard-v6':
        rec['wg_public_key'] = remote.get('wg_public_key')
    core.setdefault('exits', [])
    core['exits'] = [x for x in core['exits'] if x.get('id') != eid] + [rec]
    atomic_json(CORE_CONFIG, core)
    applied = apply_plan(eid, port_plan) if port_plan else []
    label = 'IP6GRE' if transport == 'ip6gre' else 'WireGuard-v6'
    job_set(job_id, state='done',
            message=f'{eid} {label} ONLINE · IPv6 Underlay · {local_ip.split("/")[0]} ↔ {peer_ip}',
            result={**rec, 'mappings': applied, 'remote': remote})


async def try_ipv6_fast_path(job_id, agent_id, requested_id=None, mtu=1400, mappings=None):
    agent = get_agent(agent_id)
    if not agent or not agent.get('online'):
        return False
    iran_v6 = local_public_ipv6()
    if not iran_v6:
        job_set(job_id, message='IPv6 Core: روی سرور ایران Native IPv6 پیدا نشد؛ ادامه با Coreهای فعلی')
        return False
    try:
        probe = await _ipv6_preflight(agent_id, iran_v6, job_id)
    except Exception as exc:
        job_set(job_id, message=f'IPv6 Core: Preflight ناموفق؛ fallback فعال شد: {exc}')
        return False
    if not probe:
        job_set(job_id, message='IPv6 Core: Native IPv6 دوطرفه آماده نیست؛ ادامه با Coreهای فعلی')
        return False

    exit_v6 = probe['public_ipv6']
    core = load_json(CORE_CONFIG)
    eid = _next_id(core, requested_id)
    port_plan = validate_plan(mappings or [])
    local_ip, remote_ip = legacy._allocate_pair(core)
    local_addr = str(ipaddress.ip_interface(local_ip).ip)
    remote_addr = str(ipaddress.ip_interface(remote_ip).ip)
    iface = iface_for(eid)
    job_set(job_id, state='running', message=f'IPv6 Core: Native IPv6 آماده است · {iran_v6} ↔ {exit_v6}')

    if probe.get('ip6gre_supported'):
        key = legacy._gre_key(eid, iran_v6, exit_v6)
        try:
            job_set(job_id, message='IPv6 Core مرحله 1: IP6GRE Kernel Fast Path')
            remote = await _remote_ip6gre(agent_id, eid, iran_v6, exit_v6, remote_ip, local_addr, key, IP6GRE_MTU)
            _local_cfg_ip6gre(eid, iface, local_ip, remote_addr, iran_v6, exit_v6, key, IP6GRE_MTU, agent_id)
            _start_local(eid)
            await asyncio.sleep(0.25)
            healthy, ping_text = await legacy._health_peer(iface, remote_addr, tries=8)
            if healthy:
                _save_success(job_id, core, eid, iface, local_ip, remote_addr, agent_id,
                              'ip6gre', iran_v6, exit_v6, IP6GRE_MTU, port_plan,
                              {**remote, 'gre_key': key, 'ping': ping_text})
                return True
            raise RuntimeError('Peer health IP6GRE تأیید نشد')
        except Exception as exc:
            job_set(job_id, message=f'IP6GRE ONLINE نشد: {exc} · تلاش WireGuard-v6')
            await _cleanup(agent_id, eid)

    private_file = CERTS / f'{eid}-wgv6.key'
    if probe.get('wireguard_possible'):
        for port in WIREGUARD_PORTS:
            try:
                job_set(job_id, message=f'IPv6 Core مرحله 2: WireGuard-v6 UDP/{port}')
                private_key, public_key = _wg_keypair()
                _write_secret(private_file, private_key)
                remote = await _remote_wireguard(agent_id, eid, iran_v6, exit_v6, remote_ip,
                                                 local_addr, public_key, port, WIREGUARD_V6_MTU)
                _local_cfg_wg(eid, iface, local_ip, remote_addr, iran_v6, exit_v6,
                              private_file, remote['wg_public_key'], port, WIREGUARD_V6_MTU, agent_id)
                _start_local(eid)
                await asyncio.sleep(0.7)
                healthy, ping_text = await legacy._health_peer(iface, remote_addr, tries=10)
                if healthy:
                    _save_success(job_id, core, eid, iface, local_ip, remote_addr, agent_id,
                                  'wireguard-v6', iran_v6, exit_v6, WIREGUARD_V6_MTU,
                                  port_plan, {**remote, 'ping': ping_text}, port=port)
                    return True
                raise RuntimeError('Peer health WireGuard-v6 تأیید نشد')
            except Exception as exc:
                job_set(job_id, message=f'WireGuard-v6 UDP/{port} ناموفق: {exc}')
                await _cleanup(agent_id, eid, private_file)

    job_set(job_id, message='IPv6 Fast Core سالم نشد؛ ادامه خودکار با Coreهای فعلی TP27')
    return False


async def try_sit_fast_path(job_id, agent_id, requested_id=None, mtu=1400, mappings=None):
    agent = get_agent(agent_id)
    if not agent or not agent.get('online'):
        return False
    core = load_json(CORE_CONFIG)
    iran_v4 = _public_ipv4(core.get('public_ip'))
    hinted_exit_v4 = _public_ipv4(agent.get('public_ip'))
    if not iran_v4 or not hinted_exit_v4:
        return False
    if not _module_supported('sit') or not _module_supported('ip6_gre'):
        job_set(job_id, message='SIT/6in4: Kernel ایران آماده نیست؛ ادامه با Coreهای فعلی')
        return False
    try:
        probe = await _sit_preflight(agent_id, iran_v4, job_id)
    except Exception as exc:
        job_set(job_id, message=f'SIT/6in4 Preflight ناموفق: {exc} · fallback فعال')
        return False
    if not probe:
        job_set(job_id, message='SIT/6in4: Exit قابلیت لازم را ندارد؛ ادامه با Coreهای فعلی')
        return False
    exit_v4 = probe['public_ipv4']
    eid = _next_id(core, requested_id)
    port_plan = validate_plan(mappings or [])
    local_ip, remote_ip = legacy._allocate_pair(core)
    local_addr = str(ipaddress.ip_interface(local_ip).ip)
    remote_addr = str(ipaddress.ip_interface(remote_ip).ip)
    iface = iface_for(eid)
    sit_iface = _sit_iface(eid)
    iran_sit_v6, exit_sit_v6 = _sit_pair(eid, iran_v4, exit_v4)
    key = legacy._gre_key(eid, iran_v4, exit_v4)
    job_set(job_id, state='running', message=f'SIT/6in4: تست Protocol 41 {iran_v4} ↔ {exit_v4}')
    try:
        legacy._cleanup_local(eid)
        run(['ip', 'link', 'del', sit_iface], check=False)
        _local_cfg_sit_ip6gre(
            eid, iface, sit_iface, local_ip, remote_addr, iran_v4, exit_v4,
            iran_sit_v6, exit_sit_v6, key, agent_id,
        )
        _start_local(eid)
        await asyncio.sleep(0.25)
        remote = await _remote_sit_ip6gre(
            agent_id, eid, iran_v4, exit_v4, remote_ip, local_addr,
            exit_sit_v6, iran_sit_v6, key,
        )
        healthy, ping_text = await legacy._health_peer(iface, remote_addr, tries=10)
        if not healthy:
            raise RuntimeError('Protocol 41 یا IP6GRE دوطرفه تأیید نشد')
        rec = {
            'id': eid, 'mode': 'sit-ip6gre', 'transport': 'sit-ip6gre',
            'engine': 'kernel', 'interface': iface, 'sit_interface': sit_iface,
            'endpoint': exit_v4, 'port': None, 'local_ip': local_ip,
            'remote_ip': remote_addr, 'agent_id': agent_id, 'gre_key': key,
            'mtu': SIT_IP6GRE_MTU, 'sit_mtu': SIT_MTU,
            'public_local': iran_v4, 'public_remote': exit_v4,
            'sit_local_v6': iran_sit_v6, 'sit_remote_v6': exit_sit_v6,
            'underlay_family': 'ipv4-proto41', 'overlay_family': 'ipv6',
            'service': f'tp27-link@{eid}.service',
        }
        core.setdefault('exits', [])
        core['exits'] = [x for x in core['exits'] if x.get('id') != eid] + [rec]
        atomic_json(CORE_CONFIG, core)
        applied = apply_plan(eid, port_plan) if port_plan else []
        job_set(
            job_id, state='done',
            message=f'{eid} SIT/6in4 → IP6GRE ONLINE · {local_addr} ↔ {remote_addr}',
            result={**rec, 'mappings': applied, 'remote': {**remote, 'ping': ping_text},
                    'speed_verification_required': True},
        )
        return True
    except Exception as exc:
        job_set(job_id, message=f'SIT/6in4 سالم نشد: {exc} · ادامه با Coreهای IPv4')
        await _cleanup_sit(agent_id, eid, sit_iface)
        return False


async def provision_gre_via_agent(job_id, agent_id, requested_id=None, mtu=1400, mappings=None):
    try:
        if await try_ipv6_fast_path(job_id, agent_id, requested_id, mtu, mappings):
            return
        if await try_sit_fast_path(job_id, agent_id, requested_id, mtu, mappings):
            return
    except Exception as exc:
        job_set(job_id, message=f'IPv6 Core fail-open: {exc} · ادامه با مسیر فعلی')
    return await fallback.provision_gre_via_agent(
        job_id, agent_id, requested_id=requested_id, mtu=mtu, mappings=mappings)
