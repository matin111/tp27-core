#!/usr/bin/env python3
import asyncio
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
    next_values,
    run,
    validate_id,
)


async def _tcp_probe(host: str, port: int, attempts: int = 6):
    last_error = ''
    for _ in range(attempts):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=3
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True, ''
        except Exception as exc:
            last_error = str(exc)
            await asyncio.sleep(1)
    return False, last_error


def _local_link_log(eid: str):
    rc, out, err = run([
        'journalctl', '-u', f'tp27-link@{eid}.service',
        '-n', '24', '--no-pager', '-o', 'cat'
    ], check=False)
    text = (out or err or '').strip()
    if len(text) > 3000:
        text = text[-3000:]
    return text


def _cleanup_local_failed_exit(eid: str | None):
    if not eid:
        return
    run(['systemctl', 'disable', '--now', f'tp27-link@{eid}.service'], check=False)
    try:
        (EXITS / f'{eid}.json').unlink(missing_ok=True)
    except Exception:
        pass
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
    local_started = False
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

        core = load_json(CORE_CONFIG)
        eid, local_ip, remote_ip, auto_port = next_values(core)
        if requested_id:
            eid = validate_id(requested_id)
            if any(x.get('id') == eid for x in core.get('exits', [])):
                raise RuntimeError(f'{eid} قبلاً وجود دارد')

        port = int(tunnel_port or auto_port)
        if not 1 <= port <= 65535:
            raise RuntimeError('پورت Tunnel نامعتبر است')
        if not 1000 <= int(mtu) <= 1500:
            raise RuntimeError('MTU نامعتبر است')

        iface = iface_for(eid)
        iran_ip = core.get('public_ip')
        if not iran_ip:
            raise RuntimeError('public_ip ایران در config نیست')

        port_plan = validate_plan(mappings or [])
        if port_plan:
            job_set(job_id, message=f'{len(port_plan)} Port Mapping بررسی شد')

        job_set(job_id, message=f'ساخت Certificate برای {eid}')
        client_cert, client_key = ensure_client_cert(eid, iran_ip)
        client_ca_pem = client_cert.read_text(encoding='utf-8')

        job_set(job_id, message='ارسال Provision به Agent')
        command = queue_command(agent_id, 'provision_exit', {
            'id': eid,
            'listen_port': port,
            'tunnel_ip': remote_ip,
            'mtu': int(mtu),
            'client_ca_pem': client_ca_pem,
        })
        result_cmd = await wait_command(command['id'], timeout=120)
        if result_cmd.get('state') != 'done' or not result_cmd.get('ok'):
            raise RuntimeError(result_cmd.get('error') or 'Agent provisioning failed')

        result = result_cmd.get('result') or {}
        if not result.get('service_active'):
            raise RuntimeError('سرویس Link روی سرور خارج فعال نشد')

        exit_cert_pem = result.get('exit_cert_pem', '')
        if 'BEGIN CERTIFICATE' not in exit_cert_pem:
            raise RuntimeError('Certificate خروج از Agent دریافت نشد')

        local_cert = CERTS / f'{eid}.crt'
        local_cert.parent.mkdir(parents=True, exist_ok=True)
        local_cert.write_text(exit_cert_pem.strip() + '\n', encoding='utf-8')
        os.chmod(local_cert, 0o644)

        job_set(job_id, message=f'بررسی دسترسی ایران به {endpoint}:{port}')
        reachable, probe_error = await _tcp_probe(endpoint, port)
        if not reachable:
            raise RuntimeError(
                f'پورت Tunnel {port} روی سرور خارج از سمت ایران قابل دسترس نیست'
                + (f' ({probe_error})' if probe_error else '')
            )

        # A plain TCP probe may consume the non-forking socat listener. Restart
        # the remote link before starting the real mTLS session.
        job_set(job_id, message='آماده‌سازی مجدد Link سرور خارج')
        restart = queue_command(agent_id, 'restart_link', {'id': eid})
        restart_result = await wait_command(restart['id'], timeout=45)
        if restart_result.get('state') != 'done' or not restart_result.get('ok'):
            raise RuntimeError(restart_result.get('error') or 'Restart Link سرور خارج ناموفق بود')
        await asyncio.sleep(1)

        remote_addr = str(ipaddress.ip_interface(remote_ip).ip)
        exit_cfg = {
            'role': 'iran',
            'id': eid,
            'interface': iface,
            'tunnel_ip': local_ip,
            'remote_ip': remote_addr,
            'endpoint': endpoint,
            'listen_port': port,
            'mtu': int(mtu),
            'ca_file': str(local_cert),
            'cert': str(client_cert),
            'key': str(client_key),
            'agent_id': agent_id,
        }
        atomic_json(EXITS / f'{eid}.json', exit_cfg)

        rec = {
            'id': eid,
            'interface': iface,
            'endpoint': endpoint,
            'port': port,
            'local_ip': local_ip,
            'remote_ip': remote_addr,
            'agent_id': agent_id,
        }
        core.setdefault('exits', [])
        core['exits'] = [x for x in core['exits'] if x.get('id') != eid] + [rec]
        atomic_json(CORE_CONFIG, core)

        job_set(job_id, message='راه‌اندازی Link ایران')
        run(['systemctl', 'daemon-reload'])
        rc, out, err = run(['systemctl', 'enable', '--now', f'tp27-link@{eid}.service'], check=False)
        if rc != 0:
            raise RuntimeError(err or out or 'local link failed')
        local_started = True

        job_set(job_id, message='Health Check')
        online = False
        for _ in range(25):
            await asyncio.sleep(1)
            rc, _, _ = run(['ping', '-I', iface, '-c', '1', '-W', '1', remote_addr], check=False)
            if rc == 0:
                online = True
                break
        if not online:
            log = _local_link_log(eid)
            if log:
                raise RuntimeError('Link ساخته شد اما Health Check پاسخ نداد\n' + log)
            raise RuntimeError('Link ساخته شد اما Health Check پاسخ نداد')

        applied = []
        if port_plan:
            job_set(job_id, message='اعمال Port Mapping')
            applied = apply_plan(eid, port_plan)
            job_set(job_id, message=f'{len(applied)} Port Mapping فعال شد')

        final = {
            'id': eid,
            'agent_id': agent_id,
            'interface': iface,
            'local_ip': local_ip,
            'remote_ip': remote_addr,
            'endpoint': endpoint,
            'port': port,
            'mappings': applied,
        }
        job_set(job_id, state='done', message=f'{eid} ONLINE شد', result=final)
    except Exception as exc:
        # Do not leave a dead red tunnel in the panel when provisioning failed.
        _cleanup_local_failed_exit(eid)
        detail = str(exc).strip() or 'Agent provisioning failed'
        job_set(job_id, state='failed', message=f'خطا: {detail}', error=detail)
