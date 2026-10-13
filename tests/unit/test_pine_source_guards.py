from pathlib import Path
import re

PINE = Path(__file__).resolve().parents[2] / "docs/pine/nowcaster-causal-reversal-chart-strategy.pine"


def test_causal_reversal_does_not_enter_after_session_or_backplot_confirmation():
    source = PINE.read_text(encoding="utf-8")
    assert "barstate.isconfirmed" in source
    assert "label.new(bar_index, close" in source
    assert "time_close < sessionEnd" in source
    assert 'strategy.close_all(comment = "One-hour or session exit", immediately = true)' in source


def test_causal_reversal_can_fund_one_unlevered_index_unit_at_observed_price():
    """The chart must produce fills before its signal accuracy can be measured."""
    source = PINE.read_text(encoding="utf-8")
    declaration = source.splitlines()[1]
    capital = int(re.search(r"initial_capital\s*=\s*(\d+)", declaration).group(1))
    units = int(re.search(r"default_qty_value\s*=\s*(\d+)", declaration).group(1))

    # FXCM GER30 was about 25,300 EUR on 2026-10-13. Keep a cash buffer;
    # TradingView Pine v6 defaults to 100% margin for both directions.
    assert capital >= 25_300 * units * 1.5


def test_causal_reversal_candidate_uses_only_preexisting_swing_for_confirmation():
    source = PINE.read_text(encoding="utf-8")
    assert "leg == 1 and swingHigh - close >= threshold" in source
    assert "leg == -1 and close - swingLow >= threshold" in source
    assert "shortConfirmed ? swingHigh : longConfirmed ? swingLow : na" in source
    assert "swingHigh := math.max(swingHigh, high)" in source
    assert "swingLow := math.min(swingLow, low)" in source
