#!/usr/bin/env python3
import asyncio
import ipaddress
import secrets
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_control import get_agent, queue_command, router as agent_router, wait_command
from auth import make_session, session_ok, verify_password
from provision import (
    CERTS, CORE_CONFIG, EXITS, JOBS, atomic_json, load_json,
    preflight, provision, run, validate_id,
)
from provision_gre import provision_gre_via_agent
from provision_password import preflight_password, provision_password
from ports import (
    PORT_CONFIG, add_custom, apply_profile, atomic_json as ports_atomic_json,
    reconcile as reconcile_ports, set_l2tp, state as port_state,
)
from portplan import apply_plan, validate_plan

BASE = Path(__file__).resolve().parent
app = FastAPI(title='TP27 Control Panel', docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')
app.include_router(agent_router)


def authed(request: Request):
    return session_ok(request.cookies.get('tp27_session', ''))


def guard(request: Request):
    if not authed(request):
        raise HTTPException(status_code=401, detail='authentication required')


def html(name):
    return (BASE / 'static' / name).read_text(encoding='utf-8')


def find_exit(exit_id: str):
    cfg = load_json(CORE_CONFIG)
    for item in cfg.get('exits', []):
        if item.get('id') == exit_id:
            return item
    return None


def exit_channels(item: dict):
    rows = item.get('channels') or [item]
    return [x for x in rows if x.get('id') or x.get('interface')]


def _service_for_row(row: dict):
    cid = row.get('id', '')
    transport = str(row.get('transport') or '').strip().lower()
    mode = str(row.get('mode') or '').strip().lower()
    if transport == 'framed-tls' or mode == 'framed-tls':
        return f'tp27-framed@{cid}.service'
    return f'tp27-link@{cid}.service'


def _channel_health(row: dict):
    cid = row.get('id', '')
    iface = row.get('interface', '')
    remote_ip = row.get('remote_ip', '')
    service_name = _service_for_row(row) if cid else ''
    svc_rc, svc_out, svc_err = run(
        ['systemctl', 'is-active', service_name], check=False
    ) if service_name else (1, '', '')
    iface_rc, _, _ = run(['ip', 'link', 'show', iface], check=False) if iface else (1, '', '')
    ping_rc = 1
    if iface and remote_ip:
        ping_rc, _, _ = run(['ping', '-I', iface, '-c', '1', '-W', '1', remote_ip], check=False)
    return {
        'id': cid,
        'interface': iface,
        'remote_ip': remote_ip,
        'port': row.get('port'),
        'transport': row.get('transport'),
        'service_name': service_name,
        'service': svc_rc == 0,
        'interface_up': iface_rc == 0,
        'ping': ping_rc == 0,
        'healthy': svc_rc == 0 and iface_rc == 0 and ping_rc == 0,
        'service_text': (svc_out or svc_err or '').strip(),
    }


def cleanup_local_exit(exit_id: str):
    item = find_exit(exit_id) or {'id': exit_id}
    rows = exit_channels(item)
    ids = {exit_id}
    ids.update(x.get('id') for x in rows if x.get('id'))

    for cid in ids:
        for svc in (f'tp27-link@{cid}.service', f'tp27-framed@{cid}.service'):
            run(['systemctl', 'disable', '--now', svc], check=False)
            run(['systemctl', 'reset-failed', svc], check=False)
        try:
            (EXITS / f'{cid}.json').unlink(missing_ok=True)
        except Exception:
            pass
        for path in (
            CERTS / f'{cid}.crt', CERTS / f'{cid}.key',
            CERTS / f'{cid}-iran.crt', CERTS / f'{cid}-iran.key',
            CERTS / f'{cid}-framed.crt', CERTS / f'{cid}-framed.key',
        ):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    core = load_json(CORE_CONFIG)
    core.setdefault('exits', [])
    core['exits'] = [x for x in core['exits'] if x.get('id') != exit_id]
    atomic_json(CORE_CONFIG, core)

    pstate = port_state()
    pstate['mappings'] = [m for m in pstate.get('mappings', []) if m.get('exit_id') != exit_id]
    if pstate.get('active_l2tp_exit') == exit_id:
        pstate['active_l2tp_exit'] = None
    ports_atomic_json(PORT_CONFIG, pstate)
    reconcile_ports()


async def diagnose_exit_record(item: dict):
    eid = item.get('id', '')
    checks = [_channel_health(row) for row in exit_channels(item)]
    healthy_count = sum(1 for x in checks if x['healthy'])
    total = len(checks)

    logs = []
    for ch in checks:
        if ch['healthy']:
            continue
        service_name = ch.get('service_name')
        if not service_name:
            continue
        _, out, err = run([
            'journalctl', '-u', service_name,
            '-n', '12', '--no-pager', '-o', 'cat'
        ], check=False)
        text = (out or err or '').strip()
        if text:
            logs.append(f'===== {service_name} =====\n{text[-1800:]}')
    local_log = '\n\n'.join(logs)
    if len(local_log) > 5000:
        local_log = local_log[-5000:]

    agent = get_agent(item.get('agent_id', '')) if item.get('agent_id') else None
    remote_status = ''
    if agent:
        remote_status = str((agent.get('status') or {}).get('tp27') or '')
        if len(remote_status) > 3500:
            remote_status = remote_status[-3500:]

    primary = checks[0] if checks else {}
    healthy = healthy_count > 0
    return {
        'id': eid,
        'healthy': healthy,
        'degraded': 0 < healthy_count < total,
        'local_service': healthy_count > 0,
        'interface_up': any(x['interface_up'] for x in checks),
        'peer_ping': any(x['ping'] for x in checks),
        'endpoint': item.get('endpoint'),
        'port': item.get('port'),
        'interface': item.get('interface') or primary.get('interface'),
        'remote_ip': item.get('remote_ip') or primary.get('remote_ip'),
        'agent_id': item.get('agent_id'),
        'agent_online': bool(agent and agent.get('online')),
        'agent_hostname': (agent or {}).get('hostname'),
        'local_log': local_log,
        'remote_status': remote_status,
        'mode': item.get('mode', 'single'),
        'transport': item.get('transport'),
        'channels_total': total,
        'channels_healthy': healthy_count,
        'channels': checks,
        'service_text': f'{healthy_count}/{total} channels healthy' if total else '',
    }


class SSHProbe(BaseModel):
    host: str = ''
    port: int = Field(default=22, ge=1, le=65535)
    username: str = 'root'
    password: Optional[str] = None
    private_key: Optional[str] = None
    agent_id: Optional[str] = None


class PortMappingInput(BaseModel):
    service: str = Field(min_length=1, max_length=24)
    proto: str
    listen_port: int = Field(ge=1, le=65535)
    target_port: int = Field(ge=1, le=65535)


class ExitCreate(SSHProbe):
    id: Optional[str] = None
    tunnel_port: Optional[int] = Field(default=None, ge=1, le=65535)
    mtu: int = Field(default=1280, ge=1000, le=1500)
    mappings: list[PortMappingInput] = Field(default_factory=list)


class TunnelMappingsUpdate(BaseModel):
    mappings: list[PortMappingInput] = Field(default_factory=list)


class ServiceProfile(BaseModel):
    exit_id: str
    cisco_port: int = Field(default=8897, ge=1, le=65535)
    openvpn_port: int = Field(default=9988, ge=1, le=65535)
    wireguard_port: int = Field(default=55821, ge=1, le=65535)


class CustomMapping(BaseModel):
    exit_id: str
    service: str = Field(min_length=1, max_length=24)
    proto: str
    listen_port: int = Field(ge=1, le=65535)
    target_port: int = Field(ge=1, le=65535)


class L2TPActive(BaseModel):
    exit_id: Optional[str] = None


@app.get('/login', response_class=HTMLResponse)
async def login_page(request: Request):
    return RedirectResponse('/') if authed(request) else HTMLResponse(html('login.html'))


@app.post('/login')
async def login(password: str = Form(...)):
    if not verify_password(password):
        page = html('login.html').replace('<!--ERR-->', '<div class="err">رمز عبور صحیح نیست.</div>')
        return HTMLResponse(page, status_code=401)
    response = RedirectResponse('/', status_code=303)
    response.set_cookie('tp27_session', make_session(), secure=True, httponly=True,
                        samesite='strict', max_age=43200)
    return response


@app.get('/logout')
async def logout():
    response = RedirectResponse('/login')
    response.delete_cookie('tp27_session')
    return response


@app.get('/', response_class=HTMLResponse)
async def home(request: Request):
    return HTMLResponse(html('index.html')) if authed(request) else RedirectResponse('/login')


@app.get('/api/status')
async def status(request: Request):
    guard(request)
    cfg = load_json(CORE_CONFIG)
    pstate = port_state()
    rows = []
    for item in cfg.get('exits', []):
        checks = [_channel_health(row) for row in exit_channels(item)]
        healthy_count = sum(1 for x in checks if x['healthy'])
        total = len(checks)
        eid = item.get('id', '')
        mappings = [m for m in pstate.get('mappings', []) if m.get('exit_id') == eid]
        rows.append({
            **item,
            'healthy': healthy_count > 0,
            'degraded': 0 < healthy_count < total,
            'channels_total': total,
            'channels_healthy': healthy_count,
            'channel_status': checks,
            'mappings': mappings,
            'l2tp_active': pstate.get('active_l2tp_exit') == eid,
        })
    return {
        'version': cfg.get('version'), 'role': cfg.get('role'),
        'public_ip': cfg.get('public_ip'), 'exits': rows,
        'active_l2tp_exit': pstate.get('active_l2tp_exit')
    }


@app.get('/api/exits/{exit_id}/diagnose')
async def diagnose_exit(request: Request, exit_id: str):
    guard(request)
    validate_id(exit_id)
    item = find_exit(exit_id)
    if not item:
        raise HTTPException(status_code=404, detail='تونل پیدا نشد')
    return await diagnose_exit_record(item)


@app.post('/api/exits/{exit_id}/repair')
async def repair_exit(request: Request, exit_id: str):
    guard(request)
    validate_id(exit_id)
    item = find_exit(exit_id)
    if not item:
        raise HTTPException(status_code=404, detail='تونل پیدا نشد')

    rows = exit_channels(item)
    agent_id = item.get('agent_id')
    remote_errors = []
    if agent_id:
        agent = get_agent(agent_id)
        if not agent or not agent.get('online'):
            raise HTTPException(status_code=400, detail='Agent سرور خارج آفلاین است')
        for row in rows:
            cid = row.get('id') or exit_id
            try:
                command = queue_command(agent_id, 'restart_link', {'id': cid})
                result = await wait_command(command['id'], timeout=45)
                if result.get('state') != 'done' or not result.get('ok'):
                    remote_errors.append(result.get('error') or f'Restart {cid} ناموفق بود')
            except Exception as exc:
                remote_errors.append(str(exc))

    local_errors = []
    for row in rows:
        cid = row.get('id') or exit_id
        service_name = _service_for_row({**row, 'id': cid})
        rc, out, err = run(['systemctl', 'restart', service_name], check=False)
        if rc != 0:
            local_errors.append(err or out or f'Restart {cid} ایران ناموفق بود')

    if local_errors and len(local_errors) == len(rows):
        raise HTTPException(status_code=400, detail=' | '.join(local_errors[:2]))

    await asyncio.sleep(4)
    result = await diagnose_exit_record(find_exit(exit_id))
    result['repaired'] = result.get('healthy', False)
    result['restart_warnings'] = remote_errors + local_errors
    return result


@app.put('/api/exits/{exit_id}/mappings')
async def update_exit_mappings(request: Request, exit_id: str, body: TunnelMappingsUpdate):
    guard(request)
    validate_id(exit_id)
    if not find_exit(exit_id):
        raise HTTPException(status_code=404, detail='تونل پیدا نشد')
    try:
        plan = validate_plan([m.model_dump() for m in body.mappings], exclude_exit=exit_id)
        applied = apply_plan(exit_id, plan)
        return {'ok': True, 'mappings': applied}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete('/api/exits/{exit_id}')
async def delete_exit(request: Request, exit_id: str):
    guard(request)
    validate_id(exit_id)
    item = find_exit(exit_id)
    if not item:
        raise HTTPException(status_code=404, detail='تونل پیدا نشد')

    remote_cleanup = 'not-required'
    warnings = []
    agent_id = item.get('agent_id')
    rows = exit_channels(item)
    if agent_id:
        agent = get_agent(agent_id)
        if agent and agent.get('online'):
            successful = 0
            unsupported = False
            for row in rows:
                cid = row.get('id') or exit_id
                try:
                    command = queue_command(agent_id, 'delete_exit', {'id': cid})
                    result = await wait_command(command['id'], timeout=45)
                    if result.get('state') == 'done' and result.get('ok'):
                        successful += 1
                    else:
                        err = str(result.get('error') or 'حذف روی Agent ناموفق بود')
                        if 'unsupported action' in err.lower():
                            unsupported = True
                        else:
                            warnings.append(f'{cid}: {err}')
                except Exception as exc:
                    warnings.append(f'{cid}: {exc}')
            if unsupported:
                remote_cleanup = 'pending-agent-upgrade'
                warnings.append('Agent فعلی قدیمی است؛ پاک‌سازی کامل فایل‌های TP27 خارج بعد از ارتقای Agent انجام می‌شود.')
            elif successful == len(rows):
                remote_cleanup = 'done'
            else:
                remote_cleanup = 'partial'
        else:
            remote_cleanup = 'pending-agent-online'
            warnings.append('Agent خارج آفلاین است و پاک‌سازی راه دور فعلاً انجام نشد.')

    cleanup_local_exit(exit_id)
    return {
        'ok': True,
        'id': exit_id,
        'remote_cleanup': remote_cleanup,
        'warning': ' '.join(warnings) if warnings else None,
    }


async def do_preflight(body: SSHProbe):
    if body.agent_id:
        agent = get_agent(body.agent_id)
        if not agent:
            return {'ok': False, 'error': 'Agent پیدا نشد', 'error_type': 'AgentNotFound'}
        if not agent.get('online'):
            return {'ok': False, 'error': 'Agent آفلاین است', 'error_type': 'AgentOffline'}
        return {'ok': True, 'mode': 'agent', 'agent_id': body.agent_id,
                'host': agent.get('public_ip'), 'hostname': agent.get('hostname')}

    if not body.host:
        return {'ok': False, 'error': 'IP سرور خارج لازم است', 'error_type': 'MissingHost'}
    if body.password and not body.private_key:
        return await preflight_password(body.host, body.port, body.username, body.password)
    return await preflight(body.host, body.port, body.username,
                           body.password, body.private_key)


@app.post('/api/preflight')
async def connection_preflight(request: Request, body: SSHProbe):
    guard(request)
    try:
        if not body.agent_id:
            ipaddress.ip_address(body.host)
        result = await do_preflight(body)
        if result.get('ok'):
            return result
        return JSONResponse(result, status_code=400)
    except Exception as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)


