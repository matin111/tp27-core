import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / 'panel'


def load_module():
    sys.path.insert(0, str(PANEL))
    path = PANEL / 'carrier_check.py'
    spec = importlib.util.spec_from_file_location('carrier_check_test', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_checker_is_read_only_and_does_not_run_forged_egress_probe():
    text = (PANEL / 'carrier_check.py').read_text(encoding='utf-8')

    assert 'FORGED_EGRESS_TEST not_run' in text
    assert 'ip.is_global' in text
    assert "'read_only': True" in text
    assert 'ip link add' not in text
    assert 'ip route add' not in text
    assert 'iptables -A' not in text
    assert 'nft add rule' not in text
    assert 'sysctl -w' not in text


def test_checker_scores_host_capabilities_without_claiming_provider_egress():
    module = load_module()
    report = module._capability_result({
        'UID': '0',
        'TUN': 'yes',
        'IPROUTE': 'yes',
        'CAP_NET_ADMIN': 'yes',
        'CAP_NET_RAW': 'yes',
        'DEFAULT_ROUTE': 'default via 192.0.2.1 dev eth0',
        'DEFAULT_IFACE': 'eth0',
        'MTU': '1500',
        'IP_FORWARD': '1',
        'RP_FILTER_ALL': '0',
        'RP_FILTER_DEFAULT': '0',
        'FIREWALL': 'nft',
        'GRE': 'available',
        'FOU': 'available',
        'WIREGUARD': 'available',
        'HOSTNAME': 'lab-exit',
        'OS': 'Ubuntu',
        'KERNEL': '6.8.0',
        'VIRT': 'kvm',
    })

    assert report['level'] == 'ready'
    assert report['score'] == 100
    assert report['blockers'] == []
    assert report['provider_egress']['status'] == 'unknown'
    assert report['read_only'] is True
