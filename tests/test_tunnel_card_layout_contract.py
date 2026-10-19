from pathlib import Path


def test_tunnel_card_layout_contract():
    src = Path("panel/static/ui-stability.js").read_text(encoding="utf-8")
    assert "TP27 TUNNEL CARD 2027" in src
    assert "grid-template-columns:repeat(6,minmax(0,1fr))" in src
    assert ".tp27-premium .tunnel-card .bm-strip" in src
    assert "min-width:0!important" in src
    assert "border-top:1px solid var(--p-border)" in src
