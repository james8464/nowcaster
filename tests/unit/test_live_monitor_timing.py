from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.live_monitor.types import MarketTrade, ProviderHealthEvent

NOW = datetime(2026, 8, 31, 6, 16, tzinfo=UTC)


def trade():
    return MarketTrade(
        provider="binance",
        feed="spot",
        symbol="BTCUSDT",
        price=100,
        size=1,
        provider_time=NOW + timedelta(milliseconds=80),
        received_at=NOW,
    )


def test_live_delivery_waits_for_provider_time_and_preserves_raw_receipt():
    from src.live_monitor.timing import prepare_market_event

    clock_value = NOW
    waits = []

    async def pause(seconds):
        nonlocal clock_value
        waits.append(seconds)
        clock_value += timedelta(seconds=seconds)

    original = trade()
    processed = asyncio.run(prepare_market_event(original, clock=lambda: clock_value, pause=pause))
    assert waits == [0.08]
    assert processed.provider_time == NOW + timedelta(milliseconds=80)
    assert processed.received_at == NOW
    assert processed.processed_at == NOW + timedelta(milliseconds=80)
    assert original.processed_at == NOW
    assert processed.event_id == original.event_id


def test_live_delivery_measures_queue_delay_without_waiting_again():
    from src.live_monitor.timing import prepare_market_event

    async def no_wait(_seconds):
        pytest.fail("an old event must not cause an artificial delay")

    processed = asyncio.run(prepare_market_event(trade(), clock=lambda: NOW + timedelta(seconds=40), pause=no_wait))
    assert processed.processed_at == NOW + timedelta(seconds=40)
    assert processed.received_at == NOW


@pytest.mark.parametrize("regression", [False, True])
def test_clock_adjustments_cannot_create_unbounded_waits_or_fabricated_processing_time(regression):
    from src.live_monitor.timing import prepare_market_event

    async def unchanged_clock(_seconds):
        pass

    now = NOW - timedelta(seconds=2) if regression else NOW
    with pytest.raises(ValueError, match="clock"):
        asyncio.run(prepare_market_event(trade(), clock=lambda: now, pause=unchanged_clock))


def test_process_clock_fails_closed_on_regression_between_events_or_silent_polls():
    from src.live_monitor.timing import CausalClock

    readings = iter((NOW, NOW + timedelta(seconds=5), NOW + timedelta(seconds=4), NOW + timedelta(seconds=6)))
    clock = CausalClock(lambda: next(readings))

    assert clock.now() == NOW
    assert clock.now() - NOW == timedelta(seconds=5)
    with pytest.raises(ValueError, match="regressed"):
        clock.now()
    with pytest.raises(ValueError, match="regressed"):
        clock.now()
    assert clock.watermark == NOW + timedelta(seconds=5)


def test_health_events_are_stamped_at_the_shared_causal_processing_watermark():
    from src.live_monitor.timing import CausalClock, prepare_market_event

    observed = NOW + timedelta(seconds=2)
    health = ProviderHealthEvent(
        provider="binance",
        feed="spot",
        status="healthy",
        reason="subscribed",
        occurred_at=NOW,
    )
    clock = CausalClock(lambda: observed)

    processed = asyncio.run(prepare_market_event(health, clock=clock.now))

    assert processed.occurred_at == observed
