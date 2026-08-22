from __future__ import annotations

import pandas as pd
import pytest

from src.backtest.costs import CostAssumptions
from src.backtest.execution import ExecutionAssumptions
from src.backtest.intraday import RiskLimits, run_intraday_backtest
from src.strategies.library import StrategyContext, generate_signals
from src.strategies.types import StrategyFamily, StrategySpec


def _bars(
    symbols: tuple[str, ...] = ("AAA",),
    *,
    opens: tuple[float, ...] = (100, 100, 110, 110),
    closes: tuple[float, ...] = (100, 110, 110, 110),
) -> pd.DataFrame:
    timestamps = pd.date_range("2026-08-21 10:00", periods=len(opens), freq="min", tz="UTC")
    rows: list[dict[str, object]] = []
    for timestamp, opening, close in zip(timestamps, opens, closes, strict=True):
        for symbol in symbols:
            rows.append(
                {
                    "symbol": symbol,
                    "open_timestamp": timestamp,
                    "close_timestamp": timestamp + pd.Timedelta(minutes=1),
                    "available_at": timestamp + pd.Timedelta(minutes=1),
                    "finalized": True,
                    "open": float(opening),
                    "high": float(max(opening, close)),
                    "low": float(min(opening, close)),
                    "close": float(close),
                    "volume": 10_000.0,
                    "halted": False,
                }
            )
    return pd.DataFrame(rows)


def _signal(
    strategy_id: str,
    symbol: str,
    decision: str,
    signal: int,
    strength: float = 1.0,
) -> dict[str, object]:
    timestamp = pd.Timestamp(decision, tz="UTC")
    return {
        "strategy_id": strategy_id,
        "symbol": symbol,
        "decision_timestamp": timestamp,
        "data_through": timestamp,
        "signal": signal,
        "strength": strength,
    }


def test_portfolio_enters_next_bar_and_produces_literal_equity_and_trade_ledgers() -> None:
    signals = pd.DataFrame(
        [
            _signal("trend", "AAA", "2026-08-21 10:01", 1),
            _signal("trend", "AAA", "2026-08-21 10:02", 0),
        ]
    )
    risk = RiskLimits(
        initial_cash=1_000,
        maximum_gross_exposure=0.5,
        maximum_net_exposure=0.5,
        maximum_asset_exposure=0.5,
        maximum_strategy_exposure=0.5,
    )

    result = run_intraday_backtest(_bars(), signals, ExecutionAssumptions(), risk)

    assert result.trade_ledger[["side", "quantity", "price"]].to_dict("records") == [
        {"side": "buy", "quantity": 5.0, "price": 100.0},
        {"side": "sell", "quantity": 5.0, "price": 110.0},
    ]
    assert result.trade_ledger["execution_timestamp"].tolist() == [
        pd.Timestamp("2026-08-21 10:01", tz="UTC"),
        pd.Timestamp("2026-08-21 10:02", tz="UTC"),
    ]
    assert result.equity_curve.iloc[-1]["cash"] == 1_050
    assert result.equity_curve.iloc[-1]["equity"] == 1_050
    assert result.metrics.cumulative_return == pytest.approx(0.05)

    repeated = run_intraday_backtest(_bars(), signals, ExecutionAssumptions(), risk)
    pd.testing.assert_frame_equal(result.trade_ledger, repeated.trade_ledger)
    pd.testing.assert_frame_equal(result.equity_curve, repeated.equity_curve)


def test_cash_and_global_asset_strategy_exposure_caps_are_enforced() -> None:
    signals = pd.DataFrame(
        [
            _signal("alpha", "AAA", "2026-08-21 10:01", 1),
            _signal("beta", "BBB", "2026-08-21 10:01", 1),
        ]
    )
    risk = RiskLimits(
        initial_cash=1_000,
        maximum_gross_exposure=0.6,
        maximum_net_exposure=0.6,
        maximum_asset_exposure=0.4,
        maximum_strategy_exposure=0.4,
    )

    result = run_intraday_backtest(
        _bars(("AAA", "BBB"), opens=(100, 100), closes=(100, 100)), signals, ExecutionAssumptions(), risk
    )

    closing = result.equity_curve.iloc[-1]
    assert closing["cash"] == 400
    assert closing["gross_exposure"] == pytest.approx(0.6)
    assert closing["net_exposure"] == pytest.approx(0.6)
    assert result.trade_ledger[["symbol", "quantity"]].to_dict("records") == [
        {"symbol": "AAA", "quantity": 3.0},
        {"symbol": "BBB", "quantity": 3.0},
    ]


