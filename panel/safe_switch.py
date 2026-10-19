import asyncio
import secrets
import time
from pathlib import Path

from agent_control import get_agent, queue_command, wait_command
import provision_resilient as resilient
from ports import reconcile as reconcile_ports, state as port_state
from provision import CORE_CONFIG, EXITS, JOBS, atomic_json, iface_for, job_set, load_json, run, validate_id

SWITCH_LOCK = asyncio.Lock()
BACKUP_DIR = Path('/etc/tp27/backups')


def _find(exit_id):
    for row in load_json(CORE_CONFIG).get('exits', []):
        if row.get('id') == exit_id:
            return row
    return None


def _transport(row):
    value = str(row.get('transport') or row.get('mode') or 'unknown').lower().strip()
    aliases = {'gre-native': 'gre', 'fou': 'gre-fou', 'openvpn': 'openvpn-tcp', 'gost': 'gost-wss', 'tls': 'framed-tls'}
    return aliases.get(value, value)


def _backup(exit_id, row, target):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"switch-{exit_id}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.json"
    mappings = [m for m in port_state().get('mappings', []) if m.get('exit_id') == exit_id]
    atomic_json(path, {'exit_id': exit_id, 'target': target, 'exit': row, 'mappings': mappings})
    return str(path)


def _cleanup_local(cid):
    for unit in (f'tp27-link@{cid}.service', f'tp27-framed@{cid}.service'):
        run(['systemctl', 'disable', '--now', unit], check=False)
        run(['systemctl', 'reset-failed', unit], check=False)
    run(['ip', 'link', 'del', iface_for(cid)], check=False)
    try:
        (EXITS / f'{cid}.json').unlink(missing_ok=True)
    except Exception:
        pass


async def _cleanup_remote(agent_id, cid):
    try:
        cmd = queue_command(agent_id, 'delete_exit', {'id': cid})
        result = await wait_command(cmd['id'], timeout=45)
        return result.get('state') == 'done' and bool(result.get('ok'))
    except Exception:
        return False


async def _build_candidate(job_id, agent_id, cid, target, old_row):
    agent = await resilient._ensure_agent(agent_id, job_id)
    endpoint = str(agent.get('public_ip') or '')
    core = load_json(CORE_CONFIG)
    iran_ip = str(core.get('public_ip') or '')
    local_ip, remote_ip = resilient._allocate_pair(core)
    local_addr = local_ip.split('/')[0]
    remote_addr = remote_ip.split('/')[0]
    iface = iface_for(cid)
    mtu = int(old_row.get('mtu') or resilient.DEFAULT_MTU)
    gre_key = resilient._gre_key(cid, iran_ip, endpoint)

    if target == 'gre':
        healthy, detail, remote = await resilient._build_gre(
            agent_id, cid, iface, iran_ip, endpoint,
            local_ip, remote_ip, local_addr, remote_addr,
            gre_key, mtu, 'gre', 0,
        )
        if not healthy:
            raise RuntimeError('GRE Native Health Gate را پاس نکرد: ' + str(detail)[-500:])
        port = None
    else:
        remote = None
        port = None
        errors = []
        for candidate_port in resilient.FOU_PORT_CANDIDATES:
            try:
                healthy, detail, result = await resilient._build_gre(
                    agent_id, cid, iface, iran_ip, endpoint,
                    local_ip, remote_ip, local_addr, remote_addr,
                    gre_key, mtu, 'gre-fou', candidate_port,
                )
                if healthy:
                    remote = result
                    port = candidate_port
                    break
                errors.append(f'UDP/{candidate_port}: health failed')
            except Exception as exc:
                errors.append(f'UDP/{candidate_port}: {exc}')
            _cleanup_local(cid)
            await _cleanup_remote(agent_id, cid)
        if port is None:
            raise RuntimeError('GRE/UDP FOU سالم نشد: ' + ' | '.join(errors)[-900:])

    rec = {
        'id': cid,
        'mode': 'gre-fou' if target == 'gre-fou' else 'gre-native',
        'transport': target,
        'engine': 'kernel',
        'interface': iface,
        'endpoint': endpoint,
        'port': port,
        'fou_port': port if target == 'gre-fou' else None,
        'local_ip': local_ip,
        'remote_ip': remote_addr,
        'agent_id': agent_id,
        'gre_key': gre_key,
        'mtu': mtu,
        'public_local': iran_ip,
        'public_remote': endpoint,
        'service': f'tp27-link@{cid}.service',
    }
    core = load_json(CORE_CONFIG)
    core.setdefault('exits', [])
    core['exits'] = [x for x in core['exits'] if x.get('id') != cid] + [rec]
    atomic_json(CORE_CONFIG, core)
    return rec


