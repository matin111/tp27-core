from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / 'panel' / 'static' / 'spoof-lab-guide.html'
INSTALLER = ROOT / 'install-panel-managed.sh'


def test_spoof_lab_guide_is_isolated_and_beginner_safe():
    text = GUIDE.read_text(encoding='utf-8')

    assert 'ip netns add tp27-src' in text
    assert 'ip netns add tp27-dst' in text
    assert '10.200.0.2' in text
    assert '198.51.100.77' in text
    assert 'socket.IPPROTO_RAW' in text
    assert 'socket.IP_HDRINCL' in text
    assert 'LAB_PACKET_RECEIVED=YES' in text
    assert 'LAB_CLEANUP=OK' in text

    # The tutorial must never attach the lab namespaces to a public/default path.
    assert 'ip netns exec tp27-src ip route add default' not in text
    assert 'ip -n tp27-src route add default' not in text
    assert 'ip route add default' not in text
    assert '0.0.0.0/0' not in text

    # Documentation-only source address and private lab destination stay fixed.
    assert 'SRC = &quot;' not in text  # code is literal/preformatted, not HTML-escaped source assignment
    assert 'SRC = "198.51.100.77"' in text
    assert 'DST = "10.200.0.2"' in text

    # Guide explicitly prevents users from treating a local pass as provider proof.
    assert 'نتیجه Provider همچنان UNKNOWN است' in text
    assert 'دیتاسنتر اجازه خروج چنین ترافیکی به اینترنت را می‌دهد' in text


def test_managed_installer_deploys_spoof_lab_guide():
    installer = INSTALLER.read_text(encoding='utf-8')
    assert 'spoof-lab-guide.html' in installer
    assert 'fetch "panel/static/$f" "$STATIC/$f"' in installer
