from pathlib import Path


def test_tunnel_card_layout_contract():
    src = Path("panel/static/ui-stability.js").read_text(encoding="utf-8")

    # V9 has one authoritative geometry for both inventory types.
    assert "body[data-v9-view] #agentList" in src
    assert "body[data-v9-view] #exitList" in src
    assert "grid-template-columns:minmax(0,1fr) auto!important" in src
    assert "display:flex!important" in src
    assert "min-width:0!important" in src

    # The legacy compact six-column runtime tile was the source of the
    # second visual model and must not return.
    assert "grid-template-columns:repeat(6,minmax(0,1fr))" not in src
    assert ".tp27-premium .tunnel-card{" not in src

    # V9 refreshes the live cards instead of showing a cloned alternate UI.
    assert "if(isV9())return ()=>{};" in src
