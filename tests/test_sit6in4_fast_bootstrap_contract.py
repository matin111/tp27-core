from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_builder_tries_native_ipv6_then_sit_then_existing_fallback():
    text = (ROOT / 'panel/provision_ipv6.py').read_text(encoding='utf-8')
    wrapper = text[text.index('async def provision_gre_via_agent'):]
    assert wrapper.index('try_ipv6_fast_path') < wrapper.index('try_sit_fast_path')
    assert wrapper.index('try_sit_fast_path') < wrapper.index('fallback.provision_gre_via_agent')
    assert "'underlay_family': 'ipv4-proto41'" in text
    assert "'speed_verification_required': True" in text


def test_sit_addresses_are_ula_and_deterministic():
    text = (ROOT / 'panel/provision_ipv6.py').read_text(encoding='utf-8')
    assert "f'fd27:{digest[:4]}:{digest[4:8]}:{digest[8:12]}::/127'" in text
    assert "hashlib.sha256(f'sit6in4|{eid}|{iran_v4}|{exit_v4}'" in text


def test_agent_exposes_capability_and_scoped_sit_transport():
    agent = (ROOT / 'agent/tp27-exit-agent.py').read_text(encoding='utf-8')
    assert "sit6in4_preflight_v1" in agent
    assert "protocol41_test': 'not_run'" in agent
    assert "transport == 'sit-ip6gre'" in agent
    assert "'role': 'sit-ip6gre-exit'" in agent
    assert "must be ULA IPv6" in agent


def test_runtime_uses_protocol41_and_nested_ip6gre_with_cleanup():
    runtime = (ROOT / 'bin/tp27-link-mtls').read_text(encoding='utf-8')
    assert 'TP27_SIT_IP6GRE_READY' in runtime
    assert 'mode sit local "$SIT_LOCAL_PUBLIC" remote "$SIT_REMOTE_PUBLIC"' in runtime
    assert 'type ip6gre local "$SIT_LOCAL_V6" remote "$SIT_REMOTE_V6"' in runtime
    assert 'TP27_SIT_IN -p 41' in runtime
    assert 'cleanup_sit_underlay_firewall' in runtime
    assert 'ip link del "$SIT_IFACE"' in runtime


def test_plain_agent_generation_bumped_for_capability_refresh():
    plain = (ROOT / 'agent/tp27-exit-agent-plain.py').read_text(encoding='utf-8')
    assert "SPEED_AGENT_VERSION = '1.9.0'" in plain


def test_minimal_host_probe_and_plain_cleanup_are_fail_safe():
    panel = (ROOT / 'panel/provision_ipv6.py').read_text(encoding='utf-8')
    plain = (ROOT / 'agent/tp27-exit-agent-plain.py').read_text(encoding='utf-8')
    assert "for candidate in ('/usr/sbin/modprobe', '/sbin/modprobe', '/usr/bin/modprobe', '/bin/modprobe')" in panel
    assert 'return False' in panel[panel.index('def _module_supported'):panel.index('def local_public_ipv6')]
    assert "BASE._sit_iface(eid) if hasattr(BASE, '_sit_iface') else ''" in plain
    assert "BASE.run(['ip', 'link', 'del', sit_iface]" in plain
