#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / 'agent' / 'tp27-exit-agent-plain.py').read_text(encoding='utf-8')


def test_fast_agent_generation_and_poll_default():
    assert "SPEED_AGENT_VERSION = '1.9.0'" in SRC
    assert "TP27_AGENT_POLL_SECONDS', '0.5'" in SRC


def test_remote_gre_uses_batched_cleanup_and_single_start():
    assert "def _fast_stop_exit_services" in SRC
    assert "['systemctl', 'disable', '--now', *services]" in SRC
    assert "['systemctl', 'reset-failed', *services]" in SRC
    assert "['systemctl', 'enable', '--now', svc]" in SRC


def test_remote_gre_uses_readiness_not_fixed_two_second_sleep():
    start = SRC.index('def _fast_action_provision_gre_exit')
    end = SRC.index("BASE._stop_exit_services = _fast_stop_exit_services")
    block = SRC[start:end]
    assert 'REMOTE_GRE_READY_DEADLINE' in block
    assert 'time.sleep(2)' not in block
    assert "'-c', '1'" in block
    assert "'remote_timing_v1'" in block


def test_authoritative_controller_health_gate_is_not_in_agent_fast_path():
    # The fast agent may emit one diagnostic ping, but it must not weaken or
    # replace the controller-side health gate contract.
    controller = (ROOT / 'panel' / 'provision_resilient.py').read_text(encoding='utf-8')
    assert 'HEALTH_PING_COUNT = 5' in controller
    assert 'HEALTH_REQUIRED_STREAK = 2' in controller
    assert "healthy, ping_text = await _health_peer" in controller
