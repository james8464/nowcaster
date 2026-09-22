"""Causal boundaries for the day-trader context; fixtures use hand-checkable bars."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.research.day_trader_context import (
    CalendarEvent,
    CalendarSnapshot,
    ContextObservation,
    DayTraderContextProtocol,
    MarketContextSnapshot,
    extract_context,
)
from src.research.round_two_contracts import ResearchRoundProtocol

START = datetime(2026, 9, 22, 12, tzinfo=UTC)
DECISION = START + timedelta(hours=1, seconds=2)


def protocol():
    return ResearchRoundProtocol.default(round_id="context-test", starts_at=START)


def bars(count=60):
    result = []
    for index in range(count):
        boundary = START + timedelta(minutes=index + 1)
        price = Decimal(100) + Decimal(index) / 100
        result.append(
            ContextObservation(
                provider="binance",
                feed="spot",
                symbol="BTCUSDT",
                provider_at=boundary,
                received_at=boundary + timedelta(seconds=1),
                available_at=boundary + timedelta(seconds=1),
                source_key=f"bar-{index}",
                open=price,
                high=price + Decimal("0.02"),
                low=price - Decimal("0.02"),
                close=price,
                volume=10,
                bid=price - Decimal("0.01"),
                ask=price + Decimal("0.01"),
                bid_size=3,
                ask_size=1,
                quote_provider_at=boundary,
                quote_received_at=boundary + timedelta(seconds=1),
                quote_available_at=boundary + timedelta(seconds=1),
                quote_source_key=f"quote-{index}",
            )
        )
    return tuple(result)


def calendar(**changes):
    return CalendarSnapshot(
        source="local-calendar",
        revision="calendar-v1",
        published_at=START,
        available_at=START,
        valid_until=START + timedelta(hours=2),
        coverage_starts_at=START - timedelta(hours=1),
        coverage_ends_at=START + timedelta(hours=2),
        events=(),
    ).model_copy(update=changes)


def test_complete_context_has_real_features_and_verifiable_identity():
    context = extract_context(protocol(), bars(), DECISION, calendar())
    assert context.exclusions == ()
    assert tuple(trend.timeframe_minutes for trend in context.trends) == (1, 5, 15)
    assert all(trend.direction == "up" and trend.strength == 1 for trend in context.trends)
    assert context.quote_imbalance == Decimal("0.5")
    assert context.spread_bps == pytest.approx(Decimal("1.98826921165125758027636942"))
    assert context.realized_volatility_bps > 0
    assert context.atr_normalized_range > 0
    assert context.session == "europe_americas_overlap"
    assert context.available_at <= context.decision_at < context.expires_at
    assert context.protocol_hash == protocol().identity_hash
    assert MarketContextSnapshot.model_validate_json(context.model_dump_json()) == context
    with pytest.raises(ValueError, match="hash"):
        MarketContextSnapshot.model_validate({**context.model_dump(), "quote_imbalance": Decimal("0.9")})


def test_future_or_later_received_bars_cannot_repaint_a_snapshot():
    context = extract_context(protocol(), bars(), DECISION, calendar())
    future = bars(61)[-1]
    revision = bars()[-1].model_copy(update={"available_at": DECISION + timedelta(seconds=1)})
    assert extract_context(protocol(), (*bars(), future, revision), DECISION, calendar()) == context


def test_unfinished_aggregate_is_excluded_from_features_and_its_own_extrema():
    context = extract_context(protocol(), bars(59), DECISION - timedelta(minutes=1), calendar())
    assert "insufficient_15m_history" in context.exclusions
    assert next(item for item in context.trends if item.timeframe_minutes == 15).direction == "unavailable"
    assert next(item for item in context.trends if item.timeframe_minutes == 5).last_bar_at == START + timedelta(
        minutes=55
    )


@pytest.mark.parametrize(
    "changed,reason",
    [
        ({"finalized": False}, "unfinalized_input"),
        ({"bid_size": None}, "order_book_unavailable"),
        ({"quote_source_key": None}, "order_book_unavailable"),
        ({"quote_provider_at": START}, "order_book_stale"),
        ({"bid": Decimal("90")}, "spread_exceeds_maximum"),
        ({"volume": Decimal(0)}, "liquidity_unavailable"),
    ],
)
def test_invalid_current_market_context_is_excluded(changed, reason):
    observations = (*bars()[:-1], bars()[-1].model_copy(update=changed))
    result = extract_context(protocol(), observations, DECISION, calendar())
    assert reason in result.exclusions


def test_unknown_order_book_size_is_not_invented_from_price():
    observations = tuple(
        item.model_dump(
            exclude={
                "finalized",
                "bid_size",
                "ask_size",
                "quote_provider_at",
                "quote_received_at",
                "quote_available_at",
                "quote_source_key",
            }
        )
        for item in bars()
    )
    result = extract_context(protocol(), observations, DECISION, calendar())
    assert result.quote_imbalance is None
    assert "order_book_unavailable" in result.exclusions


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"valid_until": START + timedelta(minutes=59)}, "calendar_stale"),
        ({"available_at": DECISION + timedelta(seconds=1)}, "calendar_unavailable"),
        ({"coverage_ends_at": START + timedelta(minutes=59)}, "calendar_coverage_missing"),
    ],
)
def test_calendar_requires_current_available_coverage(changes, reason):
    assert reason in extract_context(protocol(), bars(), DECISION, calendar(**changes)).exclusions


def test_missing_calendar_and_scheduled_blackout_are_explicit():
    assert "calendar_missing" in extract_context(protocol(), bars(), DECISION, None).exclusions
    event = CalendarEvent(
        event_id="event-1",
        scheduled_at=DECISION + timedelta(minutes=5),
        available_at=START,
        symbols=("BTCUSDT",),
        impact="high",
    )
    result = extract_context(protocol(), bars(), DECISION, calendar(events=(event,)))
    assert result.calendar_blackout is True
    assert "calendar_blackout" in result.exclusions


def test_gaps_conflicts_and_stale_bars_block_context():
    assert "history_gap" in extract_context(protocol(), bars()[1:30] + bars()[31:], DECISION, calendar()).exclusions
    duplicate = bars()[-1].model_copy(update={"source_key": "different", "volume": Decimal(9)})
    assert (
        "conflicting_observations" in extract_context(protocol(), (*bars(), duplicate), DECISION, calendar()).exclusions
    )
    assert (
        "observation_stale"
        in extract_context(protocol(), bars(), DECISION + timedelta(seconds=20), calendar()).exclusions
    )


def test_volatility_threshold_is_protocol_bound_and_exclusion_survives_hashing():
    constrained = DayTraderContextProtocol(round_protocol=protocol(), maximum_realized_volatility_bps=Decimal("0.01"))
    result = extract_context(constrained, bars(), DECISION, calendar())
    assert "volatility_exceeds_maximum" in result.exclusions
    assert (
        result.context_protocol_hash != extract_context(protocol(), bars(), DECISION, calendar()).context_protocol_hash
    )


def test_symbol_and_source_mismatch_cannot_be_combined_into_trend():
    with pytest.raises(ValueError):
        extract_context(protocol(), (bars()[0].model_copy(update={"symbol": "OIL"}),), DECISION, calendar())
    with pytest.raises(ValueError):
        extract_context(protocol(), (*bars(), bars()[0].model_copy(update={"symbol": "ETHUSDT"})), DECISION, calendar())


def test_calendar_rejects_events_that_were_not_known_at_snapshot_publication():
    event = CalendarEvent(
        event_id="event-1",
        scheduled_at=DECISION,
        available_at=START + timedelta(seconds=1),
        symbols=("BTCUSDT",),
        impact="high",
    )
    with pytest.raises(ValueError):
        extract_context(protocol(), bars(), DECISION, calendar(events=(event,)))


def test_quote_cannot_be_attached_to_an_observation_before_it_was_available():
    row = bars()[-1].model_copy(update={"quote_available_at": DECISION + timedelta(seconds=1)})
    with pytest.raises(ValueError, match="quote.*availability"):
        extract_context(protocol(), (*bars()[:-1], row), DECISION, calendar())


def test_available_future_quote_or_unaligned_bar_cannot_create_features():
    row = bars()[-1].model_copy(update={"quote_provider_at": DECISION + timedelta(seconds=1)})
    with pytest.raises(ValueError, match="quote chronology"):
        extract_context(protocol(), (*bars()[:-1], row), DECISION, calendar())
    row = bars()[-1].model_copy(update={"provider_at": START + timedelta(minutes=59, seconds=30)})
    assert "unaligned_bar" in extract_context(protocol(), (*bars()[:-1], row), DECISION, calendar()).exclusions


def test_absent_quote_prices_never_create_zero_spread_or_imbalance():
    row = bars()[-1].model_copy(update={"bid": None, "ask": None})
    result = extract_context(protocol(), (*bars()[:-1], row), DECISION, calendar())
    assert result.spread_bps is None and result.quote_imbalance is None
    assert "spread_unavailable" in result.exclusions


def test_abnormal_latest_range_is_retained_as_an_exclusion():
    row = bars()[-1].model_copy(update={"high": Decimal("110"), "low": Decimal("90")})
    result = extract_context(protocol(), (*bars()[:-1], row), DECISION, calendar())
    assert "abnormal_range" in result.exclusions


def test_flat_prices_are_flat_with_zero_volatility_but_not_invented_atr():
    observations = tuple(
        row.model_copy(update={"open": Decimal(100), "high": Decimal(100), "low": Decimal(100), "close": Decimal(100)})
        for row in bars()
    )
    result = extract_context(protocol(), observations, DECISION, calendar())
    assert all(trend.direction == "flat" and trend.strength == 0 for trend in result.trends)
    assert result.realized_volatility_bps == 0
    assert result.atr_normalized_range is None
    assert "atr_unavailable" in result.exclusions


def test_no_causally_available_bars_has_no_market_features():
    result = extract_context(protocol(), bars(), START, calendar())
    assert result.source_hashes == ()
    assert result.spread_bps is None and result.quote_imbalance is None
    assert all(trend.direction == "unavailable" for trend in result.trends)
    assert "observations_unavailable" in result.exclusions