async def safe_switch_job(job_id, exit_id):
    async with SWITCH_LOCK:
        candidate_id = None
        try:
            validate_id(exit_id)
            old = _find(exit_id)
            if not old:
                raise RuntimeError('Tunnel پیدا نشد')
            agent_id = old.get('agent_id')
            agent = get_agent(agent_id) if agent_id else None
            if not agent or not agent.get('online'):
                raise RuntimeError('Agent سرور خارج آفلاین است')

            current = _transport(old)
            target = 'gre' if current == 'gre-fou' else 'gre-fou'
            backup = _backup(exit_id, old, target)
            candidate_id = 'sw' + secrets.token_hex(6)
            job_set(job_id, state='running', message=f'ساخت {target} کنار مسیر فعلی؛ Tunnel فعلی هنوز فعال است')
            candidate = await _build_candidate(job_id, agent_id, candidate_id, target, old)

            healthy, detail = await resilient._health_peer(candidate['interface'], candidate['remote_ip'], tries=4)
            if not healthy:
                raise RuntimeError('Candidate قبل از Cutover سالم نماند: ' + str(detail)[-500:])

            wrapper = dict(candidate)
            wrapper['id'] = exit_id
            wrapper['channels'] = [dict(candidate)]
            wrapper['switch_underlay_id'] = candidate_id
            wrapper['switch_backup'] = backup
            wrapper['previous_transport'] = current
            wrapper['switched_at'] = int(time.time())

            core = load_json(CORE_CONFIG)
            core['exits'] = [x for x in core.get('exits', []) if x.get('id') not in {exit_id, candidate_id}] + [wrapper]
            atomic_json(CORE_CONFIG, core)
            reconcile_ports()

            healthy, detail = await resilient._health_peer(candidate['interface'], candidate['remote_ip'], tries=4)
            if not healthy:
                rollback = load_json(CORE_CONFIG)
                rollback['exits'] = [x for x in rollback.get('exits', []) if x.get('id') != exit_id] + [old]
                atomic_json(CORE_CONFIG, rollback)
                reconcile_ports()
                raise RuntimeError('Cutover ناموفق بود؛ مسیر قبلی Restore شد: ' + str(detail)[-500:])

            old_rows = old.get('channels') or [old]
            for row in old_rows:
                cid = str(row.get('id') or exit_id)
                if cid == candidate_id:
                    continue
                _cleanup_local(cid)
                await _cleanup_remote(agent_id, cid)

            job_set(job_id, state='done', message=f'{exit_id}: {current} → {target} با Cutover امن انجام شد', result={**wrapper, 'old_transport': current, 'new_transport': target, 'backup': backup})
        except Exception as exc:
            if candidate_id:
                live = _find(candidate_id)
                if live:
                    core = load_json(CORE_CONFIG)
                    core['exits'] = [x for x in core.get('exits', []) if x.get('id') != candidate_id]
                    atomic_json(CORE_CONFIG, core)
                _cleanup_local(candidate_id)
                old = _find(exit_id)
                if old and old.get('agent_id'):
                    await _cleanup_remote(old.get('agent_id'), candidate_id)
            job_set(job_id, state='failed', message='تعویض امن ناموفق بود؛ مسیر قبلی حفظ/Restore شد. ' + str(exc))