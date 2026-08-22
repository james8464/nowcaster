from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from src.backtest.costs import CostAssumptions, calculate_carry_cost, calculate_transaction_cost
from src.backtest.execution import ExecutionAssumptions, OrderIntent, run_execution


def _bars() -> pd.DataFrame:
    opens = pd.date_range("2026-08-21 10:00", periods=5, freq="min", tz="UTC")
    return pd.DataFrame(
        {
            "symbol": "XYZ",
            "open_timestamp": opens,
            "close_timestamp": opens + pd.Timedelta(minutes=1),
            "available_at": opens + pd.Timedelta(minutes=1),
            "finalized": True,
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [101.0, 102.0, 103.0, 106.0, 105.0],
            "low": [99.0, 100.0, 101.0, 98.0, 103.0],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5],
            "bid_open": [99.98, 100.98, 101.98, 102.98, 103.98],
            "ask_open": [100.02, 101.02, 102.02, 103.02, 104.02],
            "volume": [100.0, 100.0, 12.0, 100.0, 100.0],
            "halted": False,
            "session": ["A", "A", "A", "A", "A"],
        }
    )


def test_market_orders_wait_for_next_bar_and_round_price_and_quantity_adversely() -> None:
    order = OrderIntent(
        order_id="buy-1",
        strategy_id="trend",
        symbol="XYZ",
        decision_timestamp=pd.Timestamp("2026-08-21 10:01", tz="UTC"),
        side="buy",
        quantity=10.37,
    )
    assumptions = ExecutionAssumptions(
        tick_size=0.05,
        lot_size=0.1,
        costs=CostAssumptions(taker_fee_bps=10, commission_per_unit=0.02, slippage_bps=5),
    )

    result = run_execution(_bars(), [order], assumptions)

    assert not result.rejections
    fill = result.fills[0]
    assert fill.execution_timestamp == pd.Timestamp("2026-08-21 10:01", tz="UTC")
    assert fill.execution_timestamp == _bars().iloc[1]["open_timestamp"]
    assert fill.quantity == 10.3
    assert fill.price == 101.10
    assert fill.notional == pytest.approx(1_041.33)
    assert fill.fee == pytest.approx(1.04133)
    assert fill.commission == pytest.approx(0.206)
    assert fill.spread_cost == pytest.approx(0.206)
    assert fill.slippage_cost == pytest.approx(0.824)
    assert fill.total_cost == pytest.approx(2.27733)


def test_sell_uses_bid_and_downward_tick_rounding_and_maker_fee_is_explicit() -> None:
    order = OrderIntent(
        order_id="sell-1",
        strategy_id="reversion",
        symbol="XYZ",
        decision_timestamp=pd.Timestamp("2026-08-21 10:01", tz="UTC"),
        side="sell",
        quantity=2.04,
    )
    assumptions = ExecutionAssumptions(
        tick_size=0.05,
        lot_size=0.1,
        costs=CostAssumptions(taker_fee_bps=10, maker_fee_bps=2, slippage_bps=5),
    )

    fill = run_execution(_bars(), [order], assumptions).fills[0]
    maker = calculate_transaction_cost(1_000, 10, assumptions.costs, liquidity="maker")

    assert fill.quantity == 2.0
    assert fill.price == 100.90
    assert fill.fee == pytest.approx(0.2018)
    assert maker.fee == 0.2


def test_latency_skips_bars_until_the_first_actionable_open() -> None:
    order = OrderIntent(
        order_id="late-1",
        strategy_id="trend",
        symbol="XYZ",
        decision_timestamp=pd.Timestamp("2026-08-21 10:01", tz="UTC"),
        side="buy",
        quantity=1,
    )

    result = run_execution(_bars(), [order], ExecutionAssumptions(latency=timedelta(seconds=61)))

    assert result.fills[0].execution_timestamp == pd.Timestamp("2026-08-21 10:03", tz="UTC")


def test_participation_cap_partially_fills_and_zero_capacity_rejects_explicitly() -> None:
    orders = [
        OrderIntent(
            order_id="partial",
            strategy_id="trend",
            symbol="XYZ",
            decision_timestamp=pd.Timestamp("2026-08-21 10:02", tz="UTC"),
            side="buy",
            quantity=10,
        ),
        OrderIntent(
            order_id="too-small",
            strategy_id="trend",
            symbol="XYZ",
            decision_timestamp=pd.Timestamp("2026-08-21 10:01", tz="UTC"),
            side="buy",
            quantity=0.4,
        ),
    ]
    assumptions = ExecutionAssumptions(lot_size=0.5, participation_rate=0.1)

    result = run_execution(_bars(), orders, assumptions)

    assert result.fills[0].order_id == "partial"
    assert result.fills[0].quantity == 1.0
    assert result.fills[0].status == "partial"
    assert result.rejections[0].order_id == "too-small"
    assert result.rejections[0].reason == "quantity_below_lot_size"


