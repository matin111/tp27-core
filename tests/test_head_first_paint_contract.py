from pathlib import Path


def test_head_boot_gate_is_installed_before_dashboard_paint():
    installer = Path('install-panel-managed.sh').read_text(encoding='utf-8')
    assert 'first-paint.css' in installer
    assert 'href="/static/first-paint.css"' in installer


def test_runtime_releases_head_boot_gate_only_when_ready():
    js = Path('panel/static/runtime-refresh-fix.js').read_text(encoding='utf-8')
    assert "root.classList.remove('tp27-first-paint-ready')" in js
    assert "root.classList.add('tp27-first-paint-ready')" in js
    assert "releaseFirstPaint('ready')" in js


def test_boot_gate_has_css_only_escape_hatch():
    css = Path('panel/static/first-paint.css').read_text(encoding='utf-8')
    assert 'html:not(.tp27-first-paint-ready) body' in css
    assert '10s' in css
