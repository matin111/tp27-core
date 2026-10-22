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
    assert 'RAW_SOCKET_CREATE' in text
    assert 'PACKET_SOCKET_CREATE' in text
    assert 'socket.SOCK_RAW' in text

    forbidden = (
        'ip link add',
        'ip route add',
        'iptables -A',
        'nft add rule',
        'sysctl -w',
        '.sendto(',
        '.send(',
    )
    for needle in forbidden:
        assert needle not in text


def test_checker_reports_layered_spoof_readiness_without_claiming_provider_egress():
    module = load_module()
    report = module._capability_result({
        'UID': '0',
        'TUN': 'yes',
        'IPROUTE': 'yes',
        'CAP_NET_ADMIN': 'yes',
        'CAP_NET_RAW': 'yes',
        'RAW_SOCKET_CREATE': 'yes',
        'PACKET_SOCKET_CREATE': 'yes',
        'DEFAULT_ROUTE': 'default via 192.0.2.1 dev eth0',
        'DEFAULT_IFACE': 'eth0',
        'MTU': '1500',
        'IP_FORWARD': '1',
        'RP_FILTER_ALL': '0',
        'RP_FILTER_DEFAULT': '0',
        'RP_FILTER_IFACE': '0',
        'IP_NONLOCAL_BIND': '1',
        'ACCEPT_LOCAL_IFACE': '0',
        'FIREWALL': 'nft',
        'GRE': 'available',
        'FOU': 'available',
        'WIREGUARD': 'available',
        'HOSTNAME': 'lab-exit',
        'OS': 'Ubuntu',
        'KERNEL': '6.8.0',
        'VIRT': 'kvm',
        'DMI_VENDOR': 'QEMU',
        'DMI_PRODUCT': 'Standard PC',
        'CLOUD_INIT': 'yes',
        'FORGED_EGRESS_TEST': 'not_run',
    })

    assert report['level'] == 'ready'
    assert report['score'] == 100
    assert report['blockers'] == []
    assert report['spoof_readiness']['overall'] == 'host_ready_provider_unknown'
    assert report['spoof_readiness']['host_runtime'] == 'ready'
    assert report['spoof_readiness']['raw_socket'] == 'ready'
    assert report['spoof_readiness']['kernel_network'] == 'ready'
    assert report['spoof_readiness']['provider_source_validation'] == 'unknown'
    assert report['spoof_readiness']['forged_egress_test'] == 'not_run'
    assert report['provider_egress']['status'] == 'unknown'
    assert report['read_only'] is True


def test_checker_marks_local_raw_socket_denial_as_host_limited():
    module = load_module()
    report = module._capability_result({
        'UID': '0',
        'TUN': 'yes',
        'IPROUTE': 'yes',
        'CAP_NET_ADMIN': 'yes',
        'CAP_NET_RAW': 'yes',
        'RAW_SOCKET_CREATE': 'no',
        'PACKET_SOCKET_CREATE': 'yes',
        'DEFAULT_ROUTE': 'default via 192.0.2.1 dev eth0',
        'DEFAULT_IFACE': 'eth0',
        'MTU': '1500',
        'IP_FORWARD': '1',
        'RP_FILTER_ALL': '0',
        'RP_FILTER_DEFAULT': '0',
        'RP_FILTER_IFACE': '0',
        'IP_NONLOCAL_BIND': '0',
        'FIREWALL': 'nft',
        'GRE': 'available',
        'FOU': 'available',
        'VIRT': 'kvm',
        'FORGED_EGRESS_TEST': 'not_run',
    })

    assert report['level'] == 'limited'
    assert report['spoof_readiness']['overall'] == 'host_limited'
    assert report['spoof_readiness']['raw_socket'] == 'blocked'
    assert any('Raw IPv4 Socket' in item for item in report['blockers'])
    assert report['provider_egress']['status'] == 'unknown'
