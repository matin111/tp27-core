#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import time
from pathlib import Path

from agent_control import get_agent
from health_v2 import HealthPolicy, HealthSample, evaluate, new_state
from ports import reconcile as reconcile_ports
from provision import CORE_CONFIG, JOBS, atomic_json, load_json, run
import provision_resilient as resilient
import safe_switch

CONFIG_PATH = Path(os.environ.get('TP27_FAILOVER_CONFIG', '/etc/tp27/failover-v2.json'))
STATE_PATH = Path(os.environ.get('TP27_FAILOVER_STATE', '/var/lib/tp27/failover-v2-state.json'))
DEFAULT_CONFIG = {
    'enabled': False,
    'interval_seconds': 5,
    'failure_threshold': 4,
    'cooldown_seconds': 300,
    'tunnels': [],
}
SUPPORTED_AUTO_TRANSPORTS = {'gre', 'gre-fou'}


def log(event, **fields):
    suffix = ' '.join(f'{k}={v}' for k, v in fields.items() if v is not None)
    print(f'TP27_FAILOVER_V2 {event}' + (f' {suffix}' if suffix else ''), flush=True)


def _transport(row):
    value = str(row.get('transport') or row.get('mode') or '').lower().strip()
    aliases = {'gre-native': 'gre', 'fou': 'gre-fou'}
    return aliases.get(value, value)


def _active_channel(row):
    channels = row.get('channels') or []
    return dict(channels[0]) if channels else dict(row)


def _service_for(row):
    channel = _active_channel(row)
    cid = str(channel.get('id') or row.get('id') or '')
    transport = _transport(channel)
    prefix = 'tp27-framed@' if transport == 'framed-tls' else 'tp27-link@'
    return f'{prefix}{cid}.service'


def _rx_bytes(iface):
    try:
        return int(Path(f'/sys/class/net/{iface}/statistics/rx_bytes').read_text().strip())
    except Exception:
        return 0


def _ping_peer(iface, peer):
    if not iface or not peer:
        return False
    rc, _, _ = run([
        'ping', '-I', iface, '-c', '2', '-i', '0.2', '-W', '1', peer,
    ], check=False)
    return rc == 0


def _service_active(unit):
    rc, _, _ = run(['systemctl', 'is-active', '--quiet', unit], check=False)
    return rc == 0


def _agent_online(row):
    agent_id = str(row.get('agent_id') or '')
    if not agent_id:
        return False
    agent = get_agent(agent_id)
    return bool(agent and agent.get('online'))


def _probe(row, previous_rx=0):
    channel = _active_channel(row)
    iface = str(channel.get('interface') or row.get('interface') or '').split('@', 1)[0]
    peer = str(channel.get('remote_ip') or row.get('remote_ip') or '').split('/', 1)[0]
    rx_now = _rx_bytes(iface)
    sample = HealthSample(
        ts=time.time(),
        service_active=_service_active(_service_for(row)),
        peer_ok=_ping_peer(iface, peer),
        rx_progress=bool(previous_rx and rx_now > previous_rx),
        agent_online=_agent_online(row),
    )
    return sample, rx_now


def _load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        if isinstance(raw, dict):
            cfg.update(raw)
    except FileNotFoundError:
        pass
    except Exception as exc:
        log('CONFIG_ERROR', error=repr(exc))
    cfg['interval_seconds'] = max(2, min(60, int(cfg.get('interval_seconds') or 5)))
    cfg['failure_threshold'] = max(2, min(20, int(cfg.get('failure_threshold') or 4)))
    cfg['cooldown_seconds'] = max(0, min(86400, int(cfg.get('cooldown_seconds') or 300)))
    cfg['tunnels'] = [str(x) for x in (cfg.get('tunnels') or []) if str(x).strip()]
    cfg['enabled'] = bool(cfg.get('enabled'))
    return cfg


def _load_state():
    try:
        data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(STATE_PATH, state)


def _find(exit_id):
    for row in load_json(CORE_CONFIG, {'exits': []}).get('exits', []):
        if str(row.get('id') or '') == exit_id:
            return row
    return None


