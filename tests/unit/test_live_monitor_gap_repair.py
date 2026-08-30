from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.live_monitor import command
from src.live_monitor.command import MonitorBootstrap
from src.live_monitor.providers import ProviderSymbolMetadata
from src.live_monitor.types import MarketBar, MarketDepth, ProviderHealthEvent

START = datetime(2026, 8, 26, 14, 0, tzinfo=UTC)


def _bar(minute: int) -> MarketBar:
    start = START + timedelta(minutes=minute)
    return MarketBar(
        provider="binance",
        feed="spot",
        symbol="BTCUSDT",
        interval="1m",
        start=start,
        end=start + timedelta(minutes=1),
        available_at=start + timedelta(minutes=1),
        received_at=start + timedelta(minutes=1),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("10"),
        finalized=True,
        revision=0,
    )


def test_failed_gap_repair_retains_watermark_and_retries_before_health_recovers(monkeypatch) -> None:
    attempts: list[tuple[datetime, datetime]] = []

    class Adapter:
        def __init__(self, **_kwargs):
            pass

        async def stream(self, _url, _symbols):
            yield ProviderHealthEvent(
                provider="binance", feed="spot", status="healthy", reason="connected", occurred_at=START
            )
            yield _bar(3)
            yield _bar(4)

    def repair(_symbol, *, start, end):
        attempts.append((start, end))
        if len(attempts) == 1:
            raise ValueError("temporary failure")
        return tuple(_bar(index) for index in range(1, 4))

    monkeypatch.setattr(command, "BinanceSpotAdapter", Adapter)
    monkeypatch.setattr(command, "load_binance_repair_bars", repair)
    bootstrap = MonitorBootstrap(
        schema_version=1,
        session_id="gap-test",
        database_url="duckdb:///:memory:",
        crypto=("BTCUSDT",),
        config_hash="c" * 64,
        cohort_hash="0" * 64,
    )
    metadata = {("binance", "BTCUSDT"): ProviderSymbolMetadata("BTCUSDT", Decimal("0.01"), True, True, True)}

    async def collect() -> list[object]:
        events = []
        stream = command._merged_live_events(
            bootstrap,
            metadata,
            initial_last_bar_end={("binance", "spot", "BTCUSDT"): START + timedelta(minutes=1)},
        )
        async for item in stream:
            events.append(item)
            if isinstance(item, MarketBar) and item.start == START + timedelta(minutes=4):
                break
        await stream.aclose()
        return events

    events = asyncio.run(collect())

    assert attempts == [
        (START + timedelta(minutes=1), START + timedelta(minutes=3)),
        (START + timedelta(minutes=1), START + timedelta(minutes=4)),
    ]
    assert any(isinstance(item, ProviderHealthEvent) and item.reason == "gap_repair_failed" for item in events)
    assert [item.start for item in events if isinstance(item, MarketBar)] == [
        START + timedelta(minutes=1),
        START + timedelta(minutes=2),
        START + timedelta(minutes=3),
        START + timedelta(minutes=4),
    ]


@pytest.mark.parametrize("unavailable", [False, True])
def test_live_stream_captures_one_verified_book_per_new_minute_and_never_fakes_missing_depth(monkeypatch, unavailable):
    calls = []

    class Adapter:
        def __init__(self, **_kwargs):
            pass

        async def stream(self, _url, _symbols):
            yield _bar(0)
            yield _bar(0)  # Duplicate/revision must not spend another REST request.
            yield _bar(1)

    def book(symbol):
        calls.append(symbol)
        if unavailable:
            raise ValueError("book unavailable")
        return MarketDepth(
            provider="binance",
            feed="spot",
            symbol=symbol,
            provider_time=START,
            received_at=START,
            snapshot_verified=True,
            first_update_id=10,
            final_update_id=10,
            bids=({"price": "99", "size": "100"},),
            asks=({"price": "101", "size": "100"},),
        )

    monkeypatch.setattr(command, "BinanceSpotAdapter", Adapter)
    monkeypatch.setattr(command, "load_binance_depth_snapshot", book)
    bootstrap = MonitorBootstrap(
        schema_version=1,
        session_id="book-test",
        database_url="duckdb:///:memory:",
        crypto=("BTCUSDT",),
        config_hash="c" * 64,
        cohort_hash="0" * 64,
    )
    metadata = {("binance", "BTCUSDT"): ProviderSymbolMetadata("BTCUSDT", Decimal("0.01"), True, True, True)}

    async def collect():
        events = []
        stream = command._merged_live_events(bootstrap, metadata, capture_verified_depth=True)
        async for event in stream:
            events.append(event)
            if isinstance(event, MarketBar) and event.start == START + timedelta(minutes=1):
                break
        await stream.aclose()
        return events

    events = asyncio.run(collect())
    assert len(calls) == 2
    assert len([item for item in events if isinstance(item, MarketDepth)]) == (0 if unavailable else 2)
    assert len([item for item in events if isinstance(item, MarketBar)]) == 3
