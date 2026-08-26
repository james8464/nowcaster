from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from src.live_monitor.command import _transport_health_after
from src.live_monitor.providers import (
    AlpacaMarketDataAdapter,
    BinanceSpotAdapter,
    ProviderDecodeError,
    ProviderHealthTracker,
    ProviderSymbolMetadata,
    ReconnectPolicy,
    expected_repair_starts,
    load_alpaca_symbol_metadata,
    load_binance_repair_bars,
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


def test_alpaca_authenticated_subscription_and_market_fixture_keep_transport_healthy() -> None:
    adapter = AlpacaMarketDataAdapter(feed="iex", key_id="key-value", secret="secret-value")
    healthy = False
    fixture = lines("alpaca_stream.jsonl")
    for line in fixture[:3]:
        for event in adapter.decode(line, received_at=NOW):
            healthy = _transport_health_after(healthy, event)

    assert healthy is True
    for event in adapter.decode(fixture[3], received_at=NOW):
        healthy = _transport_health_after(healthy, event)
    assert healthy is True


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


def test_alpaca_rejects_untradable_metadata_and_uses_sub_dollar_increment() -> None:
    def metadata_response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"symbol": "AAPL", "tradable": False, "shortable": False, "easy_to_borrow": False},
        )

    with (
        httpx.Client(transport=httpx.MockTransport(metadata_response)) as client,
        pytest.raises(ValueError, match="metadata is unavailable"),
    ):
        load_alpaca_symbol_metadata(("AAPL",), key_id="key", secret="secret", client=client)

    adapter = AlpacaMarketDataAdapter(
        feed="iex",
        key_id="key",
        secret="secret",
        metadata={"PENNY": ProviderSymbolMetadata("PENNY", Decimal(0), True, False, False)},
    )
    event = adapter.decode(
        '[{"T":"q","S":"PENNY","bp":"0.5000","ap":"0.5002","t":"2026-08-26T14:01:02Z"}]',
        received_at=NOW,
    )[0]
    assert isinstance(event, MarketQuote) and event.tick_size == Decimal("0.0001")


def test_binance_gap_repair_requires_every_bounded_minute() -> None:
    start = NOW.replace(second=0)
    payload = [
        [int((start + timedelta(minutes=index)).timestamp() * 1_000), "100", "101", "99", "100", "5"]
        for index in range(2)
    ]

    def response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        repaired = load_binance_repair_bars("BTCUSDT", start=start, end=start + timedelta(minutes=2), client=client)
    assert tuple(item.start for item in repaired) == (start, start + timedelta(minutes=1))
    assert all(item.repair_verified for item in repaired)

    with (
        httpx.Client(transport=httpx.MockTransport(response)) as client,
        pytest.raises(ValueError, match="incomplete"),
    ):
        load_binance_repair_bars("BTCUSDT", start=start, end=start + timedelta(minutes=3), client=client)


def test_alpaca_gap_calendar_ignores_closed_market_time() -> None:
    prior_close = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)
    next_open = datetime(2026, 8, 26, 13, 30, tzinfo=UTC)

    assert expected_repair_starts("alpaca", "iex", prior_close, next_open) == ()
    assert expected_repair_starts("alpaca", "iex", next_open, next_open + timedelta(minutes=2)) == (
        next_open,
        next_open + timedelta(minutes=1),
    )
