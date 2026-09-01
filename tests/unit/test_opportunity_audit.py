from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from src.backtest.opportunities import audit_strategy_opportunities, summarize_opportunities
from src.models.trade_outcomes import BarrierPolicy


def _bars(*, gap_after: int | None = None) -> pd.DataFrame:
    opens = list(pd.date_range("2026-01-01T00:00:00Z", periods=8, freq="5min"))
    if gap_after is not None:
        for index in range(gap_after + 1, len(opens)):
            opens[index] += pd.Timedelta(minutes=5)
    frame = pd.DataFrame(
        {
            "provider": "archive",
            "feed": "spot",
            "symbol": "BTCUSDT",
            "interval": "5m",
            "open_timestamp": opens,
            "close_timestamp": [value + pd.Timedelta(minutes=5) for value in opens],
            "available_at": [value + pd.Timedelta(minutes=5) for value in opens],
            "revision": 1,
            "finalized": True,
            "open": [100.0] * 8,
            "high": [100.2] * 8,
            "low": [99.8] * 8,
            "close": [100.0] * 8,
            "volume": [1_000.0] * 8,
            "atr": [1.0] * 8,
        }
    )
    return frame


def _signals(bars: pd.DataFrame, values: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "decision_timestamp": bars["close_timestamp"].copy(),
            "data_through": bars["close_timestamp"].copy(),
            "signal": values,
            "strength": [1.0 if value else 0.0 for value in values],
            "reason": ["fixture"] * len(values),
        }
    )


def test_audit_enters_next_bar_charges_costs_and_uses_stop_first_for_ambiguous_bar() -> None:
    bars = _bars()
    bars.loc[1, ["high", "low"]] = [101.2, 98.8]
    result = audit_strategy_opportunities(
        bars,
        _signals(bars, [1, 0, 0, 0, 0, 0, 0, 0]),
        BarrierPolicy(target_r=1, stop_r=1, maximum_bars=3, round_trip_cost_bps=34),
        strategy_id="fixture",
        family="trend",
    )

    assert len(result.outcomes) == 1
    row = result.outcomes.iloc[0]
    assert row["entry_timestamp"] == pd.Timestamp("2026-01-01T00:05:00Z")
    assert row["exit_reason"] == "ambiguous_stop_first"
    assert row["gross_return"] == pytest.approx(-0.01)
    assert row["net_return"] == pytest.approx(-0.0134)
    assert result.diagnostics["signals_considered"] == 1
    assert result.diagnostics["gap_blocked"] == 0


def test_audit_never_enters_or_carries_an_opportunity_across_a_missing_bar() -> None:
    entry_gap = _bars(gap_after=0)
    blocked = audit_strategy_opportunities(
        entry_gap,
        _signals(entry_gap, [1, 0, 0, 0, 0, 0, 0, 0]),
        BarrierPolicy(target_r=1, stop_r=1, maximum_bars=3),
        strategy_id="fixture",
        family="trend",
    )
    assert blocked.outcomes.empty
    assert blocked.diagnostics["gap_blocked"] == 1

    later_gap = _bars(gap_after=1)
    later_gap.loc[1, ["high", "low", "close"]] = [100.4, 99.6, 100.1]
    truncated = audit_strategy_opportunities(
        later_gap,
        _signals(later_gap, [1, 0, 0, 0, 0, 0, 0, 0]),
        BarrierPolicy(target_r=2, stop_r=2, maximum_bars=3),
        strategy_id="fixture",
        family="trend",
    )
    assert truncated.outcomes.empty
    assert truncated.diagnostics["gap_truncated"] == 1


def test_persistent_signals_cannot_create_overlapping_hypothetical_positions() -> None:
    bars = _bars()
    result = audit_strategy_opportunities(
        bars,
        _signals(bars, [1] * len(bars)),
        BarrierPolicy(target_r=2, stop_r=2, maximum_bars=3),
        strategy_id="fixture",
        family="trend",
    )

    assert list(result.outcomes["decision_timestamp"]) == [
        pd.Timestamp("2026-01-01T00:05:00Z"),
        pd.Timestamp("2026-01-01T00:20:00Z"),
    ]
    assert result.diagnostics["overlap_blocked"] == 4
    assert result.diagnostics["right_censored"] == 2


def test_completed_opportunity_results_are_prefix_invariant() -> None:
    bars = _bars()
    signals = _signals(bars, [1, 0, 0, 0, -1, 0, 0, 0])
    policy = BarrierPolicy(target_r=2, stop_r=2, maximum_bars=2, round_trip_cost_bps=10)

    prefix = audit_strategy_opportunities(
        bars.iloc[:4], signals.iloc[:4], policy, strategy_id="fixture", family="trend"
    ).outcomes
    full = audit_strategy_opportunities(bars, signals, policy, strategy_id="fixture", family="trend").outcomes

    pd.testing.assert_frame_equal(prefix.reset_index(drop=True), full.iloc[: len(prefix)].reset_index(drop=True))


