#!/usr/bin/env python3
import asyncio
import ipaddress
import os
import re
import secrets
from pathlib import Path

import agent_control
import provision_resilient as legacy
from agent_control import get_agent, queue_command, wait_command
from portplan import apply_plan, validate_plan
from provision import CERTS, CORE_CONFIG, EXITS, JOBS, atomic_json, iface_for, job_set, load_json, run, validate_id

# Controller actions used by the independent fallback engines.
agent_control.ALLOWED_ACTIONS.add('provision_gost_exit')
agent_control.ALLOWED_ACTIONS.add('provision_openvpn_exit')

MIN_AGENT_VERSION = legacy.MIN_AGENT_VERSION
DEFAULT_MTU = legacy.DEFAULT_MTU
GOST_MTU = 1300
OPENVPN_MTU = 1300
# Prefer ports commonly reachable through filtered networks, then the high TCP
# ports that were useful during TP27 transport validation. Occupied ports are
# never killed or replaced; the Exit Agent simply skips them.
OPENVPN_PORT_CANDIDATES = (443, 80, 2053, 2083, 8443, 10443, 8080, 28443, 29443)
GOST_PORT_CANDIDATES = (443, 80, 8443, 2053, 2083, 10443, 8080)
GOST_BIN = Path('/usr/local/bin/tp27-gost')
GOST_INSTALLER = Path('/usr/local/lib/tp27/tp27-gost-install.sh')
GOST_CORE = Path('/usr/local/lib/tp27/tp27-gost-core.py')
GOST_DISPATCHER = Path('/usr/local/lib/tp27/tp27-link-dispatcher')
OPENVPN_CORE = Path('/usr/local/lib/tp27/tp27-openvpn-core')
_BUILD_LOCK = asyncio.Lock()


def _job_last_text(job_id):
    job = JOBS.get(job_id) or {}
    rows = job.get('messages') or []
    return str(rows[-1].get('text') or '') if rows else ''


