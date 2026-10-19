from pathlib import Path


def test_refresh_shadow_copies_live_grid_layout():
    js = Path('panel/static/ui-stability.js').read_text(encoding='utf-8')
    assert 'function copyLayoutSnapshot(live,shadow)' in js
    assert "'grid-template-columns'" in js
    assert "shadow.style.setProperty('width',width+'px','important')" in js
    assert "shadow.dataset.tp27ShadowFor=id" in js


def test_refresh_shadow_does_not_force_auto_width():
    js = Path('panel/static/ui-stability.js').read_text(encoding='utf-8')
    assert '.tp27-refresh-shadow{position:absolute!important' in js
    assert 'width:auto' not in js