@app.post('/api/exits')
async def add_exit(request: Request, body: ExitCreate):
    guard(request)
    try:
        if not body.agent_id:
            ipaddress.ip_address(body.host)
        if body.id:
            validate_id(body.id)
        mapping_plan = validate_plan([m.model_dump() for m in body.mappings])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    probe = await do_preflight(body)
    if not probe.get('ok'):
        raise HTTPException(status_code=400, detail=probe.get('error') or 'اتصال ناموفق است')

    job_id = secrets.token_hex(8)
    JOBS[job_id] = {'id': job_id, 'state': 'queued', 'messages': []}

    if body.agent_id:
        asyncio.create_task(provision_gre_via_agent(
            job_id, body.agent_id, body.id,
            body.mtu, mapping_plan,
        ))
    elif body.password and not body.private_key:
        asyncio.create_task(provision_password(
            job_id, body.host, body.port, body.username,
            body.password, body.id, body.tunnel_port,
            body.mtu, mapping_plan,
        ))
    else:
        if not body.private_key:
            raise HTTPException(status_code=400, detail='Private Key لازم است')
        asyncio.create_task(provision(
            job_id, body.host, body.port, body.username,
            body.password, body.private_key, body.id,
            body.tunnel_port, body.mtu, mapping_plan,
        ))
    return {'job_id': job_id}


@app.get('/api/ports')
async def ports(request: Request):
    guard(request)
    return port_state()


@app.post('/api/ports/profile')
async def create_profile(request: Request, body: ServiceProfile):
    guard(request)
    try:
        return {'ok': True, 'mappings': apply_profile(body.exit_id, body.cisco_port,
                                                      body.openvpn_port, body.wireguard_port)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post('/api/ports/custom')
async def create_custom_mapping(request: Request, body: CustomMapping):
    guard(request)
    try:
        proto = body.proto.lower().strip()
        if proto not in {'tcp', 'udp'}:
            raise ValueError('پروتکل باید tcp یا udp باشد')
        item = add_custom(body.exit_id, body.service, proto,
                          body.listen_port, body.target_port)
        return {'ok': True, 'mapping': item}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post('/api/l2tp/active')
async def l2tp_active(request: Request, body: L2TPActive):
    guard(request)
    try:
        return {'ok': True, 'active_l2tp_exit': set_l2tp(body.exit_id)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get('/api/jobs/{job_id}')
async def job(request: Request, job_id: str):
    guard(request)
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail='job not found')
    return JOBS[job_id]
