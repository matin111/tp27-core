from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_safe_switch_ui_uses_dedicated_switch_endpoint():
    text = (ROOT / 'panel/static/safe-switch-ui.js').read_text(encoding='utf-8')
    assert "/switch" in text
    assert "method:'DELETE'" not in text
    assert 'مسیر فعلی تا سالم شدن مسیر جدید حذف نمی‌شود' in text


def test_safe_switch_backend_is_blue_green():
    text = (ROOT / 'panel/safe_switch.py').read_text(encoding='utf-8')
    build_at = text.index('candidate = await _build_candidate')
    cutover_at = text.index('reconcile_ports()', build_at)
    cleanup_at = text.index('_cleanup_local(cid)', cutover_at)
    assert build_at < cutover_at < cleanup_at
    assert 'مسیر قبلی حفظ/Restore شد' in text
    assert "wrapper['channels'] = [dict(candidate)]" in text


def test_managed_installer_deploys_safe_switch():
    text = (ROOT / 'install-panel-managed.sh').read_text(encoding='utf-8')
    assert 'safe_switch.py safe_switch_router.py' in text
    assert 'safe-switch-ui.js' in text
    assert 'TP27_SAFE_SWITCH_ROUTER_V1' in text
