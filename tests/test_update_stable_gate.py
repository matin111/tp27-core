from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_self_update_defaults_to_stable_channel():
    text = (ROOT / 'panel/self_update.py').read_text(encoding='utf-8')
    assert "or 'stable'" in text
    assert "commits/{ref}" in text
    assert "'channel': channel" in text
    assert "commits/main" not in text


def test_post_update_smoke_checks_ui_api_and_data_planes():
    text = (ROOT / 'bin/tp27-panel-self-update').read_text(encoding='utf-8')
    assert 'verify_panel_smoke()' in text
    assert "write_state verifying" in text
    assert "'/api/status'" in text
    assert "'/api/agents'" in text
    assert 'node --check' in text
    assert 'compileall -q' in text
    assert 'verify_data_planes' in text
    assert 'data-planes.before' in text
    assert 'Post-Update Smoke Test' in text


def test_managed_installer_has_bootstrap_smoke_gate():
    text = (ROOT / 'install-panel-managed.sh').read_text(encoding='utf-8')
    assert 'DATA_PLANES_BEFORE=' in text
    assert 'INSTALL_SMOKE=OK' in text
    assert "get('/api/status',auth=True)" in text
    assert "get('/api/agents',auth=True)" in text
    assert 'node --check' in text
    assert 'data plane stopped during panel update' in text
    assert 'DATA_PLANES=verified-untouched' in text


def test_update_ui_exposes_stable_and_verifying_state():
    text = (ROOT / 'panel/static/self-update.js').read_text(encoding='utf-8')
    assert 'updChannel' in text
    assert "verifying:'Smoke Test'" in text
    assert 'نسخه Stable' in text
