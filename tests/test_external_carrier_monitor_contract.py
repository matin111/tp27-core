from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORTPLAN = ROOT / 'panel' / 'portplan.py'


def monitor_source() -> str:
    text = PORTPLAN.read_text(encoding='utf-8')
    marker = '# Read-only External Carrier Monitor'
    assert marker in text
    return text.split(marker, 1)[1]


def test_external_carrier_monitor_routes_exist():
    text = monitor_source()
    assert "'/external-carriers'" in text
    assert "'/api/external-carriers'" in text
    assert "'/api/external-carriers/{carrier_id}'" in text


def test_external_carrier_monitor_is_read_only_for_network_and_services():
    text = monitor_source()
    forbidden = (
        "'systemctl', 'restart'",
        "'systemctl', 'start'",
        "'systemctl', 'stop'",
        "'systemctl', 'enable'",
        "'systemctl', 'disable'",
        "'ip', 'link', 'add'",
        "'ip', 'link', 'del'",
        "'ip', 'route', 'add'",
        "'iptables'",
        "'nft'",
    )
    for token in forbidden:
        assert token not in text


def test_external_carrier_monitor_only_reads_expected_health_commands():
    text = monitor_source()
    assert "['ip', '-br', 'link', 'show', 'dev', iface]" in text
    assert "['ip', '-br', 'addr', 'show', 'dev', iface]" in text
    assert "['ip', '-s', 'link', 'show', 'dev', iface]" in text
    assert "['systemctl', 'is-active', service]" in text
    assert "'ping', '-I', iface" in text