def _write_text_atomic(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _ensure_local_openvpn():
    missing = [p for p in (OPENVPN_CORE, GOST_DISPATCHER) if not p.exists()]
    if missing:
        raise RuntimeError('OpenVPN runtime روی ایران نصب نیست؛ Installer یکپارچه TP27 را دوباره اجرا کنید')
    rc, out, err = run(['openvpn', '--version'], check=False)
    if rc != 0 or 'OpenVPN' not in (out + '\n' + err):
        raise RuntimeError('OpenVPN binary روی ایران آماده نیست؛ Installer یکپارچه TP27 را دوباره اجرا کنید')


def _openvpn_static_key():
    # 2048-bit per-tunnel random key. Never deterministic and never shared
    # between tunnel IDs. It is transported only through the authenticated
    # TP27 controller channel and stored mode 0600 on both ends.
    lines = [secrets.token_hex(16) for _ in range(16)]
    return (
        '#\n# 2048 bit OpenVPN static key (TP27 per-tunnel)\n#\n'
        '-----BEGIN OpenVPN Static key V1-----\n' +
        '\n'.join(lines) + '\n'
        '-----END OpenVPN Static key V1-----\n'
    )


def _cleanup_openvpn_local(eid):
    legacy._cleanup_local(eid)
    try:
        (CERTS / f'{eid}-openvpn.key').unlink(missing_ok=True)
    except Exception:
        pass


async def _provision_remote_openvpn(agent_id, eid, iran_ip, endpoint, remote_ip,
                                    local_addr, listen_port, static_key):
    payload = {
        'id': eid,
        'iran_public': iran_ip,
        'exit_public': endpoint,
        'tunnel_ip': remote_ip,
        'peer_ip': local_addr,
        'listen_port': int(listen_port),
        'mtu': OPENVPN_MTU,
        'static_key': static_key,
    }
    command = queue_command(agent_id, 'provision_openvpn_exit', payload)
    result_cmd = await wait_command(command['id'], timeout=100)
    if result_cmd.get('state') != 'done' or not result_cmd.get('ok'):
        raise RuntimeError(result_cmd.get('error') or f'OpenVPN/TCP روی TCP/{listen_port} ساخته نشد')
    result = result_cmd.get('result') or {}
    if not result.get('service_active'):
        raise RuntimeError('OpenVPN/TCP روی سرور خارج Active نشد')
    return result


def _write_local_openvpn(eid, iface, local_ip, remote_addr, iran_ip, endpoint,
                         listen_port, static_key, agent_id):
    secret = CERTS / f'{eid}-openvpn.key'
    _write_text_atomic(secret, static_key, 0o600)
    cfg = {
        'role': 'openvpn-client',
        'transport': 'openvpn-tcp',
        'mode': 'openvpn-tcp',
        'engine': 'openvpn',
        'id': eid,
        'interface': iface,
        'tunnel_ip': local_ip,
        'peer_ip': remote_addr,
        'local_public': iran_ip,
        'remote_public': endpoint,
        'listen_port': int(listen_port),
        'mtu': OPENVPN_MTU,
        'secret_file': str(secret),
        'agent_id': agent_id,
    }
    atomic_json(EXITS / f'{eid}.json', cfg)
    return cfg


def _start_local_openvpn(eid):
    _ensure_local_openvpn()
    run(['systemctl', 'daemon-reload'], check=False)
    run(['systemctl', 'enable', f'tp27-link@{eid}.service'], check=False)
    rc, out, err = run(['systemctl', 'restart', f'tp27-link@{eid}.service'], check=False)
    if rc != 0:
        raise RuntimeError(err or out or 'OpenVPN/TCP ایران Start نشد')


async def _build_openvpn(agent_id, eid, iface, iran_ip, endpoint,
                         local_ip, remote_ip, local_addr, remote_addr,
                         listen_port, static_key):
    remote = await _provision_remote_openvpn(
        agent_id, eid, iran_ip, endpoint, remote_ip, local_addr,
        listen_port, static_key,
    )
    _write_local_openvpn(
        eid, iface, local_ip, remote_addr, iran_ip, endpoint,
        listen_port, static_key, agent_id,
    )
    _start_local_openvpn(eid)
    await asyncio.sleep(4)
    healthy, ping_text = await legacy._health_peer(iface, remote_addr, tries=8)
    return healthy, ping_text, remote


def _ensure_local_gost():
    missing = [p for p in (GOST_INSTALLER, GOST_CORE, GOST_DISPATCHER) if not p.exists()]
    if missing:
        raise RuntimeError('GOST runtime روی ایران نصب نیست: ' + ', '.join(str(x) for x in missing))
    version_text = ''
    if GOST_BIN.exists():
        _, out, err = run([str(GOST_BIN), '-V'], check=False)
        version_text = (out + '\n' + err).strip()
    if '3.3.0' not in version_text:
        rc, out, err = run([str(GOST_INSTALLER)], check=False)
        if rc != 0:
            raise RuntimeError('نصب خودکار GOST v3.3.0 روی ایران ناموفق بود: ' + (err or out or 'download failed'))
        _, out, err = run([str(GOST_BIN), '-V'], check=False)
        version_text = (out + '\n' + err).strip()
    if '3.3.0' not in version_text:
        raise RuntimeError('GOST v3.3.0 روی ایران آماده نیست')


def _cleanup_gost_local(eid):
    legacy._cleanup_local(eid)
    try:
        (CERTS / f'{eid}-gost-ca.crt').unlink(missing_ok=True)
    except Exception:
        pass


async def _provision_remote_gost(agent_id, eid, iran_ip, endpoint, remote_ip,
                                 local_addr, listen_port, username, password, ws_path):
    payload = {
        'id': eid,
        'iran_public': iran_ip,
        'exit_public': endpoint,
        'tunnel_ip': remote_ip,
        'peer_ip': local_addr,
        'listen_port': int(listen_port),
        'mtu': GOST_MTU,
        'gost_username': username,
        'gost_password': password,
        'ws_path': ws_path,
    }
    command = queue_command(agent_id, 'provision_gost_exit', payload)
    result_cmd = await wait_command(command['id'], timeout=150)
    if result_cmd.get('state') != 'done' or not result_cmd.get('ok'):
        raise RuntimeError(result_cmd.get('error') or f'GOST/WSS روی TCP/{listen_port} ساخته نشد')
    result = result_cmd.get('result') or {}
    if not result.get('service_active'):
        raise RuntimeError('GOST/WSS روی سرور خارج Active نشد')
    cert_pem = str(result.get('server_cert_pem') or '')
    if 'BEGIN CERTIFICATE' not in cert_pem:
        raise RuntimeError('Certificate سرور GOST از Agent دریافت نشد')
    internal_port = int(result.get('internal_port') or 0)
    if not 1024 <= internal_port <= 65535:
        raise RuntimeError('Internal GOST port نامعتبر است')
    return result


def _write_local_gost(eid, iface, local_ip, remote_addr, iran_ip, endpoint,
                      listen_port, internal_port, username, password, ws_path,
                      cert_pem, agent_id):
    ca = CERTS / f'{eid}-gost-ca.crt'
    _write_text_atomic(ca, cert_pem.strip() + '\n', 0o644)
    cfg = {
        'role': 'gost-client',
        'transport': 'gost-wss',
        'mode': 'gost-wss',
        'engine': 'gost',
        'gost_version': '3.3.0',
        'id': eid,
        'interface': iface,
        'tunnel_ip': local_ip,
        'peer_ip': remote_addr,
        'local_public': iran_ip,
        'remote_public': endpoint,
        'listen_port': int(listen_port),
        'internal_port': int(internal_port),
        'mtu': GOST_MTU,
        'gost_username': username,
        'gost_password': password,
        'ws_path': ws_path,
        'ca_file': str(ca),
        'agent_id': agent_id,
    }
    atomic_json(EXITS / f'{eid}.json', cfg)
    return cfg


def _start_local_gost(eid):
    _ensure_local_gost()
    run(['systemctl', 'daemon-reload'], check=False)
    run(['systemctl', 'enable', f'tp27-link@{eid}.service'], check=False)
    rc, out, err = run(['systemctl', 'restart', f'tp27-link@{eid}.service'], check=False)
    if rc != 0:
        raise RuntimeError(err or out or 'GOST/WSS ایران Start نشد')


async def _build_gost(agent_id, eid, iface, iran_ip, endpoint,
                      local_ip, remote_ip, local_addr, remote_addr,
                      listen_port, username, password, ws_path):
    remote = await _provision_remote_gost(
        agent_id, eid, iran_ip, endpoint, remote_ip, local_addr,
        listen_port, username, password, ws_path,
    )
    _write_local_gost(
        eid, iface, local_ip, remote_addr, iran_ip, endpoint,
        listen_port, remote['internal_port'], username, password, ws_path,
        remote['server_cert_pem'], agent_id,
    )
    _start_local_gost(eid)
    await asyncio.sleep(5)
    healthy, ping_text = await legacy._health_peer(iface, remote_addr, tries=7)
    return healthy, ping_text, remote


def _resolve_context(agent_id, requested_id, mtu, mappings):
    agent = get_agent(agent_id)
    if not agent or not agent.get('online'):
        raise RuntimeError('Agent خارج آنلاین نیست')
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
    local_ip, remote_ip = legacy._allocate_pair(core)
    remote_addr = str(ipaddress.ip_interface(remote_ip).ip)
    local_addr = str(ipaddress.ip_interface(local_ip).ip)
    return agent, endpoint, core, iran_ip, eid, mtu, port_plan, local_ip, remote_ip, local_addr, remote_addr, iface_for(eid)


def _save_success(job_id, core, eid, iface, endpoint, iran_ip, local_ip,
                  remote_addr, agent_id, final_transport, final_port,
                  final_remote, port_plan, mtu):
    engine = {
        'openvpn-tcp': 'openvpn',
        'gost-wss': 'gost',
        'framed-tls': 'tp27',
        'gre': 'kernel',
        'gre-fou': 'kernel',
    }.get(final_transport, 'tp27')
    rec = {
        'id': eid,
        'mode': final_transport,
        'transport': final_transport,
        'engine': engine,
        'interface': iface,
        'endpoint': endpoint,
        'port': final_port,
        'fou_port': final_port if final_transport == 'gre-fou' else None,
        'local_ip': local_ip,
        'remote_ip': remote_addr,
        'agent_id': agent_id,
        'gre_key': None,
        'mtu': OPENVPN_MTU if final_transport == 'openvpn-tcp' else (GOST_MTU if final_transport in {'gost-wss', 'framed-tls'} else mtu),
        'public_local': iran_ip,
        'public_remote': endpoint,
        'service': f'tp27-link@{eid}.service' if final_transport != 'framed-tls' else f'tp27-framed@{eid}.service',
    }
    if final_transport == 'gost-wss':
        rec['gost_version'] = '3.3.0'
        rec['internal_port'] = final_remote.get('internal_port')
        rec['ws_path'] = final_remote.get('ws_path')
    core.setdefault('exits', [])
    core['exits'] = [x for x in core['exits'] if x.get('id') != eid] + [rec]
    atomic_json(CORE_CONFIG, core)

    applied = []
    if port_plan:
        label = {
            'openvpn-tcp': 'OPENVPN/TCP FALLBACK',
            'gost-wss': 'GOST/WSS CORE',
            'framed-tls': 'TP27 TLS FALLBACK',
        }.get(final_transport, final_transport.upper())
        job_set(job_id, message=f'اعمال Port Mapping روی {label}')
        applied = apply_plan(eid, port_plan)

    if final_transport == 'openvpn-tcp':
        done = f'{eid} OpenVPN/TCP ONLINE · TCP {final_port} · {local_ip.split("/")[0]} ↔ {remote_addr}'
    elif final_transport == 'gost-wss':
        done = f'{eid} GOST/WSS ONLINE · TCP {final_port} · {local_ip.split("/")[0]} ↔ {remote_addr}'
    else:
        done = f'{eid} TLS/TCP ONLINE · TCP {final_port} · {local_ip.split("/")[0]} ↔ {remote_addr}'
    job_set(job_id, state='done', message=done, result={**rec, 'mappings': applied, 'remote': final_remote})


async def provision_gre_via_agent(job_id, agent_id, requested_id=None,
                                  mtu=DEFAULT_MTU, mappings=None):
    # Start with native kernel transports. If they are asymmetric/filtered,
    # prefer the independently validated OpenVPN P2P/TCP transport before the
    # experimental GOST and Framed-TLS engines.
    async with _BUILD_LOCK:
        original_tls_ports = tuple(legacy.TLS_PORT_CANDIDATES)
        legacy.TLS_PORT_CANDIDATES = ()
        try:
            await legacy.provision_gre_via_agent(
                job_id, agent_id, requested_id=requested_id, mtu=mtu, mappings=mappings
            )
        finally:
            legacy.TLS_PORT_CANDIDATES = original_tls_ports

        if (JOBS.get(job_id) or {}).get('state') == 'done':
            return

        previous_error = _job_last_text(job_id)
        if 'هیچ Transport دوطرفه ONLINE نشد' not in previous_error:
            return

        eid = None
        try:
            (agent, endpoint, core, iran_ip, eid, mtu, port_plan,
             local_ip, remote_ip, local_addr, remote_addr, iface) = _resolve_context(
                agent_id, requested_id, mtu, mappings
            )

            # Stage 3: validated TCP fallback. Do not probe the TCP socket with
            # /dev/tcp first because a raw probe can consume a tcp-server
            # session. Start the real OpenVPN client directly and validate L3.
            job_set(job_id, state='running', message='مرحله 3: GRE/FOU سالم نیست؛ سوییچ به OpenVPN P2P/TCP')
            static_key = _openvpn_static_key()
            openvpn_errors = []
            for port in OPENVPN_PORT_CANDIDATES:
                job_set(job_id, message=f'OpenVPN/TCP: تست TCP {port} روی سرور خارج')
                remote = {}
                ping_text = ''
                try:
                    healthy, ping_text, remote = await _build_openvpn(
                        agent_id, eid, iface, iran_ip, endpoint,
                        local_ip, remote_ip, local_addr, remote_addr,
                        port, static_key,
                    )
                    if healthy:
                        job_set(job_id, message=f'OpenVPN/TCP روی TCP {port} دوطرفه ONLINE شد')
                        _save_success(
                            job_id, core, eid, iface, endpoint, iran_ip, local_ip,
                            remote_addr, agent_id, 'openvpn-tcp', port, remote,
                            port_plan, mtu,
                        )
                        return
                    openvpn_errors.append(f'TCP {port}: Peer Ping نشد')
                except Exception as exc:
                    openvpn_errors.append(f'TCP {port}: {exc}')
                _cleanup_openvpn_local(eid)
                await legacy._cleanup_remote(agent_id, eid)
                await asyncio.sleep(1)

            # Stage 4: retain GOST as an additional optional core.
            job_set(job_id, message='مرحله 4: OpenVPN/TCP سالم نشد؛ تست GOST/WSS')
            username = 'tp27_' + secrets.token_hex(4)
            password = secrets.token_urlsafe(24)
            ws_path = '/api/v1/stream/' + secrets.token_hex(12)
            gost_errors = []
            for port in GOST_PORT_CANDIDATES:
                job_set(job_id, message=f'GOST/WSS: تست TCP {port} روی سرور خارج')
                remote = {}
                ping_text = ''
                try:
                    healthy, ping_text, remote = await _build_gost(
                        agent_id, eid, iface, iran_ip, endpoint,
                        local_ip, remote_ip, local_addr, remote_addr,
                        port, username, password, ws_path,
                    )
                    if healthy:
                        job_set(job_id, message=f'GOST/WSS روی TCP {port} دوطرفه ONLINE شد')
                        _save_success(
                            job_id, core, eid, iface, endpoint, iran_ip, local_ip,
                            remote_addr, agent_id, 'gost-wss', port, remote,
                            port_plan, mtu,
                        )
                        return
                    gost_errors.append(f'TCP {port}: Peer Ping نشد')
                except Exception as exc:
                    gost_errors.append(f'TCP {port}: {exc}')
                _cleanup_gost_local(eid)
                await legacy._cleanup_remote(agent_id, eid)
                await asyncio.sleep(1)

            # Stage 5: final experimental framed TLS core.
            tls_errors = []
            job_set(job_id, message='مرحله 5: GOST Core سالم نشد؛ تست آخر Framed TLS/TCP')
            for port in original_tls_ports:
                job_set(job_id, message=f'Framed TLS: تست TCP {port}')
                remote = {}
                ping_text = ''
                try:
                    healthy, ping_text, remote = await legacy._build_tls(
                        agent_id, eid, iface, iran_ip, endpoint,
                        local_ip, remote_ip, local_addr, remote_addr, port,
                    )
                    if healthy:
                        job_set(job_id, message=f'Framed TLS روی TCP {port} دوطرفه ONLINE شد')
                        _save_success(
                            job_id, core, eid, iface, endpoint, iran_ip, local_ip,
                            remote_addr, agent_id, 'framed-tls', port, remote,
                            port_plan, mtu,
                        )
                        return
                    tls_errors.append(f'TCP {port}: Peer Ping نشد')
                except Exception as exc:
                    tls_errors.append(f'TCP {port}: {exc}')
                legacy._cleanup_local(eid)
                await legacy._cleanup_remote(agent_id, eid)
                await asyncio.sleep(1)

            raise RuntimeError(
                'هیچ Core دوطرفه ONLINE نشد. '
                'GRE/FOU، OpenVPN/TCP، GOST/WSS و Framed TLS Health Check را پاس نکردند.\n'
                'OPENVPN: ' + ' | '.join(openvpn_errors)[-2200:] +
                '\nGOST: ' + ' | '.join(gost_errors)[-1800:] +
                '\nTLS: ' + ' | '.join(tls_errors)[-1600:]
            )
        except Exception as exc:
            if eid:
                _cleanup_openvpn_local(eid)
                try:
                    (CERTS / f'{eid}-gost-ca.crt').unlink(missing_ok=True)
                except Exception:
                    pass
                try:
                    await legacy._cleanup_remote(agent_id, eid)
                except Exception:
                    pass
            job_set(job_id, state='failed', message='خطا: ' + str(exc))