def test_opposing_strategies_net_before_orders_reach_the_market() -> None:
    signals = pd.DataFrame(
        [
            _signal("trend", "AAA", "2026-08-21 10:01", 1),
            _signal("reversion", "AAA", "2026-08-21 10:01", -1),
        ]
    )

    result = run_intraday_backtest(
        _bars(opens=(100, 100), closes=(100, 100)),
        signals,
        ExecutionAssumptions(),
        RiskLimits(initial_cash=1_000),
    )

    assert result.trade_ledger.empty
    assert result.equity_curve.iloc[-1]["equity"] == 1_000
    assert result.equity_curve.iloc[-1]["gross_exposure"] == 0


def test_volatility_target_at_each_open_uses_only_returns_from_prior_closes() -> None:
    bars = _bars(opens=(100, 100, 110, 99, 99), closes=(100, 110, 99, 99, 99))
    signals = pd.DataFrame([_signal("trend", "AAA", "2026-08-21 10:01", 1)])
    risk = RiskLimits(
        initial_cash=1_000,
        target_volatility=0.1,
        volatility_lookback=2,
        minimum_volatility_observations=2,
        periods_per_year=1,
    )

    result = run_intraday_backtest(bars, signals, ExecutionAssumptions(lot_size=0.1), risk)

    row = result.equity_curve.loc[result.equity_curve["timestamp"] == pd.Timestamp("2026-08-21 10:04", tz="UTC")].iloc[
        0
    ]
    assert row["volatility_estimate"] == pytest.approx(0.14142135623730953)
    assert row["volatility_scale"] == pytest.approx(0.7071067811865475)
    sell = result.trade_ledger.loc[
        result.trade_ledger["execution_timestamp"] == pd.Timestamp("2026-08-21 10:03", tz="UTC")
    ].iloc[0]
    assert sell["side"] == "sell"
    assert sell["quantity"] == pytest.approx(2.9)

    changed = bars.copy()
    changed.loc[changed["open_timestamp"] == pd.Timestamp("2026-08-21 10:03", tz="UTC"), "close"] = 200
    changed.loc[changed["open_timestamp"] == pd.Timestamp("2026-08-21 10:03", tz="UTC"), "high"] = 200
    changed_result = run_intraday_backtest(changed, signals, ExecutionAssumptions(lot_size=0.1), risk)
    changed_row = changed_result.equity_curve.loc[
        changed_result.equity_curve["timestamp"] == pd.Timestamp("2026-08-21 10:04", tz="UTC")
    ].iloc[0]
    assert changed_row["volatility_estimate"] == row["volatility_estimate"]
    pd.testing.assert_frame_equal(
        result.trade_ledger[result.trade_ledger["execution_timestamp"] <= pd.Timestamp("2026-08-21 10:03", tz="UTC")],
        changed_result.trade_ledger[
            changed_result.trade_ledger["execution_timestamp"] <= pd.Timestamp("2026-08-21 10:03", tz="UTC")
        ],
    )


def test_signal_data_timestamp_after_decision_is_rejected() -> None:
    signal = _signal("trend", "AAA", "2026-08-21 10:01", 1)
    signal["data_through"] = pd.Timestamp("2026-08-21 10:02", tz="UTC")

    with pytest.raises(ValueError, match="decision cannot precede"):
        run_intraday_backtest(
            _bars(opens=(100, 100), closes=(100, 100)),
            pd.DataFrame([signal]),
            ExecutionAssumptions(),
            RiskLimits(),
        )