def _remove_candidate_record(candidate_id):
    core = load_json(CORE_CONFIG, {'exits': []})
    before = len(core.get('exits', []))
    core['exits'] = [x for x in core.get('exits', []) if x.get('id') != candidate_id]
    if len(core['exits']) != before:
        atomic_json(CORE_CONFIG, core)


async def _discard_candidate(agent_id, candidate_id):
    _remove_candidate_record(candidate_id)
    safe_switch._cleanup_local(candidate_id)
    await safe_switch._cleanup_remote(agent_id, candidate_id)


def _restore_core(old):
    core = load_json(CORE_CONFIG, {'exits': []})
    exit_id = str(old.get('id'))
    core['exits'] = [x for x in core.get('exits', []) if x.get('id') != exit_id] + [old]
    atomic_json(CORE_CONFIG, core)
    reconcile_ports()


async def _make_before_break(exit_id, state, policy):
    async with safe_switch.SWITCH_LOCK:
        old = _find(exit_id)
        if not old:
            return False, 'TUNNEL_NOT_FOUND'

        current_transport = _transport(old)
        if current_transport not in SUPPORTED_AUTO_TRANSPORTS:
            return False, 'TRANSPORT_OBSERVE_ONLY'

        # A final pre-build check prevents an unnecessary candidate when the
        # path recovered between the monitor sample and acquiring the lock.
        previous_rx = int(state.get('last_rx') or 0)
        pre_sample, pre_rx = _probe(old, previous_rx)
        state['last_rx'] = pre_rx
        if pre_sample.alive:
            return False, 'RECOVERED_BEFORE_BUILD'
        if not pre_sample.agent_online:
            return False, 'UNDERLAY_CONTROL_DOWN'

        agent_id = str(old.get('agent_id') or '')
        target = 'gre-fou' if current_transport == 'gre' else 'gre'
        backup = safe_switch._backup(exit_id, old, target)
        candidate_id = 'fv' + secrets.token_hex(5)
        job_id = 'failover-' + secrets.token_hex(8)
        JOBS[job_id] = {
            'id': job_id,
            'kind': 'auto-failover-v2',
            'state': 'running',
            'messages': [],
            'exit_id': exit_id,
        }
        log('CANDIDATE_BUILD', tunnel=exit_id, current=current_transport,
            target=target, candidate=candidate_id)

        candidate = None
        committed = False
        try:
            candidate = await safe_switch._build_candidate(
                job_id, agent_id, candidate_id, target, old,
            )
            healthy, detail = await resilient._health_peer(
                candidate['interface'], candidate['remote_ip'], tries=4,
            )
            if not healthy:
                raise RuntimeError('candidate health failed: ' + str(detail)[-400:])
            log('CANDIDATE_READY', tunnel=exit_id, candidate=candidate_id,
                target=target)

            # Make-before-break guard: the original data plane is still alive.
            # If it recovered while the candidate was being built, prefer the
            # existing path and remove the candidate without any cutover.
            current = _find(exit_id) or old
            current_sample, current_rx = _probe(current, pre_rx)
            state['last_rx'] = current_rx
            if current_sample.alive:
                log('CUTOVER_ABORT_RECOVERED', tunnel=exit_id,
                    candidate=candidate_id, reason=current_sample.reason)
                await _discard_candidate(agent_id, candidate_id)
                return False, 'RECOVERED_BEFORE_CUTOVER'

            wrapper = dict(candidate)
            wrapper['id'] = exit_id
            wrapper['channels'] = [dict(candidate)]
            wrapper['switch_underlay_id'] = candidate_id
            wrapper['switch_backup'] = backup
            wrapper['previous_transport'] = current_transport
            wrapper['switched_at'] = int(time.time())
            wrapper['failover_v2'] = True

            core = load_json(CORE_CONFIG, {'exits': []})
            core['exits'] = [
                x for x in core.get('exits', [])
                if x.get('id') not in {exit_id, candidate_id}
            ] + [wrapper]
            atomic_json(CORE_CONFIG, core)
            reconcile_ports()
            committed = True

            healthy, detail = await resilient._health_peer(
                candidate['interface'], candidate['remote_ip'], tries=4,
            )
            if not healthy:
                _restore_core(old)
                committed = False
                raise RuntimeError('post-cutover health failed: ' + str(detail)[-400:])

            # Only after the new path passed the second Health Gate is the old
            # data plane removed. This is the actual break part of MBB.
            for old_row in old.get('channels') or [old]:
                old_id = str(old_row.get('id') or exit_id)
                if old_id == candidate_id:
                    continue
                safe_switch._cleanup_local(old_id)
                await safe_switch._cleanup_remote(agent_id, old_id)

            state['last_switch'] = time.time()
            state['failure_streak'] = 0
            state['recovery_streak'] = 0
            state['phase'] = 'cooldown'
            state['last_reason'] = f'CUTOVER_{current_transport}_TO_{target}'
            log('CUTOVER_OK', tunnel=exit_id, old=current_transport,
                new=target, candidate=candidate_id)
            return True, 'CUTOVER_OK'
        except Exception as exc:
            if committed:
                _restore_core(old)
            if candidate is not None:
                await _discard_candidate(agent_id, candidate_id)
            log('CANDIDATE_FAILED', tunnel=exit_id, candidate=candidate_id,
                error=repr(exc))
            return False, 'CANDIDATE_FAILED'
        finally:
            JOBS.pop(job_id, None)


