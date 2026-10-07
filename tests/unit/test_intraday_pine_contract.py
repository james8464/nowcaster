from pathlib import Path


def test_visual_companion_counter_recomputes_on_realtime_updates():
    source = Path("docs/pine/nowcaster-intraday-companion.pine").read_text(encoding="utf-8")
    assert "barstate.isnew" not in source
    assert "barstate.isconfirmed" in source
    assert "strategy.entry" not in source
    assert "alert(" not in source
