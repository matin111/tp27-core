from pathlib import Path


def test_v9_refresh_skips_unchanged_visual_dom():
    js = Path("panel/static/ui-stability.js").read_text(encoding="utf-8")

    assert "function tunnelVisualSignature(status)" in js
    assert "function agentVisualSignature(list)" in js
    assert "dataset.tp27VisualSignature" in js
    assert "list.dataset.tp27VisualSignature===signature&&hasExpectedCards" in js
    assert "list.dataset.tp27Refresh='silent'" in js


def test_v9_refresh_ignores_volatile_heartbeat_fields():
    js = Path("panel/static/ui-stability.js").read_text(encoding="utf-8")

    agent_block = js.split("function agentVisualSignature(list){", 1)[1].split("function agentOptionsSignature", 1)[0]
    assert "last_seen" not in agent_block
    assert "agent_id" in agent_block
    assert "online" in agent_block
    assert "version" in agent_block
    assert "tunnel_count" in agent_block


def test_v9_refresh_preserves_last_good_cards_on_transient_error():
    js = Path("panel/static/ui-stability.js").read_text(encoding="utf-8")

    assert "if(list.querySelector('.tunnel-card'))return previous;" in js
    assert "if(list.querySelector('.agent-card'))return previous;" in js
    assert "dataset.tp27RefreshError" in js


def test_v9_installs_silent_data_hooks_before_refresh_wrapper():
    js = Path("panel/static/ui-stability.js").read_text(encoding="utf-8")

    assert "function installSilentV9DataHooks()" in js
    assert "window.loadStatus=silentV9LoadStatus" in js
    assert "window.loadAgents=silentV9LoadAgents" in js
    assert "installSilentV9DataHooks();\n  installLoadStatusHook();\n  installRefreshHook();" in js