async def evaluate_once(cfg, state, only_tunnel=None, dry_run=False):
    policy = HealthPolicy(
        failure_threshold=cfg['failure_threshold'],
        recovery_threshold=1,
        cooldown_seconds=cfg['cooldown_seconds'],
    )
    selected = set(cfg.get('tunnels') or [])
    core = load_json(CORE_CONFIG, {'exits': []})

    for row in core.get('exits', []):
        exit_id = str(row.get('id') or '')
        if not exit_id:
            continue
        if only_tunnel and exit_id != only_tunnel:
            continue
        if not only_tunnel and exit_id not in selected:
            continue

        tunnel_state = dict(state.get(exit_id) or new_state())
        previous_rx = int(tunnel_state.get('last_rx') or 0)
        sample, rx_now = _probe(row, previous_rx)
        tunnel_state['last_rx'] = rx_now
        previous_phase = tunnel_state.get('phase')
        tunnel_state, action = evaluate(tunnel_state, sample, policy)
        tunnel_state['last_rx'] = rx_now
        state[exit_id] = tunnel_state

        if previous_phase != tunnel_state.get('phase') or not sample.alive:
            log('HEALTH', tunnel=exit_id, phase=tunnel_state.get('phase'),
                reason=sample.reason, failures=tunnel_state.get('failure_streak'),
                peer=int(sample.peer_ok), rx_progress=int(sample.rx_progress),
                agent=int(sample.agent_online), action=action)

        if action == 'prepare':
            if dry_run:
                log('DRY_RUN_CANDIDATE', tunnel=exit_id,
                    transport=_transport(row))
                continue
            switched, reason = await _make_before_break(
                exit_id, tunnel_state, policy,
            )
            log('FAILOVER_RESULT', tunnel=exit_id, switched=int(switched),
                reason=reason)

    _save_state(state)


async def main_async(args):
    state = _load_state()
    announced_disabled = False
    while True:
        cfg = _load_config()
        enabled = cfg['enabled'] or bool(args.tunnel)
        if not enabled:
            if not announced_disabled:
                log('DISABLED', config=str(CONFIG_PATH))
                announced_disabled = True
        else:
            announced_disabled = False
            await evaluate_once(
                cfg, state, only_tunnel=args.tunnel, dry_run=args.dry_run,
            )
        if args.once:
            return 0
        await asyncio.sleep(cfg['interval_seconds'])


def main():
    parser = argparse.ArgumentParser(prog='tp27-failover-v2')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--tunnel', help='evaluate exactly one tunnel; bypasses global enabled flag')
    parser.add_argument('--dry-run', action='store_true', help='never build or switch a candidate')
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == '__main__':
    raise SystemExit(main())
