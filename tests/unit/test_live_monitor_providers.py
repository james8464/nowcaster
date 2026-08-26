from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.live_monitor.providers import (
    AlpacaMarketDataAdapter,
    BinanceSpotAdapter,
    ProviderDecodeError,
    ProviderHealthTracker,
    ReconnectPolicy,
)
from src.live_monitor.types import MarketBar, MarketQuote, MonitorHealth, ProviderHealthEvent

FIXTURES = Path(__file__).parents[1] / "fixtures" / "live_monitor"
NOW = datetime(2026, 8, 26, 14, 1, 2, tzinfo=UTC)


def lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


def test_alpaca_decodes_complete_multi_event_frame_and_classifies_connection_limit() -> None:
    adapter = AlpacaMarketDataAdapter(feed="iex", key_id="key-value", secret="secret-value")
    assert adapter.authentication() == {"action": "auth", "key": "key-value", "secret": "secret-value"}
    assert adapter.subscription(("aapl",)) == {
        "action": "subscribe",
        "quotes": ["AAPL"],
        "bars": ["AAPL"],
    }

    events = adapter.decode(lines("alpaca_stream.jsonl")[3], received_at=NOW)

    assert len(events) == 2
    assert isinstance(events[0], MarketQuote) and events[0].symbol == "AAPL"
    assert isinstance(events[1], MarketBar) and events[1].finalized is True
    error = adapter.decode(lines("alpaca_stream.jsonl")[4], received_at=NOW)
    assert error == (
        ProviderHealthEvent(
            provider="alpaca",
            feed="iex",
            status=MonitorHealth.FAILED,
            reason="connection_limit",
            occurred_at=NOW,
        ),
    )
    assert "secret-value" not in repr(adapter)


def test_binance_accepts_only_closed_klines_and_maps_book_ticker() -> None:
    adapter = BinanceSpotAdapter()
    assert adapter.subscription(("BTCUSDT", "ethusdt")) == {
        "method": "SUBSCRIBE",
        "params": [
            "btcusdt@bookTicker",
            "btcusdt@kline_1m",
            "ethusdt@bookTicker",
            "ethusdt@kline_1m",
        ],
        "id": 1,
    }
    quote = adapter.decode(lines("binance_stream.jsonl")[1], received_at=NOW)
    closed = adapter.decode(lines("binance_stream.jsonl")[2], received_at=NOW)
    open_kline = adapter.decode(lines("binance_stream.jsonl")[3], received_at=NOW)

    assert len(quote) == 1 and isinstance(quote[0], MarketQuote)
    assert len(closed) == 1 and isinstance(closed[0], MarketBar)
    assert closed[0].symbol == "BTCUSDT" and closed[0].feed == "spot"
    assert open_kline == ()


def test_decoders_reject_oversized_malformed_and_unknown_payloads_without_secrets() -> None:
    alpaca = AlpacaMarketDataAdapter(feed="sip", key_id="identifier", secret="do-not-log")
    for value in ("not-json", "{}", "[" + " " * 70_000 + "]"):
        with pytest.raises(ProviderDecodeError) as raised:
            alpaca.decode(value, received_at=NOW)
        assert "do-not-log" not in str(raised.value)


def test_health_tracker_stays_frozen_until_fresh_continuous_data_and_reconnect_is_bounded() -> None:
    tracker = ProviderHealthTracker(stale_after=timedelta(seconds=30))
    assert tracker.status(now=NOW) is MonitorHealth.WARMING
    tracker.connected(at=NOW)
    tracker.observed(at=NOW + timedelta(seconds=1), continuity_ok=False)
    assert tracker.status(now=NOW + timedelta(seconds=2)) is MonitorHealth.RECONNECTING
    tracker.observed(at=NOW + timedelta(seconds=3), continuity_ok=True)
    assert tracker.status(now=NOW + timedelta(seconds=4)) is MonitorHealth.HEALTHY
    assert tracker.status(now=NOW + timedelta(seconds=40)) is MonitorHealth.STALE

    policy = ReconnectPolicy(initial_seconds=1, maximum_seconds=8, multiplier=2, rotate_after=timedelta(hours=23))
    assert [policy.delay(attempt) for attempt in range(6)] == [1, 2, 4, 8, 8, 8]
    assert policy.rotation_due(connected_at=NOW, now=NOW + timedelta(hours=23)) is True
