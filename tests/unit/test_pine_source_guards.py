from pathlib import Path

PINE = Path(__file__).resolve().parents[2] / "docs/pine/nowcaster-causal-reversal-chart-strategy.pine"


def test_causal_reversal_does_not_enter_after_session_or_backplot_confirmation():
    source = PINE.read_text(encoding="utf-8")
    assert "barstate.isconfirmed" in source
    assert "label.new(bar_index, close" in source
    assert "time_close < sessionEnd" in source
    assert 'strategy.close_all(comment = "One-hour or session exit", immediately = true)' in source
