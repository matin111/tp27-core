from pathlib import Path


INDEX = Path('panel/static/index.html')
V9 = Path('panel/static/v9.css')
INSTALLER = Path('install-panel-managed.sh')


def test_v9_is_a_structural_app_shell_not_only_a_theme_patch():
    html = INDEX.read_text(encoding='utf-8')
    for marker in (
        'class="v9-app-shell"',
        'class="v9-sidebar"',
        'class="v9-workspace"',
        'class="v9-topbar"',
        'data-v9-target="overview"',
        'data-v9-target="servers"',
        'data-v9-target="tunnels"',
        'data-v9-target="builder"',
        'data-v9-target="maintenance"',
    ):
        assert marker in html


def test_v9_preserves_runtime_dom_contracts():
    html = INDEX.read_text(encoding='utf-8')
    for element_id in (
        'publicIp', 'agentCount', 'exitCount', 'onlineCount',
        'agentList', 'exitList', 'agentId', 'exitId', 'tunnelPort',
        'portRows', 'job', 'serverWizard', 'agentManage',
        'tunnelManage', 'tunnelDiag', 'managePortRows',
    ):
        assert html.count(f'id="{element_id}"') == 1

    for handler in (
        'refreshAll()', 'openServerWizard()', 'preflight()', 'addExit()',
        'saveManagedPorts()', 'diagnoseManagedTunnel()',
        'repairManagedTunnel()', 'confirmDeleteTunnel()',
    ):
        assert handler in html


def test_v9_keeps_builder_direct_h3_for_premium_enhancement_contract():
    html = INDEX.read_text(encoding='utf-8')
    needle = 'data-section="builder" data-v9-view-panel="builder">\n          <h3>ساخت تونل</h3>'
    assert needle in html


def test_v9_stylesheet_loads_after_existing_design_system():
    html = INDEX.read_text(encoding='utf-8')
    assert '/static/v9.css' in html
    assert html.index('/static/premium.css') < html.index('/static/v9.css')
    css = V9.read_text(encoding='utf-8')
    for marker in (
        '.v9-app-shell', '.v9-sidebar', '.v9-topbar',
        '.v9-overview-grid', '.v9-maintenance-grid',
        'body[data-v9-view="servers"]',
        '#agentList', '#exitList',
        'html[data-theme="light"]',
        '@media(max-width:900px)',
        'prefers-reduced-motion:reduce',
    ):
        assert marker in css


def test_v9_has_no_network_or_service_mutation_logic():
    combined = INDEX.read_text(encoding='utf-8') + '\n' + V9.read_text(encoding='utf-8')
    for forbidden in (
        'systemctl restart tp27-link@',
        'systemctl restart tp27-framed@',
        'ip tunnel add',
        'ip link del',
        'iptables -',
        'nft add',
    ):
        assert forbidden not in combined


def test_managed_installer_deploys_and_smokes_v9_asset():
    script = INSTALLER.read_text(encoding='utf-8')
    assert 'premium.css v9.css premium-ui.js' in script
    assert "'/static/v9.css'," in script


def test_v9_navigation_is_client_side_only_and_persistent():
    html = INDEX.read_text(encoding='utf-8')
    assert "const KEY='tp27_v9_view'" in html
    assert "document.body.dataset.v9View=view" in html
    assert "localStorage.setItem(KEY,view)" in html
    assert "data-v9-goto=\"builder\"" in html
