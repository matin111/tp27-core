from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_builder_requires_low_latency_agent_generation():
    text = (ROOT / 'panel/provision_resilient.py').read_text(encoding='utf-8')
    assert 'MIN_AGENT_VERSION = (1, 6, 0)' in text
    assert "AGENT_UPGRADE_TARGET = '1.6.0'" in text
    assert 'AGENT_RECONNECT_POLL = 1.0' in text


def test_universal_agent_polls_commands_before_heartbeat():
    text = (ROOT / 'agent/tp27-exit-agent-plain.py').read_text(encoding='utf-8')
    assert "SPEED_AGENT_VERSION = '1.7.0'" in text
    assert "TP27_AGENT_POLL_SECONDS', '0.5'" in text
    assert 'def run_https_fast()' in text
    https_body = text.split('def run_https_fast()', 1)[1].split('def run_forever()', 1)[0]
    assert https_body.index("'/api/agent/poll'") < https_body.index("'/api/agent/heartbeat'")
    plain_body = text.split('def run_forever()', 1)[1]
    assert plain_body.index("'/v1/poll'") < plain_body.index("'/v1/heartbeat'")


def test_fast_builder_settle_delays_are_bounded():
    text = (ROOT / 'panel/provision_resilient.py').read_text(encoding='utf-8')
    assert 'GRE_LOCAL_SETTLE = 0.10' in text
    assert 'TLS_LOCAL_SETTLE = 0.25' in text
    assert 'CANDIDATE_CLEANUP_SETTLE = 0.05' in text
