#!/usr/bin/env python3
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from auth import session_ok

ROOT = Path('/etc/tp27')
AGENTS_FILE = ROOT / 'agents.json'
COMMANDS_FILE = ROOT / 'agent-commands.json'
ENROLL_TOKEN_FILE = ROOT / 'agent-enroll.token'
CORE_CONFIG = ROOT / 'config.json'
LOCK = threading.RLock()

ALLOWED_ACTIONS = {
    'status',
    'provision_exit',
    'provision_reverse_exit',
    'provision_gre_exit',
    'restart_link',
    'delete_exit',
    'upgrade_agent',
}

router = APIRouter()


def _atomic_json(path: Path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def ensure_enrollment_token():
    ROOT.mkdir(parents=True, exist_ok=True)
    if not ENROLL_TOKEN_FILE.exists():
        ENROLL_TOKEN_FILE.write_text(secrets.token_urlsafe(32) + '\n', encoding='utf-8')
        os.chmod(ENROLL_TOKEN_FILE, 0o600)
    return ENROLL_TOKEN_FILE.read_text(encoding='utf-8').strip()


def rotate_enrollment_token():
    token = secrets.token_urlsafe(32)
    ENROLL_TOKEN_FILE.write_text(token + '\n', encoding='utf-8')
    os.chmod(ENROLL_TOKEN_FILE, 0o600)
    return token


def _valid_agent_id(value: str):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,80}', value or ''):
        raise ValueError('agent_id نامعتبر است')
    return value


def _hash_secret(secret: str):
    return hashlib.sha256(secret.encode()).hexdigest()


def _agents():
    data = _load(AGENTS_FILE, {'agents': {}})
    data.setdefault('agents', {})
    return data


def _commands():
    data = _load(COMMANDS_FILE, {'commands': []})
    data.setdefault('commands', [])
    return data


def _linked_tunnels(agent_id: str):
    core = _load(CORE_CONFIG, {'exits': []})
    return [e for e in core.get('exits', []) if e.get('agent_id') == agent_id]


def _admin_guard(request: Request):
    if not session_ok(request.cookies.get('tp27_session', '')):
        raise HTTPException(status_code=401, detail='authentication required')


def _agent_auth(request: Request):
    agent_id = request.headers.get('X-TP27-Agent-ID', '').strip()
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='agent authentication required')
    secret = auth[7:].strip()
    with LOCK:
        data = _agents()
        rec = data['agents'].get(agent_id)
    if not rec or not hmac.compare_digest(rec.get('secret_hash', ''), _hash_secret(secret)):
        raise HTTPException(status_code=401, detail='invalid agent credentials')
    return agent_id, rec


def list_agents():
    now = int(time.time())
    with LOCK:
        data = _agents()
        out = []
        for aid, rec in data['agents'].items():
            if rec.get('disabled'):
                continue
            row = {k: v for k, v in rec.items() if k != 'secret_hash'}
            row['agent_id'] = aid
            row['online'] = now - int(rec.get('last_seen', 0) or 0) <= 45
            row['tunnel_count'] = len(_linked_tunnels(aid))
            out.append(row)
        return sorted(out, key=lambda x: (not x['online'], x.get('hostname', ''), x['agent_id']))


def get_agent(agent_id: str):
    with LOCK:
        rec = _agents()['agents'].get(agent_id)
        if not rec or rec.get('disabled'):
            return None
        row = {k: v for k, v in rec.items() if k != 'secret_hash'}
    row['agent_id'] = agent_id
    row['online'] = int(time.time()) - int(row.get('last_seen', 0) or 0) <= 45
    row['tunnel_count'] = len(_linked_tunnels(agent_id))
    return row


def retire_agent(agent_id: str):
    _valid_agent_id(agent_id)
    with LOCK:
        data = _agents()
        rec = data['agents'].get(agent_id)
        if not rec or rec.get('disabled'):
            raise ValueError('سرور خارج پیدا نشد')
        rec['disabled'] = True
        rec['disabled_at'] = int(time.time())
        _atomic_json(AGENTS_FILE, data)

        commands = _commands()
        changed = False
        for item in commands.get('commands', []):
            if item.get('agent_id') == agent_id and item.get('state') in {'queued', 'delivered'}:
                item['state'] = 'failed'
                item['ok'] = False
                item['error'] = 'Agent از پنل حذف شد'
                item['finished_at'] = int(time.time())
                changed = True
        if changed:
            _atomic_json(COMMANDS_FILE, commands)
    return True


def queue_command(agent_id: str, action: str, args=None):
    _valid_agent_id(agent_id)
    if action not in ALLOWED_ACTIONS:
        raise ValueError('action مجاز نیست')
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError('Agent پیدا نشد')
    if not agent.get('online'):
        raise ValueError('Agent آفلاین است')
    item = {
        'id': secrets.token_hex(12),
        'agent_id': agent_id,
        'action': action,
        'args': args or {},
        'state': 'queued',
        'created_at': int(time.time()),
        'delivered_at': None,
        'finished_at': None,
        'ok': None,
        'result': None,
        'error': None,
    }
    with LOCK:
        data = _commands()
        data['commands'].append(item)
        data['commands'] = data['commands'][-500:]
        _atomic_json(COMMANDS_FILE, data)
    return item


def get_command(command_id: str):
    with LOCK:
        for item in _commands()['commands']:
            if item.get('id') == command_id:
                return dict(item)
    return None


