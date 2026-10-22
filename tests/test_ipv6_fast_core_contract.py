import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / 'panel'


def test_ipv6_builder_order_and_fail_open_contract():
    text = (PANEL / 'provision_ipv6.py').read_text(encoding='utf-8')
    assert "'ip6gre'" in text
    assert "'wireguard-v6'" in text
    assert text.index('IPv6 Core مرحله 1: IP6GRE') < text.index('IPv6 Core مرحله 2: WireGuard-v6')
    assert 'return await fallback.provision_gre_via_agent' in text
    assert "underlay_family': 'ipv6'" in text
    assert "WIREGUARD_PORTS = (51820, 51888, 443)" in text


def test_ipv6_builder_accepts_only_global_v6():
    sys.path.insert(0, str(PANEL))
    spec = importlib.util.spec_from_file_location('tp27_ipv6_test', PANEL / 'provision_ipv6.py')
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module._global_ipv6('2606:4700:4700::1111') == '2606:4700:4700::1111'
    assert module._global_ipv6('fe80::1') == ''
    assert module._global_ipv6('fd00::1') == ''
    assert module._global_ipv6('192.0.2.1') == ''


def test_agent_and_runtime_support_ipv6_transports():
    agent = (ROOT / 'agent' / 'tp27-exit-agent.py').read_text(encoding='utf-8')
    runtime = (ROOT / 'bin' / 'tp27-link-mtls').read_text(encoding='utf-8')
    assert "transport in {'ip6gre', 'wireguard-v6'}" in agent
    assert 'ipv6_preflight_v1' in agent
    assert 'public_ipv6()' in agent
    assert "type ip6gre" in runtime
    assert "type wireguard" in runtime
    assert 'endpoint "[${V6_REMOTE_PUBLIC}]:${WG_REMOTE_PORT}"' in runtime
    assert 'TP27_IP6GRE_READY' in runtime
    assert 'TP27_WGV6_READY' in runtime


def test_managed_installer_deploys_wrapper_without_restarting_data_planes():
    installer = (ROOT / 'install-panel-managed.sh').read_text(encoding='utf-8')
    assert 'wireguard-tools' in installer
    assert 'fetch panel/provision_multicore.py "$PANEL/provision_multicore.py"' in installer
    assert 'fetch panel/provision_ipv6.py "$PANEL/provision_gre.py"' in installer
    assert '# No tp27-link@ or tp27-framed@ service is restarted here.' in installer
    assert 'DATA_PLANES=verified-untouched' in installer
