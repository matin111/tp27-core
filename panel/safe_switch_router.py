import asyncio
import secrets

from fastapi import APIRouter, HTTPException, Request

from auth import session_ok
from provision import JOBS, validate_id
from safe_switch import safe_switch_job

router = APIRouter()
_SWITCH_TASKS = {}


def _guard(request: Request):
    if not session_ok(request.cookies.get('tp27_session', '')):
        raise HTTPException(status_code=401, detail='authentication required')


def _track(job_id, task):
    _SWITCH_TASKS[job_id] = task
    task.add_done_callback(lambda _task: _SWITCH_TASKS.pop(job_id, None))


@router.post('/api/exits/{exit_id}/switch')
async def switch_exit_transport(request: Request, exit_id: str):
    _guard(request)
    try:
        validate_id(exit_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    for job in JOBS.values():
        if job.get('exit_id') == exit_id and job.get('kind') == 'transport-switch' and job.get('state') not in {'done', 'failed'}:
            raise HTTPException(status_code=409, detail='تعویض Transport این Tunnel در حال اجراست')

    job_id = secrets.token_hex(8)
    JOBS[job_id] = {
        'id': job_id,
        'kind': 'transport-switch',
        'state': 'queued',
        'messages': [],
        'exit_id': exit_id,
    }
    task = asyncio.create_task(safe_switch_job(job_id, exit_id))
    _track(job_id, task)
    return {'ok': True, 'job_id': job_id, 'safe_switch': True}
