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

from auth import make_session, session_ok, verify_password
from provision import CORE_CONFIG, JOBS, load_json, preflight, provision, run, validate_id
from ports import add_custom, apply_profile, set_l2tp, state as port_state
from portplan import validate_plan

BASE = Path(__file__).resolve().parent
app = FastAPI(title='TP27 Control Panel', docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')


def authed(request: Request):
    return session_ok(request.cookies.get('tp27_session', ''))


def guard(request: Request):
    if not authed(request):
        raise HTTPException(status_code=401, detail='authentication required')


def html(name):
    return (BASE / 'static' / name).read_text(encoding='utf-8')


class SSHProbe(BaseModel):
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    username: str = 'root'
    password: Optional[str] = None
    private_key: Optional[str] = None


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
        eid = item.get('id', '')
        iface = item.get('interface', '')
        svc_rc, _, _ = run(['systemctl','is-active','--quiet',f'tp27-link@{eid}.service'], check=False)
        ping_rc = 1
        if iface and item.get('remote_ip'):
            ping_rc, _, _ = run(['ping','-I',iface,'-c','1','-W','1',item['remote_ip']], check=False)
        mappings = [m for m in pstate.get('mappings', []) if m.get('exit_id') == eid]
        rows.append({**item, 'healthy': svc_rc == 0 and ping_rc == 0,
                     'mappings': mappings,
                     'l2tp_active': pstate.get('active_l2tp_exit') == eid})
    return {'version': cfg.get('version'), 'role': cfg.get('role'),
            'public_ip': cfg.get('public_ip'), 'exits': rows,
            'active_l2tp_exit': pstate.get('active_l2tp_exit')}


@app.post('/api/preflight')
async def ssh_preflight(request: Request, body: SSHProbe):
    guard(request)
    try:
        ipaddress.ip_address(body.host)
        return await preflight(body.host, body.port, body.username,
                               body.password, body.private_key)
    except Exception as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)


@app.post('/api/exits')
async def add_exit(request: Request, body: ExitCreate):
    guard(request)
    try:
        ipaddress.ip_address(body.host)
        if body.id:
            validate_id(body.id)
        mapping_plan = validate_plan([m.model_dump() for m in body.mappings])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not body.password and not body.private_key:
        raise HTTPException(status_code=400, detail='رمز یا کلید SSH لازم است')

    probe = await preflight(body.host, body.port, body.username,
                            body.password, body.private_key)
    if not probe.get('ok'):
        raise HTTPException(status_code=400, detail=probe.get('error') or 'اتصال SSH ناموفق است')

    job_id = secrets.token_hex(8)
    JOBS[job_id] = {'id': job_id, 'state': 'queued', 'messages': []}
    asyncio.create_task(provision(job_id, body.host, body.port, body.username,
                                  body.password, body.private_key, body.id,
                                  body.tunnel_port, body.mtu, mapping_plan))
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
