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


class ExitCreate(SSHProbe):
    id: Optional[str] = None
    tunnel_port: Optional[int] = Field(default=None, ge=1, le=65535)
    mtu: int = Field(default=1280, ge=1000, le=1500)


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
    rows = []
    for item in cfg.get('exits', []):
        eid = item.get('id', '')
        iface = item.get('interface', '')
        svc_rc, _, _ = run(['systemctl','is-active','--quiet',f'tp27-link@{eid}.service'], check=False)
        ping_rc = 1
        if iface and item.get('remote_ip'):
            ping_rc, _, _ = run(['ping','-I',iface,'-c','1','-W','1',item['remote_ip']], check=False)
        rows.append({**item, 'healthy': svc_rc == 0 and ping_rc == 0})
    return {'version': cfg.get('version'), 'role': cfg.get('role'),
            'public_ip': cfg.get('public_ip'), 'exits': rows}


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
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not body.password and not body.private_key:
        raise HTTPException(status_code=400, detail='رمز یا کلید SSH لازم است')
    job_id = secrets.token_hex(8)
    JOBS[job_id] = {'id': job_id, 'state': 'queued', 'messages': []}
    asyncio.create_task(provision(job_id, body.host, body.port, body.username,
                                  body.password, body.private_key, body.id,
                                  body.tunnel_port, body.mtu))
    return {'job_id': job_id}


@app.get('/api/jobs/{job_id}')
async def job(request: Request, job_id: str):
    guard(request)
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail='job not found')
    return JOBS[job_id]