def test_unavailable_borrow_does_not_block_a_long_position_exit() -> None:
    signals = pd.DataFrame(
        [
            _signal("trend", "AAA", "2026-08-21 10:01", 1),
            _signal("trend", "AAA", "2026-08-21 10:02", 0),
        ]
    )

    result = run_intraday_backtest(
        _bars(),
        signals,
        ExecutionAssumptions(short_borrow_available=False),
        RiskLimits(initial_cash=1_000, maximum_gross_exposure=0.5, maximum_strategy_exposure=0.5),
    )

    assert result.trade_ledger["side"].tolist() == ["buy", "sell"]
    assert result.trade_ledger.iloc[-1]["quantity"] == 5


@pytest.mark.parametrize(
    ("short_borrow_available", "short_bar_volume", "expected_quantity"),
    [(False, 10_000.0, 0.0), (True, 2.0, 2.0)],
    ids=["borrow-rejection", "liquidity-rescale"],
)
def test_dependent_market_neutral_legs_are_atomically_rejected_or_rescaled(
    short_borrow_available: bool,
    short_bar_volume: float,
    expected_quantity: float,
) -> None:
    bars = _bars(("AAA", "BBB"), opens=(100, 100), closes=(100, 100))
    actionable = bars["open_timestamp"] == pd.Timestamp("2026-08-21 10:01", tz="UTC")
    bars.loc[actionable & (bars["symbol"] == "BBB"), "volume"] = short_bar_volume
    signals = pd.DataFrame(
        [
            _signal("pairs", "AAA", "2026-08-21 10:01", 1),
            _signal("pairs", "BBB", "2026-08-21 10:01", -1),
        ]
    )
    risk = RiskLimits(
        initial_cash=1_000,
        maximum_gross_exposure=1.0,
        maximum_net_exposure=0.1,
        maximum_asset_exposure=0.6,
        maximum_strategy_exposure=0.6,
    )

    result = run_intraday_backtest(
        bars,
        signals,
        ExecutionAssumptions(short_borrow_available=short_borrow_available),
        risk,
    )

    if expected_quantity == 0:
        assert result.trade_ledger.empty
        assert "dependent_leg_not_executable" in result.rejection_ledger["reason"].tolist()
    else:
        quantities = result.trade_ledger.set_index("symbol")["quantity"].to_dict()
        assert quantities == {"AAA": expected_quantity, "BBB": expected_quantity}
    closing = result.equity_curve.iloc[-1]
    assert abs(closing["net_exposure"]) <= 0.1
    assert closing["gross_exposure"] <= 0.6


def test_carry_accrues_on_position_held_during_bar_before_session_close_fill() -> None:
    bars = _bars(opens=(100, 100), closes=(100, 100))
    bars["session_close_timestamp"] = pd.Series(pd.NaT, index=bars.index, dtype="datetime64[ns, UTC]")
    last = bars["open_timestamp"] == pd.Timestamp("2026-08-21 10:01", tz="UTC")
    bars.loc[last, "session_close_timestamp"] = bars.loc[last, "close_timestamp"]
    assumptions = ExecutionAssumptions(
        costs=CostAssumptions(funding_bps_per_period=10),
        flatten_at_session_end=True,
        session_close=lambda row: row["session_close_timestamp"],
    )
    signals = pd.DataFrame([_signal("trend", "AAA", "2026-08-21 10:01", 1)])
    risk = RiskLimits(
        initial_cash=1_000,
        maximum_gross_exposure=0.5,
        maximum_net_exposure=0.5,
        maximum_asset_exposure=0.5,
        maximum_strategy_exposure=0.5,
    )

    result = run_intraday_backtest(bars, signals, assumptions, risk)

    assert result.trade_ledger["order_type"].tolist() == ["market", "session_flatten"]
    assert result.equity_curve.iloc[-1]["costs"] == pytest.approx(0.5)
    assert result.equity_curve.iloc[-1]["equity"] == pytest.approx(999.5)


