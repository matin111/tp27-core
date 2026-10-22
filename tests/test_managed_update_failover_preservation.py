from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install-panel-managed.sh"


def installer_source() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_fresh_failover_config_still_defaults_disabled():
    text = installer_source()
    assert 'export TP27_FAILOVER_CONFIG_CREATED=0' in text
    assert '"enabled": false' in text
    assert 'TP27_FAILOVER_CONFIG_CREATED=1' in text
    assert 'New Failover V2 config must default to disabled' in text


def test_existing_enabled_failover_is_preserved_not_rejected():
    text = installer_source()
    assert 'Failover V2 must default to disabled' not in text
    assert "if not isinstance(failover_enabled, bool):" in text
    assert "created=os.environ.get('TP27_FAILOVER_CONFIG_CREATED') == '1'" in text
    assert "origin='created' if created else 'preserved'" in text


def test_enabled_failover_restart_is_deferred_during_update():
    text = installer_source()
    assert 'if [[ "$FAILOVER_ENABLED" == "false" ]]; then' in text
    assert 'FAILOVER_RESTART_STATE=performed-disabled' in text
    assert 'FAILOVER_RESTART_STATE=deferred-enabled' in text
    assert 'FAILOVER_V2_RESTART=$FAILOVER_RESTART_STATE' in text
