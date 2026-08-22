from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from src.ingestion.alpaca_bars import AlpacaBarProvider
from src.ingestion.bars import BarRequest, MarketBar
from src.ingestion.binance_bars import BinanceBarProvider
from src.ingestion.csv_bars import CSVBarProvider
from src.strategies.types import BarInterval

FIXTURES = Path(__file__).parents[1] / "fixtures" / "bars"


def _json_fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_binance_normalizes_utc_rejects_open_bar_and_hashes_complete_payload():
    payload = _json_fixture("binance_klines.json")
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    provider = BinanceBarProvider(
        client,
        clock=lambda: datetime(2026, 8, 22, 10, 7, tzinfo=UTC),
    )

    bars = list(
        provider.fetch(
            BarRequest(
                symbol="btcusdt",
                interval=BarInterval.FIVE_MINUTES,
                start=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
                end=datetime(2026, 8, 22, 10, 10, tzinfo=UTC),
            )
        )
    )

    assert len(bars) == 1
    bar = bars[0]
    assert bar.provider == "binance"
    assert bar.feed == "spot"
    assert bar.symbol == "BTCUSDT"
    assert bar.open_timestamp == datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
    assert bar.close_timestamp == datetime(2026, 8, 22, 10, 5, tzinfo=UTC)
    assert bar.available_at == datetime(2026, 8, 22, 10, 7, tzinfo=UTC)
    assert bar.trade_count == 321
    assert bar.payload_hash == "5ecaf0e14ec23613558771976a2b3832bb3b16cd7afb7ed807334f27ec0371ec"


def test_alpaca_preserves_feed_pages_exclusive_end_and_deduplicates_page_boundary(monkeypatch):
    pages = {
        None: _json_fixture("alpaca_bars_page_1.json"),
        "page-2": _json_fixture("alpaca_bars_page_2.json"),
    }
    tokens: list[str | None] = []

    def respond(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("page_token")
        tokens.append(token)
        assert request.headers["APCA-API-KEY-ID"] == "test-key"
        assert request.headers["APCA-API-SECRET-KEY"] == "test-secret"
        return httpx.Response(200, json=pages[token])

    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    client = httpx.Client(transport=httpx.MockTransport(respond))
    provider = AlpacaBarProvider(
        client,
        feed="iex",
        clock=lambda: datetime(2026, 8, 22, 10, 30, tzinfo=UTC),
    )

    bars = list(
        provider.fetch(
            BarRequest(
                symbol="aapl",
                interval=BarInterval.FIVE_MINUTES,
                start=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
                end=datetime(2026, 8, 22, 10, 15, tzinfo=UTC),
            )
        )
    )

    assert tokens == [None, "page-2"]
    assert [bar.open_timestamp.minute for bar in bars] == [0, 5, 10]
    assert {bar.feed for bar in bars} == {"iex"}
    assert bars[0].payload_hash == "de38fb0c9934106f57db3dca6ceb9a1ebe622a857e2e3df13658a6b075ee7d9f"


def test_alpaca_rate_limit_retries_are_bounded(monkeypatch):
    attempts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"message": "rate limit exceeded"})

    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    provider = AlpacaBarProvider(
        httpx.Client(transport=httpx.MockTransport(respond)),
        max_attempts=3,
        sleep=lambda seconds: None,
    )
    request = BarRequest(
        symbol="AAPL",
        interval=BarInterval.FIVE_MINUTES,
        start=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
        end=datetime(2026, 8, 22, 10, 5, tzinfo=UTC),
    )

    with pytest.raises(httpx.HTTPStatusError):
        list(provider.fetch(request))

    assert attempts == 3


def test_csv_provider_rejects_unfinalized_rows_and_preserves_explicit_revisions(tmp_path):
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume,vwap,trade_count,finalized,available_at,revision\n"
        "2026-08-22T10:00:00Z,10,12,9,11,100,10.5,20,true,2026-08-22T10:06:00Z,1\n"
        "2026-08-22T10:00:00Z,10,12,9,11.5,105,10.7,22,true,2026-08-22T10:07:00Z,2\n"
        "2026-08-22T10:05:00Z,11.5,13,11,12,50,12,9,false,2026-08-22T10:07:00Z,1\n",
        encoding="utf-8",
    )
    provider = CSVBarProvider(csv_path, provider="licensed_vendor", feed="consolidated")

    bars = list(
        provider.fetch(
            BarRequest(
                symbol="xyz",
                interval=BarInterval.FIVE_MINUTES,
                start=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
                end=datetime(2026, 8, 22, 10, 10, tzinfo=UTC),
            )
        )
    )

    assert [bar.revision for bar in bars] == [1, 2]
    assert {bar.provider for bar in bars} == {"licensed_vendor"}
    assert {bar.feed for bar in bars} == {"consolidated"}
    assert bars[0].payload_hash != bars[1].payload_hash


def test_bar_request_requires_explicit_utc_boundaries():
    with pytest.raises(ValueError, match="UTC"):
        BarRequest(
            symbol="AAPL",
            interval=BarInterval.FIVE_MINUTES,
            start=datetime(2026, 8, 22, 10, 0),
            end=datetime(2026, 8, 22, 10, 5),
        )


def test_bar_request_rejects_zero_offset_named_zone_instead_of_explicit_utc():
    with pytest.raises(ValueError, match="UTC"):
        BarRequest(
            symbol="AAPL",
            interval=BarInterval.FIVE_MINUTES,
            start=datetime(2026, 1, 22, 10, 0, tzinfo=ZoneInfo("Europe/London")),
            end=datetime(2026, 1, 22, 10, 5, tzinfo=ZoneInfo("Europe/London")),
        )


def test_finalized_market_bar_cannot_be_available_before_its_close():
    with pytest.raises(ValueError, match="available"):
        MarketBar(
            provider="alpaca",
            feed="iex",
            symbol="AAPL",
            interval=BarInterval.FIVE_MINUTES,
            open_timestamp=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
            close_timestamp=datetime(2026, 8, 22, 10, 5, tzinfo=UTC),
            available_at=datetime(2026, 8, 22, 10, 4, tzinfo=UTC),
            open=100,
            high=101,
            low=99,
            close=100.5,
            volume=10,
            payload_hash="a" * 64,
        )