async def wait_command(command_id: str, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = get_command(command_id)
        if item and item.get('state') in {'done', 'failed'}:
            return item
        await asyncio.sleep(1)
    raise TimeoutError(f'Agent command timeout: {command_id}')


class EnrollBody(BaseModel):
    enrollment_token: str = Field(min_length=16, max_length=256)
    agent_id: str = Field(min_length=3, max_length=80)
    hostname: str = Field(min_length=1, max_length=255)
    public_ip: str = Field(min_length=3, max_length=64)
    version: str = Field(default='1', max_length=32)


class HeartbeatBody(BaseModel):
    hostname: str = Field(min_length=1, max_length=255)
    public_ip: str = Field(min_length=3, max_length=64)
    version: str = Field(default='1', max_length=32)
    status: dict = Field(default_factory=dict)


class AgentResultBody(BaseModel):
    command_id: str = Field(min_length=8, max_length=80)
    ok: bool
    result: dict = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=10000)


class CommandBody(BaseModel):
    action: str
    args: dict = Field(default_factory=dict)


@router.get('/api/control-key')
async def control_key():
    return {'service': 'TP27 Controller', 'agent_api': 3, 'transport': 'outbound-https'}


@router.post('/api/agent/enroll')
async def agent_enroll(body: EnrollBody):
    try:
        aid = _valid_agent_id(body.agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    expected = ensure_enrollment_token()
    if not hmac.compare_digest(body.enrollment_token, expected):
        raise HTTPException(status_code=403, detail='invalid enrollment token')
    secret = secrets.token_urlsafe(48)
    now = int(time.time())
    with LOCK:
        data = _agents()
        old = data['agents'].get(aid, {})
        data['agents'][aid] = {
            'hostname': body.hostname,
            'public_ip': body.public_ip,
            'version': body.version,
            'secret_hash': _hash_secret(secret),
            'created_at': int(old.get('created_at', now) or now),
            'last_seen': now,
            'status': old.get('status') or {},
            'disabled': False,
        }
        _atomic_json(AGENTS_FILE, data)
    return {'ok': True, 'agent_id': aid, 'agent_secret': secret, 'poll_seconds': 5}


@router.post('/api/agent/heartbeat')
async def agent_heartbeat(request: Request, body: HeartbeatBody):
    aid, _ = _agent_auth(request)
    now = int(time.time())
    with LOCK:
        data = _agents()
        rec = data['agents'].get(aid)
        rec['hostname'] = body.hostname
        rec['public_ip'] = body.public_ip
        rec['version'] = body.version
        rec['status'] = body.status
        rec['last_seen'] = now
        _atomic_json(AGENTS_FILE, data)
    return {'ok': True, 'server_time': now}


@router.post('/api/agent/poll')
async def agent_poll(request: Request):
    aid, _ = _agent_auth(request)
    now = int(time.time())
    command = None
    with LOCK:
        data = _agents()
        rec = data['agents'].get(aid)
        rec['last_seen'] = now
        _atomic_json(AGENTS_FILE, data)

        commands = _commands()
        for item in commands['commands']:
            if item.get('agent_id') == aid and item.get('state') == 'queued':
                item['state'] = 'delivered'
                item['delivered_at'] = now
                command = {
                    'id': item['id'],
                    'action': item['action'],
                    'args': item.get('args') or {},
                }
                _atomic_json(COMMANDS_FILE, commands)
                break
    return {'ok': True, 'command': command}


@router.post('/api/agent/result')
async def agent_result(request: Request, body: AgentResultBody):
    aid, _ = _agent_auth(request)
    now = int(time.time())
    with LOCK:
        commands = _commands()
        found = False
        for item in commands['commands']:
            if item.get('id') == body.command_id and item.get('agent_id') == aid:
                item['state'] = 'done' if body.ok else 'failed'
                item['ok'] = bool(body.ok)
                item['result'] = body.result
                item['error'] = body.error
                item['finished_at'] = now
                found = True
                break
        if not found:
            raise HTTPException(status_code=404, detail='command not found')
        _atomic_json(COMMANDS_FILE, commands)
    return {'ok': True}


@router.get('/api/agents')
async def api_agents(request: Request):
    _admin_guard(request)
    return {'agents': list_agents()}


@router.get('/api/agents/{agent_id}/manage')
async def api_agent_manage(request: Request, agent_id: str):
    _admin_guard(request)
    _valid_agent_id(agent_id)
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail='سرور خارج پیدا نشد')
    tunnels = _linked_tunnels(agent_id)
    return {'agent': agent, 'tunnels': tunnels}


@router.post('/api/agents/{agent_id}/commands')
async def api_agent_command(request: Request, agent_id: str, body: CommandBody):
    _admin_guard(request)
    try:
        item = queue_command(agent_id, body.action, body.args)
        # Keep the full command record for compatibility while also returning
        # the explicit field the browser uses for polling.
        return {**item, 'command_id': item['id']}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get('/api/agents/commands/{command_id}')
async def api_agent_command_status(request: Request, command_id: str):
    _admin_guard(request)
    item = get_command(command_id)
    if not item:
        raise HTTPException(status_code=404, detail='command not found')
    return item


@router.post('/api/agents/enrollment/rotate')
async def api_rotate_enrollment(request: Request):
    _admin_guard(request)
    token = rotate_enrollment_token()
    return {'ok': True, 'token': token}


@router.delete('/api/agents/{agent_id}')
async def api_delete_agent(request: Request, agent_id: str):
    _admin_guard(request)
    _valid_agent_id(agent_id)
    tunnels = _linked_tunnels(agent_id)
    if tunnels:
        names = ', '.join(str(t.get('id')) for t in tunnels)
        raise HTTPException(status_code=409, detail=f'ابتدا Tunnelهای وابسته را حذف کنید: {names}')
    try:
        retire_agent(agent_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        'ok': True,
        'agent_id': agent_id,
        'message': 'سرور خارج از پنل حذف شد. سرویس‌های VPN اصلی سرور تغییری نکردند.',
    }
