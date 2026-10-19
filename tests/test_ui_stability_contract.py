from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ui_stability_script_contract():
    js = (ROOT / 'panel/static/ui-stability.js').read_text(encoding='utf-8')
    assert 'tp27-ui-stabilizing' in js
    assert 'freezeDashboard' in js
    assert 'normalizePortBadges' in js
    assert 'refreshPromise' in js
    assert "cisco:10" in js and "openvpn:20" in js and "wireguard:30" in js


def test_index_loads_ui_stability_last():
    html = (ROOT / 'panel/static/index.html').read_text(encoding='utf-8')
    assert '/static/ui-stability.js' in html
    assert html.rfind('/static/ui-stability.js') > html.rfind('/static/self-update.js')
