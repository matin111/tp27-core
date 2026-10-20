from pathlib import Path


INDEX = Path('panel/static/index.html')
PREMIUM = Path('panel/static/premium.css')
PREMIUM_UI = Path('panel/static/premium-ui.js')


def test_dashboard_declares_first_paint_and_color_scheme_in_source():
    html = INDEX.read_text(encoding='utf-8')
    assert '<meta name="color-scheme" content="dark light">' in html
    assert 'href="/static/first-paint.css"' in html
    assert html.index('/static/first-paint.css') < html.index('/static/style.css')
    assert html.index('/static/style.css') < html.index('/static/premium.css')


def test_premium_css_is_not_a_second_visibility_boot_gate():
    css = PREMIUM.read_text(encoding='utf-8')
    assert 'body:not(.tp27-premium)' not in css
    assert 'visibility:hidden!important' not in css
    assert 'first-paint.css is the only boot/visibility gate' in css


def test_dashboard_keeps_existing_behavior_contract_ids_and_scripts():
    html = INDEX.read_text(encoding='utf-8')
    for element_id in (
        'publicIp',
        'agentCount',
        'exitCount',
        'onlineCount',
        'agentList',
        'exitList',
        'agentId',
        'exitId',
        'portRows',
        'job',
        'serverWizard',
        'agentManage',
        'tunnelManage',
        'tunnelDiag',
    ):
        assert f'id="{element_id}"' in html

    for script in (
        '/static/runtime-refresh-fix.js',
        '/static/app.js',
        '/static/fast-ui.js',
        '/static/agent-manage.js',
        '/static/premium-ui.js',
        '/static/benchmark-ui.js',
        '/static/server-speed-ui.js',
        '/static/safe-switch-ui.js',
        '/static/self-update.js',
        '/static/ui-stability.js',
    ):
        assert script in html


def test_builder_keeps_direct_heading_for_existing_enhancer():
    html = INDEX.read_text(encoding='utf-8')
    assert (
        '<section class="card builder dashboard-section" data-section="builder">\n'
        '      <h3>ساخت تونل</h3>'
    ) in html


def test_builder_copy_matches_auto_transport_architecture():
    html = INDEX.read_text(encoding='utf-8')
    js = PREMIUM_UI.read_text(encoding='utf-8')
    assert 'AUTO Smart Builder' in js
    assert 'AUTO · Smart Transport Selection' in html
    assert 'Raw GRE' in html
    assert 'GRE/UDP FOU' in html
    assert 'Framed TLS/TCP' in html


def test_design_system_supports_light_dark_rtl_and_accessibility():
    css = PREMIUM.read_text(encoding='utf-8')
    assert 'html[data-theme="light"]' in css
    assert '--p-accent:#8b5cf6' in css
    assert 'margin-inline-start' in css
    assert ':focus-visible' in css
    assert 'prefers-reduced-motion:reduce' in css


def test_ui_upgrade_does_not_introduce_network_or_api_logic():
    html = INDEX.read_text(encoding='utf-8')
    css = PREMIUM.read_text(encoding='utf-8')
    combined = html + '\n' + css
    for forbidden in (
        'systemctl restart tp27-link@',
        'systemctl restart tp27-framed@',
        'iptables ',
        'nft ',
        'ip tunnel add',
        'ip link del',
    ):
        assert forbidden not in combined
