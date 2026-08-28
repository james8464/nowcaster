from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.live_monitor.types import (
    AlertState,
    DepthLevel,
    Direction,
    MarketBar,
    MarketDepth,
    MarketQuote,
    MarketStatusEvent,
    MarketTrade,
    MonitorHealth,
    MonitorWireEvent,
    TradePlan,
)

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def valid_bar(**updates) -> MarketBar:
    values = {
        "provider": "alpaca",
        "feed": "iex",
        "symbol": "aapl",
        "interval": "1m",
        "start": NOW,
        "end": NOW + timedelta(minutes=1),
        "available_at": NOW + timedelta(minutes=1, seconds=2),
        "received_at": NOW + timedelta(minutes=1, seconds=3),
        "open": Decimal("100"),
        "high": Decimal("101"),
        "low": Decimal("99"),
        "close": Decimal("100.5"),
        "volume": Decimal("42"),
        "finalized": True,
        "revision": 0,
    }
    values.update(updates)
    return MarketBar(**values)


def test_market_bar_normalizes_symbol_and_has_stable_identity() -> None:
    first = valid_bar()
    second = valid_bar(received_at=NOW + timedelta(minutes=1, seconds=9))

    assert first.symbol == "AAPL"
    assert first.bar_id == second.bar_id
    assert len(first.bar_id) == 64


def test_market_bar_rejects_naive_time_impossible_ohlc_and_unfinalized_input() -> None:
    with pytest.raises(ValidationError, match="explicit UTC"):
        valid_bar(start=datetime(2026, 8, 26, 12, 0))
    with pytest.raises(ValidationError, match="OHLC"):
        valid_bar(high=Decimal("99"))
    with pytest.raises(ValidationError, match="finalized"):
        valid_bar(finalized=False)


def test_market_quote_rejects_crossed_market_and_nonpositive_tick() -> None:
    with pytest.raises(ValidationError, match="ask"):
        MarketQuote(
            provider="alpaca",
            feed="iex",
            symbol="AAPL",
            bid=Decimal("101"),
            ask=Decimal("100"),
            last=Decimal("100.5"),
            tick_size=Decimal("0.01"),
            provider_time=NOW,
            received_at=NOW,
        )


def test_market_quote_trade_depth_and_status_retain_microstructure_provenance() -> None:
    quote = MarketQuote(
        provider="binance",
        feed="spot",
        symbol="btcusdt",
        bid=Decimal("64000.10"),
        ask=Decimal("64000.20"),
        bid_size=Decimal("1.2"),
        ask_size=Decimal("0.8"),
        last=Decimal("64000.15"),
        tick_size=Decimal("0.01"),
        sequence=42,
        provider_time=NOW,
        received_at=NOW + timedelta(milliseconds=5),
    )
    trade = MarketTrade(
        provider="binance",
        feed="spot",
        symbol="BTCUSDT",
        trade_id="17",
        price=Decimal("64000.15"),
        size=Decimal("0.25"),
        aggressor="sell",
        sequence=17,
        provider_time=NOW,
        received_at=NOW + timedelta(milliseconds=6),
    )
    depth = MarketDepth(
        provider="binance",
        feed="spot",
        symbol="BTCUSDT",
        first_update_id=40,
        final_update_id=42,
        bids=(DepthLevel(price=Decimal("64000.10"), size=Decimal("1.2")),),
        asks=(DepthLevel(price=Decimal("64000.20"), size=Decimal("0.8")),),
        provider_time=NOW,
        received_at=NOW + timedelta(milliseconds=7),
    )
    status = MarketStatusEvent(
        provider="alpaca",
        feed="sip",
        symbol="AAPL",
        kind="luld",
        status="limit_state",
        provider_time=NOW,
        received_at=NOW + timedelta(milliseconds=8),
        details={"lower": "99", "upper": "101"},
    )

    assert (quote.bid_size, quote.ask_size, quote.sequence) == (Decimal("1.2"), Decimal("0.8"), 42)
    assert quote.processed_at == quote.received_at
    assert trade.aggressor == "sell" and len(trade.event_id) == 64
    assert depth.sequence == 42 and depth.bids[0].price == Decimal("64000.10")
    assert status.kind == "luld" and len(status.event_id) == 64


def test_microstructure_events_reject_negative_sizes_and_reversed_sequences() -> None:
    with pytest.raises(ValidationError):
        DepthLevel(price=Decimal("100"), size=Decimal("-1"))
    with pytest.raises(ValidationError, match="update id"):
        MarketDepth(
            provider="binance",
            feed="spot",
            symbol="BTCUSDT",
            first_update_id=12,
            final_update_id=11,
            bids=(),
            asks=(),
            provider_time=NOW,
            received_at=NOW,
        )
    with pytest.raises(ValidationError):
        MarketQuote(
            provider="alpaca",
            feed="iex",
            symbol="AAPL",
            bid=Decimal("99"),
            ask=Decimal("100"),
            last=Decimal("100"),
            tick_size=Decimal("0"),
            provider_time=NOW,
            received_at=NOW,
        )


def test_trade_plan_enforces_long_and_short_price_geometry() -> None:
    common = {
        "plan_id": "a" * 64,
        "provider": "alpaca",
        "feed": "iex",
        "symbol": "AAPL",
        "decision_interval": "5m",
        "decision_time": NOW,
        "expires_at": NOW + timedelta(minutes=15),
        "entry_low": Decimal("100"),
        "entry_high": Decimal("100.1"),
        "risk_per_unit": Decimal("3"),
        "reward_to_risk_1": Decimal("1"),
        "reward_to_risk_2": Decimal("1.5"),
        "venue_note": None,
    }
    long_plan = TradePlan(
        **common,
        direction=Direction.LONG,
        stop=Decimal("97"),
        target_1=Decimal("103"),
        target_2=Decimal("104.5"),
    )
    assert long_plan.direction is Direction.LONG

    with pytest.raises(ValidationError, match="long plan"):
        TradePlan(
            **common,
            direction=Direction.LONG,
            stop=Decimal("101"),
            target_1=Decimal("103"),
            target_2=Decimal("104.5"),
        )

    short_values = dict(common)
    short_values.update(
        entry_low=Decimal("99.8"),
        entry_high=Decimal("99.9"),
        risk_per_unit=Decimal("3.1"),
        reward_to_risk_1=Decimal("1.0322580645"),
        reward_to_risk_2=Decimal("1.5161290323"),
    )
    short_plan = TradePlan(
        **short_values,
        direction=Direction.SHORT,
        stop=Decimal("103"),
        target_1=Decimal("96.7"),
        target_2=Decimal("95.2"),
    )
    assert short_plan.direction is Direction.SHORT


def test_wire_event_requires_utc_and_bounds_nested_payload() -> None:
    event = MonitorWireEvent(
        schema_version=1,
        event_id="b" * 64,
        sequence=1,
        event_type="heartbeat",
        emitted_at=NOW,
        payload={"health": MonitorHealth.HEALTHY.value, "state": AlertState.WATCHING.value},
    )
    assert event.payload["health"] == "healthy"

    payload: dict[str, object] = {}
    cursor = payload
    for index in range(20):
        nested: dict[str, object] = {}
        cursor[str(index)] = nested
        cursor = nested
    with pytest.raises(ValidationError, match="depth"):
        MonitorWireEvent(
            schema_version=1,
            event_id="c" * 64,
            sequence=2,
            event_type="heartbeat",
            emitted_at=NOW,
            payload=payload,
        )
