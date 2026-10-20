from pathlib import Path


def test_v9_modals_are_centered_not_drawers():
    html = Path('panel/static/index.html').read_text(encoding='utf-8')
    assert 'id="v9-centered-modal-contract"' in html
    assert 'place-items:center!important' in html
    assert 'height:auto!important' in html
    assert 'margin:auto!important' in html
    assert 'border-radius:22px!important' in html
    assert 'max-height:min(88vh,900px)!important' in html


def test_mobile_modals_stay_centered_cards():
    html = Path('panel/static/index.html').read_text(encoding='utf-8')
    block = html.split('id="v9-centered-modal-contract"', 1)[1].split('</style>', 1)[0]
    assert 'max-height:90svh!important' in block
    assert 'border-radius:18px!important' in block
    assert 'height:100vh!important' not in block
    assert 'border-radius:0!important' not in block