def test_unavailable_short_borrow_is_rejected_with_a_reason() -> None:
    order = OrderIntent(
        order_id="short",
        strategy_id="reversion",
        symbol="XYZ",
        decision_timestamp=pd.Timestamp("2026-08-21 10:01", tz="UTC"),
        side="sell",
        quantity=1,
        position_effect="open",
    )

    result = run_execution(_bars(), [order], ExecutionAssumptions(short_borrow_available=False))

    assert not result.fills
    assert result.rejections[0].reason == "short_borrow_unavailable"


def test_auto_sell_from_flat_cannot_bypass_unavailable_borrow() -> None:
    order = OrderIntent(
        order_id="auto-short",
        strategy_id="reversion",
        symbol="XYZ",
        decision_timestamp=pd.Timestamp("2026-08-21 10:01", tz="UTC"),
        side="sell",
        quantity=1,
    )

    result = run_execution(_bars(), [order], ExecutionAssumptions(short_borrow_available=False))

    assert not result.fills
    assert result.rejections[0].reason == "short_borrow_unavailable"


def test_execution_rejects_an_unresolved_revision_ledger() -> None:
    bars = _bars().iloc[:2].copy()
    bars["provider"] = "test"
    bars["feed"] = "sip"
    bars["interval"] = "1m"
    bars["revision"] = 1
    correction = bars.iloc[[0]].copy()
    correction["revision"] = 2
    correction["available_at"] = correction["available_at"] + pd.Timedelta(minutes=5)

    with pytest.raises(ValueError, match="causally resolved revision"):
        run_execution(pd.concat([bars, correction], ignore_index=True), [], ExecutionAssumptions())


def test_funding_and_borrow_costs_use_signed_funding_and_short_notional() -> None:
    costs = CostAssumptions(funding_bps_per_period=10, borrow_bps_per_period=20)

    long = calculate_carry_cost(2, 100, costs)
    short = calculate_carry_cost(-2, 100, costs)

    assert long.funding == 0.2
    assert long.borrow == 0
    assert long.total == 0.2
    assert short.funding == -0.2
    assert short.borrow == 0.4
    assert short.total == 0.2


def test_last_bar_of_an_injected_session_flattens_the_open_position() -> None:
    bars = _bars().iloc[:4].copy()
    bars["session"] = ["A", "A", "B", "B"]
    order = OrderIntent(
        order_id="entry",
        strategy_id="trend",
        symbol="XYZ",
        decision_timestamp=pd.Timestamp("2026-08-21 10:00:30", tz="UTC"),
        side="buy",
        quantity=2,
    )
    assumptions = ExecutionAssumptions(
        flatten_at_session_end=True,
        session_label=lambda row: str(row["session"]),
    )

    result = run_execution(bars, [order], assumptions)

    assert [fill.order_type for fill in result.fills] == ["market", "session_flatten"]
    assert result.fills[1].side == "sell"
    assert result.fills[1].quantity == 2
    assert result.fills[1].price == 101.5
    assert result.fills[1].execution_timestamp == pd.Timestamp("2026-08-21 10:02", tz="UTC")
    assert result.positions == {"XYZ": 0.0}


def test_same_bar_stop_target_collision_uses_documented_adverse_ordering() -> None:
    order = OrderIntent(
        order_id="bracket",
        strategy_id="trend",
        symbol="XYZ",
        decision_timestamp=pd.Timestamp("2026-08-21 10:03", tz="UTC"),
        side="sell",
        quantity=2,
        order_type="bracket_exit",
        stop_price=99,
        target_price=105,
        position_effect="close",
    )

    result = run_execution(_bars(), [order], ExecutionAssumptions())

    fill = result.fills[0]
    assert fill.price == 99
    assert fill.fill_reason == "adverse_stop_before_target"


def test_untouched_trigger_is_not_silently_filled() -> None:
    order = OrderIntent(
        order_id="untouched",
        strategy_id="trend",
        symbol="XYZ",
        decision_timestamp=pd.Timestamp("2026-08-21 10:03", tz="UTC"),
        side="sell",
        quantity=1,
        order_type="protective_stop",
        stop_price=90,
        position_effect="close",
    )

    result = run_execution(_bars(), [order], ExecutionAssumptions())

    assert not result.fills
    assert result.rejections[0].reason == "trigger_not_reached"
