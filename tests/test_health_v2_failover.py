from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_health_module():
    path = ROOT / 'panel' / 'health_v2.py'
    spec = importlib.util.spec_from_file_location('health_v2_test', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_health_v2_rx_progress_prevents_false_failover():
    h = load_health_module()
    policy = h.HealthPolicy(failure_threshold=4, cooldown_seconds=300)
    state = h.new_state()
    state['failure_streak'] = 3

    sample = h.HealthSample(
        ts=1000,
        service_active=True,
        peer_ok=False,
        rx_progress=True,
        agent_online=True,
    )
    state, action = h.evaluate(state, sample, policy)

    assert action == 'hold'
    assert state['phase'] == 'healthy'
    assert state['failure_streak'] == 0
    assert state['last_reason'] == 'RX_PROGRESS'


def test_health_v2_requires_persistent_failure_before_candidate():
    h = load_health_module()
    policy = h.HealthPolicy(failure_threshold=4, cooldown_seconds=300)
    state = h.new_state()

    for i in range(3):
        sample = h.HealthSample(
            ts=1000 + i * 5,
            service_active=True,
            peer_ok=False,
            rx_progress=False,
            agent_online=True,
        )
        state, action = h.evaluate(state, sample, policy)
        assert action == 'hold'

    sample = h.HealthSample(
        ts=1020,
        service_active=True,
        peer_ok=False,
        rx_progress=False,
        agent_online=True,
    )
    state, action = h.evaluate(state, sample, policy)
    assert action == 'prepare'
    assert state['phase'] == 'candidate-needed'


def test_health_v2_never_builds_when_agent_control_is_down():
    h = load_health_module()
    policy = h.HealthPolicy(failure_threshold=2, cooldown_seconds=0)
    state = h.new_state()

    for ts in (1000, 1005):
        sample = h.HealthSample(
            ts=ts,
            service_active=True,
            peer_ok=False,
            rx_progress=False,
            agent_online=False,
        )
        state, action = h.evaluate(state, sample, policy)

    assert action == 'hold'
    assert state['phase'] == 'underlay-down'


def test_health_v2_honors_post_switch_cooldown():
    h = load_health_module()
    policy = h.HealthPolicy(failure_threshold=2, cooldown_seconds=300)
    state = h.new_state()
    state['last_switch'] = 1000

    for ts in (1100, 1105):
        sample = h.HealthSample(
            ts=ts,
            service_active=True,
            peer_ok=False,
            rx_progress=False,
            agent_online=True,
        )
        state, action = h.evaluate(state, sample, policy)

    assert action == 'hold'
    assert state['phase'] == 'cooldown'


def test_failover_v2_is_make_before_break_and_rechecks_original_path():
    text = (ROOT / 'panel' / 'failover_v2.py').read_text(encoding='utf-8')

    build_at = text.index("candidate = await safe_switch._build_candidate")
    ready_at = text.index("CANDIDATE_READY", build_at)
    recheck_at = text.index("current_sample, current_rx = _probe", ready_at)
    commit_at = text.index("wrapper = dict(candidate)", recheck_at)
    verify_at = text.index("post-cutover health failed", commit_at)
    break_at = text.index("safe_switch._cleanup_local(old_id)", verify_at)

    assert build_at < ready_at < recheck_at < commit_at < verify_at < break_at
    assert "CUTOVER_ABORT_RECOVERED" in text
    assert "UNDERLAY_CONTROL_DOWN" in text
    assert "SUPPORTED_AUTO_TRANSPORTS = {'gre', 'gre-fou'}" in text


def test_failover_v2_defaults_to_inert_and_scoped():
    text = (ROOT / 'panel' / 'failover_v2.py').read_text(encoding='utf-8')
    installer = (ROOT / 'install-panel-managed.sh').read_text(encoding='utf-8')
    unit = (ROOT / 'systemd' / 'tp27-failover-v2.service').read_text(encoding='utf-8')

    assert "'enabled': False" in text
    assert "'tunnels': []" in text
    assert '"enabled": false' in installer
    assert '"tunnels": []' in installer
    assert 'health_v2.py failover_v2.py' in installer
    assert 'tp27-failover-v2.service' in installer
    assert 'FAILOVER_V2=disabled' in installer
    assert 'ExecStart=/opt/tp27-panel/venv/bin/python /opt/tp27-panel/failover_v2.py' in unit
