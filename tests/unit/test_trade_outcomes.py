from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from src.models.trade_outcomes import BarrierPolicy, label_trade_outcomes


def _bars() -> pd.DataFrame:
    opens = pd.date_range("2026-08-26 14:00", periods=4, freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "provider": "test",
            "feed": "sip",
            "symbol": "XYZ",
            "open_timestamp": opens,
            "close_timestamp": opens + pd.Timedelta(minutes=5),
            "available_at": opens + pd.Timedelta(minutes=5, seconds=1),
            "open": [100.0, 100.0, 100.2, 100.4],
            "high": [100.5, 101.2, 100.8, 101.0],
            "low": [99.5, 98.8, 99.8, 100.0],
            "close": [100.0, 100.1, 100.4, 100.8],
            "atr": [1.0, 1.0, 1.0, 1.0],
        }
    )


def test_same_bar_stop_and_target_uses_adverse_ordering_and_mature_timestamp() -> None:
    outcomes = label_trade_outcomes(
        _bars(),
        BarrierPolicy(target_r=1, stop_r=1, maximum_bars=2, round_trip_cost_bps=10),
        directions=("long",),
    )

    first = outcomes[0]
    assert first.exit_reason == "ambiguous_stop_first"
    assert first.target_before_stop is False
    assert first.gross_return == pytest.approx(-0.01)
    assert first.net_return == pytest.approx(-0.011)
    assert first.maximum_favourable_excursion_r == pytest.approx(1.2)
    assert first.maximum_adverse_excursion_r == pytest.approx(1.2)
    assert first.outcome_available_at == pd.Timestamp("2026-08-26 14:10:01", tz="UTC").to_pydatetime()


def test_short_outcome_uses_directional_excursions_and_expiry_costs() -> None:
    bars = _bars()
    bars.loc[1:, "high"] = [100.4, 100.5, 100.9]
    bars.loc[1:, "low"] = [99.5, 99.4, 99.3]
    policy = BarrierPolicy(target_r=2, stop_r=2, maximum_bars=2, round_trip_cost_bps=20)

    outcome = label_trade_outcomes(bars, policy, directions=("short",))[0]

    assert outcome.exit_reason == "expired"
    assert outcome.maximum_favourable_excursion_r == pytest.approx(0.6)
    assert outcome.maximum_adverse_excursion_r == pytest.approx(0.5)
    assert outcome.net_return == pytest.approx(outcome.gross_return - 0.002)


def test_completed_outcomes_are_prefix_invariant_and_reject_noncausal_rows() -> None:
    bars = _bars()
    policy = BarrierPolicy(target_r=1, stop_r=1, maximum_bars=2)
    prefix = label_trade_outcomes(bars.iloc[:3], policy, directions=("long",))[0]
    appended = label_trade_outcomes(bars, policy, directions=("long",))[0]
    assert prefix == appended

    malformed = bars.copy()
    malformed.loc[1, "available_at"] = malformed.loc[1, "open_timestamp"]
    with pytest.raises(ValueError, match="available"):
        label_trade_outcomes(malformed, policy)

    with pytest.raises(ValueError, match="positive"):
        replace(policy, target_r=0)
