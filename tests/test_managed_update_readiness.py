from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'install-panel-managed.sh'


def test_managed_installer_uses_bounded_panel_readiness_gate():
    text = INSTALLER.read_text(encoding='utf-8')

    assert 'PRE_RESTART_APP_IMPORT=OK' in text
    assert 'PANEL_READY=0' in text
    assert 'for attempt in $(seq 1 30)' in text
    assert 'PANEL_READY=OK attempt=$attempt' in text
    assert 'journalctl -u tp27-panel.service -n 120 --no-pager' in text

    # Regression: a single curl immediately after a fixed sleep caused a false
    # rollback while systemd had already reported the panel process active.
    old_gate = (
        'sleep 2\n'
        'systemctl is-active --quiet tp27-panel.service || '
        '{ systemctl status tp27-panel.service --no-pager -l; exit 3; }\n'
        'curl -kfsS --connect-timeout 2 --max-time 5 '
        'https://127.0.0.1:8787/login >/dev/null\n'
    )
    assert old_gate not in text
