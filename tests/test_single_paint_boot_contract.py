from pathlib import Path


def test_runtime_locks_first_paint_until_premium_ready():
    js = Path('panel/static/runtime-refresh-fix.js').read_text(encoding='utf-8')
    assert "root.classList.add('tp27-first-paint-lock')" in js
    assert 'firstPaintReady()' in js
    assert "document.getElementById('premiumCommand')" in js
    assert "actions.querySelector('.bm-btn')" in js
    assert "actions.querySelector('.premium-stability-btn')" in js
    assert "card.querySelector('.srv-speed-wrap')" in js
    assert "releaseFirstPaint('ready')" in js


def test_first_paint_has_bounded_failsafe():
    js = Path('panel/static/runtime-refresh-fix.js').read_text(encoding='utf-8')
    assert 'FIRST_PAINT_MAX_MS=8000' in js
    assert "releaseFirstPaint('failsafe')" in js
