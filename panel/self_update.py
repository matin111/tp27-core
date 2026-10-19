#!/usr/bin/env python3
import json
import os
import re
import secrets
import subprocess
import time
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from auth import session_ok

router = APIRouter()

STATE_DIR = Path('/var/lib/tp27-panel-updater')
STATE_FILE = STATE_DIR / 'state.json'
VERSION_FILE = Path('/etc/tp27/panel-version')
BACKUP_ROOT = Path('/var/backups/tp27-panel')
UPDATER = Path('/usr/local/lib/tp27/tp27-panel-self-update')
REPO = 'matin111/tp27-core'
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
TERMINAL = {'done', 'failed', 'rolled_back'}
LATEST_CACHE = {'sha': None, 'at': 0.0}
LATEST_TTL = 60


def _guard(request: Request):
    if not session_ok(request.cookies.get('tp27_session', '')):
        raise HTTPException(status_code=401, detail='authentication required')


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def _atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _current_version():
    try:
        value = VERSION_FILE.read_text(encoding='utf-8').strip().lower()
    except Exception:
        value = ''
    return value if SHA_RE.fullmatch(value) else 'unknown'


def _latest_sha(timeout=8, force=False):
    now = time.monotonic()
    cached = str(LATEST_CACHE.get('sha') or '')
    if not force and SHA_RE.fullmatch(cached) and now - float(LATEST_CACHE.get('at') or 0) < LATEST_TTL:
        return cached
    req = urllib.request.Request(
        f'https://api.github.com/repos/{REPO}/commits/main',
        headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'TP27-Control-Panel-Updater/1.0',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))
    except Exception as exc:
        if SHA_RE.fullmatch(cached):
            return cached
        raise RuntimeError(f'بررسی GitHub ناموفق بود: {exc}')
    sha = str(data.get('sha') or '').strip().lower()
    if not SHA_RE.fullmatch(sha):
        raise RuntimeError('SHA معتبر از GitHub دریافت نشد')
    LATEST_CACHE['sha'] = sha
    LATEST_CACHE['at'] = now
    return sha


def _state():
    return _read_json(STATE_FILE, {
        'job_id': None,
        'state': 'idle',
        'message': 'آماده',
        'target': None,
        'started_at': None,
        'finished_at': None,
    })


def _backups(limit=8):
    if not BACKUP_ROOT.exists():
        return []
    rows = []
    for path in sorted(BACKUP_ROOT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.is_symlink() or not path.is_dir() or not (path / 'system.tar.gz').exists():
            continue
        before = ''
        try:
            before = (path / 'version.before').read_text(encoding='utf-8').strip()
        except Exception:
            pass
        rows.append({
            'name': path.name,
            'before': before or 'unknown',
            'created_at': int(path.stat().st_mtime),
        })
        if len(rows) >= limit:
            break
    return rows


def _unit_name(job_id):
    return 'tp27-panel-update-' + re.sub(r'[^a-z0-9-]', '', job_id.lower())[:40]


def _spawn(args, unit):
    if not UPDATER.exists():
        raise RuntimeError('Self-Updater نصب نشده است')
    cmd = [
        'systemd-run', '--unit', unit, '--collect', '--quiet',
        '--property=Type=exec',
        str(UPDATER), *args,
    ]
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or 'systemd-run failed').strip())


@router.get('/api/system/update/status')
async def update_status(request: Request):
    _guard(request)
    current = _current_version()
    state = _state()
    latest = None
    check_error = None
    try:
        latest = _latest_sha()
    except Exception as exc:
        check_error = str(exc)
    return {
        'current': current,
        'latest': latest,
        'update_available': bool(latest and current != latest),
        'check_error': check_error,
        'job': state,
        'backups': _backups(),
    }


@router.post('/api/system/update/check')
async def update_check(request: Request):
    _guard(request)
    try:
        latest = _latest_sha(force=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    current = _current_version()
    return {
        'current': current,
        'latest': latest,
        'update_available': current != latest,
    }


@router.post('/api/system/update/apply')
async def update_apply(request: Request):
    _guard(request)
    state = _state()
    if state.get('state') not in TERMINAL | {'idle', None}:
        raise HTTPException(status_code=409, detail='یک عملیات بروزرسانی در حال اجراست')
    try:
        target = _latest_sha(force=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    current = _current_version()
    if current == target:
        return {'ok': True, 'already_latest': True, 'current': current, 'target': target}
    job_id = secrets.token_hex(8)
    unit = _unit_name(job_id)
    record = {
        'job_id': job_id,
        'state': 'queued',
        'message': 'بروزرسانی در صف اجرا قرار گرفت',
        'current': current,
        'target': target,
        'started_at': int(time.time()),
        'finished_at': None,
        'rollback': False,
        'unit': unit,
    }
    _atomic_json(STATE_FILE, record)
    try:
        _spawn([target, job_id], unit)
    except Exception as exc:
        record.update(state='failed', message=str(exc), finished_at=int(time.time()))
        _atomic_json(STATE_FILE, record)
        raise HTTPException(status_code=500, detail=str(exc))
    return {'ok': True, 'job_id': job_id, 'target': target, 'unit': unit}


@router.post('/api/system/update/rollback')
async def update_rollback(request: Request):
    _guard(request)
    state = _state()
    if state.get('state') not in TERMINAL | {'idle', None}:
        raise HTTPException(status_code=409, detail='یک عملیات بروزرسانی در حال اجراست')
    backups = _backups(limit=1)
    if not backups:
        raise HTTPException(status_code=404, detail='Backup قابل Rollback وجود ندارد')
    job_id = secrets.token_hex(8)
    unit = _unit_name(job_id)
    backup_name = backups[0]['name']
    record = {
        'job_id': job_id,
        'state': 'queued',
        'message': 'Rollback در صف اجرا قرار گرفت',
        'current': _current_version(),
        'target': backups[0].get('before') or 'unknown',
        'backup': backup_name,
        'started_at': int(time.time()),
        'finished_at': None,
        'rollback': True,
        'unit': unit,
    }
    _atomic_json(STATE_FILE, record)
    try:
        _spawn(['--rollback', backup_name, job_id], unit)
    except Exception as exc:
        record.update(state='failed', message=str(exc), finished_at=int(time.time()))
        _atomic_json(STATE_FILE, record)
        raise HTTPException(status_code=500, detail=str(exc))
    return {'ok': True, 'job_id': job_id, 'backup': backup_name, 'unit': unit}