def test_task3_strategy_signal_frame_is_directly_consumable_with_explicit_identity() -> None:
    bars = _bars(opens=(2, 3, 5, 5), closes=(2, 3, 5, 5))
    bars.loc[3, ["open_timestamp", "close_timestamp", "available_at"]] += pd.Timedelta(minutes=1)
    bars["provider"] = "test-provider"
    bars["feed"] = "test-feed"
    bars["interval"] = "1m"
    bars["revision"] = 1
    spec = StrategySpec(
        strategy_id="donchian_breakout",
        family=StrategyFamily.TREND,
        version="test",
        intervals=("1m",),
        warmup_bars=1,
        parameters={"lookback": 2},
    )
    strategy_signals = generate_signals(spec, bars, StrategyContext())

    result = run_intraday_backtest(
        bars,
        strategy_signals,
        ExecutionAssumptions(),
        RiskLimits(initial_cash=1_000),
        strategy_id=spec.strategy_id,
        symbol="AAA",
    )

    assert result.trade_ledger.iloc[0]["strategy_id"] == "donchian_breakout"
    assert result.trade_ledger.iloc[0]["symbol"] == "AAA"
    assert result.trade_ledger.iloc[0]["execution_timestamp"] > strategy_signals.iloc[2]["decision_timestamp"]


def test_strategy_cap_counts_held_symbols_without_a_current_bar_or_delta() -> None:
    bars = _bars(("AAA", "BBB"), opens=(100, 100, 100), closes=(100, 100, 100))
    keep = (
        (bars["symbol"] == "AAA")
        & bars["open_timestamp"].isin(
            pd.to_datetime(
                [
                    "2026-08-21 10:00Z",
                    "2026-08-21 10:01Z",
                ]
            )
        )
    ) | ((bars["symbol"] == "BBB") & (bars["open_timestamp"] == pd.Timestamp("2026-08-21 10:02Z")))
    bars = bars.loc[keep].copy()
    signals = pd.DataFrame(
        [
            _signal("asynchronous", "AAA", "2026-08-21 10:01", 1, strength=0.2),
            _signal("asynchronous", "BBB", "2026-08-21 10:02", 1, strength=0.2),
        ]
    )
    risk = RiskLimits(
        initial_cash=1_000,
        maximum_gross_exposure=1,
        maximum_net_exposure=1,
        maximum_asset_exposure=1,
        maximum_strategy_exposure=0.2,
    )

    result = run_intraday_backtest(bars, signals, ExecutionAssumptions(), risk)

    assert result.trade_ledger[["symbol", "quantity"]].to_dict("records") == [{"symbol": "AAA", "quantity": 2.0}]
    assert result.equity_curve.iloc[-1]["gross_exposure"] == pytest.approx(0.2)
    assert "projected_risk_limit" in result.rejection_ledger["reason"].tolist()


def test_asymmetric_session_close_batch_is_rejected_before_breaking_net_cap() -> None:
    bars = _bars(("AAA", "BBB"), opens=(100, 100, 100), closes=(100, 100, 100))
    bars["session_close_timestamp"] = pd.Series(pd.NaT, index=bars.index, dtype="datetime64[ns, UTC]")
    closing = bars["open_timestamp"] == pd.Timestamp("2026-08-21 10:02Z")
    bars.loc[closing, "session_close_timestamp"] = bars.loc[closing, "close_timestamp"]
    bars.loc[closing & (bars["symbol"] == "BBB"), "halted"] = True
    signals = pd.DataFrame(
        [
            _signal("pairs", "AAA", "2026-08-21 10:01", 1, strength=0.5),
            _signal("pairs", "BBB", "2026-08-21 10:01", -1, strength=0.5),
        ]
    )
    assumptions = ExecutionAssumptions(
        flatten_at_session_end=True,
        session_close=lambda row: row["session_close_timestamp"],
    )
    risk = RiskLimits(
        initial_cash=1_000,
        maximum_gross_exposure=1,
        maximum_net_exposure=0.1,
        maximum_asset_exposure=0.5,
        maximum_strategy_exposure=1,
    )

    result = run_intraday_backtest(bars, signals, assumptions, risk)

    assert result.trade_ledger["order_type"].tolist() == ["market", "market"]
    assert result.equity_curve.iloc[-1]["net_exposure"] == 0
    assert set(result.rejection_ledger["reason"]) >= {
        "session_close_bar_not_actionable",
        "session_close_batch_projected_risk_limit",
    }