def test_expiry_bar_is_scored_before_target_like_the_live_monitor() -> None:
    bars = _bars()
    bars.loc[1, ["high", "low", "close"]] = [100.4, 99.8, 100.2]
    bars.loc[2, ["high", "low", "close"]] = [101.2, 99.8, 100.6]
    result = audit_strategy_opportunities(
        bars,
        _signals(bars, [1, 0, 0, 0, 0, 0, 0, 0]),
        BarrierPolicy(target_r=1, stop_r=2, maximum_bars=2),
        strategy_id="fixture",
        family="trend",
    )

    assert len(result.outcomes) == 1
    assert result.outcomes.iloc[0]["exit_reason"] == "expired"
    assert result.outcomes.iloc[0]["exit_price"] == pytest.approx(100.6)


def test_summary_is_explicitly_retrospective_and_never_live_promotable() -> None:
    bars = _bars()
    bars.loc[1, ["high", "close"]] = [101.2, 101.0]
    result = audit_strategy_opportunities(
        bars,
        _signals(bars, [1, 0, 0, 0, 0, 0, 0, 0]),
        BarrierPolicy(target_r=1, stop_r=1, maximum_bars=3, round_trip_cost_bps=10),
        strategy_id="fixture",
        family="trend",
    )

    summary = summarize_opportunities(result.outcomes, bootstrap_samples=100, seed=7)

    assert summary["evidence_tier"] == "retrospective_archive_only"
    assert summary["eligible_for_live_promotion"] is False
    assert summary["opportunities"] == 1
    assert summary["target_before_stop_rate"] == 1.0
    assert summary["net_win_rate"] == 1.0
    assert summary["mean_net_return"] == pytest.approx(0.009)
    assert summary["cumulative_net_return"] == pytest.approx(0.009)
    assert summary["bootstrap_probability_positive"] is None


def test_audit_rejects_noncausal_or_mismatched_signal_rows() -> None:
    bars = _bars()
    signals = _signals(bars, [1, 0, 0, 0, 0, 0, 0, 0])
    signals.loc[0, "decision_timestamp"] = signals.loc[0, "data_through"] - pd.Timedelta(seconds=1)
    with pytest.raises(ValueError, match="decision|causal"):
        audit_strategy_opportunities(
            bars,
            signals,
            BarrierPolicy(target_r=1, stop_r=1, maximum_bars=3),
            strategy_id="fixture",
            family="trend",
        )

    unmatched = _signals(bars, [1, 0, 0, 0, 0, 0, 0, 0])
    unmatched.loc[0, "data_through"] = datetime(2025, 12, 31, tzinfo=UTC)
    with pytest.raises(ValueError, match="matching finalized bar"):
        audit_strategy_opportunities(
            bars,
            unmatched,
            BarrierPolicy(target_r=1, stop_r=1, maximum_bars=3),
            strategy_id="fixture",
            family="trend",
        )


def test_audit_requires_finalized_explicit_utc_inputs_and_bar_availability() -> None:
    unfinalized = _bars()
    unfinalized.loc[0, "finalized"] = False
    with pytest.raises(ValueError, match="finalized"):
        audit_strategy_opportunities(
            unfinalized,
            _signals(unfinalized, [1, 0, 0, 0, 0, 0, 0, 0]),
            BarrierPolicy(target_r=1, stop_r=1, maximum_bars=3),
            strategy_id="fixture",
            family="trend",
        )

    bars = _bars()
    non_utc = _signals(bars, [1, 0, 0, 0, 0, 0, 0, 0])
    non_utc["decision_timestamp"] = non_utc["decision_timestamp"].dt.tz_convert("Europe/Paris")
    with pytest.raises(ValueError, match="UTC"):
        audit_strategy_opportunities(
            bars,
            non_utc,
            BarrierPolicy(target_r=1, stop_r=1, maximum_bars=3),
            strategy_id="fixture",
            family="trend",
        )

    delayed = _bars()
    delayed.loc[0, "available_at"] = delayed.loc[0, "close_timestamp"] + pd.Timedelta(seconds=1)
    with pytest.raises(ValueError, match="availability"):
        audit_strategy_opportunities(
            delayed,
            _signals(delayed, [1, 0, 0, 0, 0, 0, 0, 0]),
            BarrierPolicy(target_r=1, stop_r=1, maximum_bars=3),
            strategy_id="fixture",
            family="trend",
        )


def test_audit_supplies_a_stable_reason_when_the_optional_column_is_absent() -> None:
    bars = _bars()
    bars.loc[1, ["high", "close"]] = [101.2, 101.0]
    signals = _signals(bars, [1, 0, 0, 0, 0, 0, 0, 0]).drop(columns="reason")

    result = audit_strategy_opportunities(
        bars,
        signals,
        BarrierPolicy(target_r=1, stop_r=1, maximum_bars=3),
        strategy_id="fixture",
        family="trend",
    )

    assert result.outcomes.iloc[0]["signal_reason"] == "configured strategy condition"
