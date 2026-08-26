from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.live_monitor import command
from src.live_monitor.command import MonitorBootstrap
from src.live_monitor.providers import ProviderSymbolMetadata
from src.live_monitor.types import MarketBar, ProviderHealthEvent

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
