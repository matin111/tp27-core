from pathlib import Path


UI_STABILITY = Path('panel/static/ui-stability.js')


def test_v9_owns_one_server_and_tunnel_card_layout():
    js = UI_STABILITY.read_text(encoding='utf-8')
    assert 'body[data-v9-view] #agentList' in js
    assert 'body[data-v9-view] #exitList' in js
    assert 'grid-template-columns:minmax(0,1fr) auto!important' in js
    assert '.tp27-premium .tunnel-card{' not in js


def test_v9_does_not_render_refresh_shadow_as_second_card_model():
    js = UI_STABILITY.read_text(encoding='utf-8')
    assert 'function freezeDashboard()' in js
    assert 'if(isV9())return ()=>{};' in js
    assert 'visible second card model during refresh' in js


def test_v9_restores_panel_update_and_github_navigation():
    js = UI_STABILITY.read_text(encoding='utf-8')
    assert "update.id='v9PanelUpdateBtn'" in js
    assert 'بروزرسانی پنل' in js
    assert 'window.tp27OpenPanelUpdate' in js
    assert "github.id='v9GitHubLink'" in js
    assert 'https://github.com/matin111/tp27-core' in js
