from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAIN_UNIT = ROOT / 'systemd' / 'tp27-exit-agent-plain.service'
HTTPS_UNIT = ROOT / 'systemd' / 'tp27-exit-agent.service'
DATA_PLANE_UNIT = ROOT / 'systemd' / 'tp27-link@.service'
BASE_AGENT = ROOT / 'agent' / 'tp27-exit-agent.py'


def test_exit_agents_do_not_hide_kernel_module_capability_probe():
    for unit in (PLAIN_UNIT, HTTPS_UNIT):
        text = unit.read_text(encoding='utf-8')
        assert 'ProtectKernelModules=false' in text
        assert 'ProtectKernelModules=true' not in text


def test_sit_preflight_still_checks_required_kernel_modules():
    text = BASE_AGENT.read_text(encoding='utf-8')
    assert "sit6in4_preflight_v1" in text
    assert "modprobe', '-n', 'sit'" in text
    assert "modprobe', '-n', 'ip6_gre'" in text
    assert "'protocol41_test': 'not_run'" in text


def test_data_plane_unit_is_not_weakened_by_agent_policy_change():
    text = DATA_PLANE_UNIT.read_text(encoding='utf-8')
    assert 'ExecStart=/usr/local/lib/tp27/tp27-link-dispatcher %i' in text
    assert 'ProtectKernelModules=false' not in text
