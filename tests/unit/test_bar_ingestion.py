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
from src.strategies.calendars import XNYS_CALENDAR
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
    assert bar.quote_volume == 800_500.125
    assert bar.taker_buy_base_volume == 6.1
    assert bar.taker_buy_quote_volume == 390_210.5
    assert bar.payload_hash == "5ecaf0e14ec23613558771976a2b3832bb3b16cd7afb7ed807334f27ec0371ec"


def test_rest_backfill_records_receipt_provenance_instead_of_claiming_historical_vintage() -> None:
    payload = _json_fixture("binance_klines.json")
    retrieved_at = datetime(2026, 8, 22, 10, 7, tzinfo=UTC)
    provider = BinanceBarProvider(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))),
        clock=lambda: retrieved_at,
    )

    bar = list(
        provider.fetch(
            BarRequest(
                symbol="BTCUSDT",
                interval=BarInterval.FIVE_MINUTES,
                start=datetime(2026, 8, 22, 10, tzinfo=UTC),
                end=datetime(2026, 8, 22, 10, 10, tzinfo=UTC),
            )
        )
    )[0]

    assert bar.source_available_at == datetime(2026, 8, 22, 10, 5, tzinfo=UTC)
    assert bar.observed_at == retrieved_at
    assert bar.available_at == retrieved_at
    assert bar.vintage_fidelity == "backfilled_rest_no_revision_history"


def test_binance_rejects_non_spot_provider_feed_configuration():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])))

    with pytest.raises(ValueError, match="spot"):
        BinanceBarProvider(client, feed="futures")


def test_binance_rejects_non_spot_request_feed_before_http_call():
    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError("unsupported feed must be rejected before an HTTP call")

    provider = BinanceBarProvider(httpx.Client(transport=httpx.MockTransport(unexpected_request)))
    request = BarRequest(
        symbol="BTCUSDT",
        interval=BarInterval.FIVE_MINUTES,
        start=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
        end=datetime(2026, 8, 22, 10, 5, tzinfo=UTC),
        feed="futures",
    )

    with pytest.raises(ValueError, match="spot"):
        list(provider.fetch(request))


def test_binance_receipt_timestamp_is_after_each_paginated_response() -> None:
    start = datetime(2026, 8, 22, 10, tzinfo=UTC)
    events: list[str] = []
    receipts = iter(
        (
            datetime(2026, 8, 22, 10, 20, tzinfo=UTC),
            datetime(2026, 8, 22, 10, 25, tzinfo=UTC),
        )
    )

    def row(index: int) -> list[object]:
        opened = start.timestamp() * 1_000 + index * 300_000
        return [
            int(opened),
            "100",
            "110",
            "99",
            str(101 + index),
            "10",
            int(opened + 299_999),
            "1000",
            10,
            "5",
            "500",
            "0",
        ]

    def respond(request: httpx.Request) -> httpx.Response:
        events.append("response")
        cursor = int(request.url.params["startTime"])
        payload = [row(0), row(1)] if cursor == int(start.timestamp() * 1_000) else [row(2)]
        return httpx.Response(200, json=payload)

    def clock() -> datetime:
        events.append("clock")
        return next(receipts)

    provider = BinanceBarProvider(httpx.Client(transport=httpx.MockTransport(respond)), clock=clock)
    bars = list(
        provider.fetch(
            BarRequest(
                symbol="BTCUSDT",
                interval=BarInterval.FIVE_MINUTES,
                start=start,
                end=datetime(2026, 8, 22, 10, 15, tzinfo=UTC),
                page_size=2,
            )
        )
    )

    assert events == ["response", "clock", "response", "clock"]
    assert [bar.retrieved_at.minute for bar in bars if bar.retrieved_at is not None] == [20, 20, 25]


def test_binance_verified_external_cache_resumes_without_network(tmp_path: Path) -> None:
    payload = _json_fixture("binance_klines.json")
    request = BarRequest(
        symbol="BTCUSDT",
        interval=BarInterval.FIVE_MINUTES,
        start=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
        end=datetime(2026, 8, 22, 10, 10, tzinfo=UTC),
    )

    def clock() -> datetime:
        return datetime(2026, 8, 22, 10, 7, tzinfo=UTC)

    first = BinanceBarProvider(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))),
        cache_dir=tmp_path,
        clock=clock,
    )
    expected = list(first.fetch(request))

    def unexpected_request(_: httpx.Request) -> httpx.Response:
        raise AssertionError("verified cache should satisfy the identical page request")

    resumed = BinanceBarProvider(
        httpx.Client(transport=httpx.MockTransport(unexpected_request)),
        cache_dir=tmp_path,
        clock=clock,
    )

    assert list(resumed.fetch(request)) == expected
    assert len(list(tmp_path.rglob("*.json"))) == 1
    assert len(list(tmp_path.rglob("*.sha256"))) == 1


def test_binance_external_cache_rejects_checksum_mismatch(tmp_path: Path) -> None:
    payload = _json_fixture("binance_klines.json")
    request = BarRequest(
        symbol="BTCUSDT",
        interval=BarInterval.FIVE_MINUTES,
        start=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
        end=datetime(2026, 8, 22, 10, 10, tzinfo=UTC),
    )
    provider = BinanceBarProvider(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))),
        cache_dir=tmp_path,
        clock=lambda: datetime(2026, 8, 22, 10, 7, tzinfo=UTC),
    )
    list(provider.fetch(request))
    next(tmp_path.rglob("*.json")).write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="checksum mismatch"):
        list(provider.fetch(request))


def test_xnys_calendar_matches_alpaca_buckets_dst_early_close_and_daily_labels() -> None:
    normal_dst = XNYS_CALENDAR.expected_opens(
        datetime(2026, 3, 9, tzinfo=UTC),
        datetime(2026, 3, 10, tzinfo=UTC),
        BarInterval.ONE_HOUR,
    )
    normal_standard = XNYS_CALENDAR.expected_opens(
        datetime(2026, 3, 6, tzinfo=UTC),
        datetime(2026, 3, 7, tzinfo=UTC),
        BarInterval.ONE_HOUR,
    )
    black_friday = XNYS_CALENDAR.expected_opens(
        datetime(2026, 11, 27, tzinfo=UTC),
        datetime(2026, 11, 28, tzinfo=UTC),
        BarInterval.FIVE_MINUTES,
    )
    daily = XNYS_CALENDAR.expected_opens(
        datetime(2026, 11, 27, tzinfo=UTC),
        datetime(2026, 11, 28, tzinfo=UTC),
        BarInterval.ONE_DAY,
    )

    assert normal_dst == tuple(datetime(2026, 3, 9, hour, tzinfo=UTC) for hour in range(13, 20))
    assert normal_standard == tuple(datetime(2026, 3, 6, hour, tzinfo=UTC) for hour in range(14, 21))
    assert len(black_friday) == 42
    assert black_friday[0] == datetime(2026, 11, 27, 14, 30, tzinfo=UTC)
    assert black_friday[-1] == datetime(2026, 11, 27, 17, 55, tzinfo=UTC)
    assert daily == (datetime(2026, 11, 27, 5, tzinfo=UTC),)
    assert XNYS_CALENDAR.close_for(daily[0], BarInterval.ONE_DAY) == datetime(2026, 11, 27, 18, tzinfo=UTC)
    assert XNYS_CALENDAR.version == "offline-rules-2026.3"


def test_xnys_new_year_keeps_friday_session_for_saturday_holiday_and_observes_sunday_on_monday() -> None:
    saturday_new_year_daily = XNYS_CALENDAR.expected_opens(
        datetime(2027, 12, 31, tzinfo=UTC),
        datetime(2028, 1, 1, tzinfo=UTC),
        BarInterval.ONE_DAY,
    )
    saturday_new_year_minutes = XNYS_CALENDAR.expected_opens(
        datetime(2027, 12, 31, tzinfo=UTC),
        datetime(2028, 1, 1, tzinfo=UTC),
        BarInterval.ONE_MINUTE,
    )
    sunday_new_year_observed = XNYS_CALENDAR.expected_opens(
        datetime(2023, 1, 2, tzinfo=UTC),
        datetime(2023, 1, 3, tzinfo=UTC),
        BarInterval.ONE_DAY,
    )

    assert saturday_new_year_daily == (datetime(2027, 12, 31, 5, tzinfo=UTC),)
    assert len(saturday_new_year_minutes) == 390
    assert saturday_new_year_minutes[0] == datetime(2027, 12, 31, 14, 30, tzinfo=UTC)
    assert saturday_new_year_minutes[-1] == datetime(2027, 12, 31, 20, 59, tzinfo=UTC)
    assert sunday_new_year_observed == ()


def test_alpaca_daily_bar_uses_session_close_instead_of_a_fixed_day(monkeypatch) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    payload = {
        "bars": [
            {
                "t": "2026-11-27T05:00:00Z",
                "o": 100,
                "h": 102,
                "l": 99,
                "c": 101,
                "v": 1000,
                "vw": 100.5,
                "n": 20,
            }
        ],
        "next_page_token": None,
    }
    provider = AlpacaBarProvider(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))),
        clock=lambda: datetime(2026, 11, 27, 18, 1, tzinfo=UTC),
    )

    bars = list(
        provider.fetch(
            BarRequest(
                symbol="AAPL",
                interval=BarInterval.ONE_DAY,
                start=datetime(2026, 11, 27, tzinfo=UTC),
                end=datetime(2026, 11, 28, tzinfo=UTC),
                feed="iex",
            )
        )
    )

    assert len(bars) == 1
    assert bars[0].open_timestamp == datetime(2026, 11, 27, 5, tzinfo=UTC)
    assert bars[0].close_timestamp == datetime(2026, 11, 27, 18, tzinfo=UTC)
    assert bars[0].source_available_at == datetime(2026, 11, 27, 18, tzinfo=UTC)
    assert bars[0].available_at == datetime(2026, 11, 27, 18, 1, tzinfo=UTC)


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
    assert [bar.available_at.minute for bar in bars] == [30, 30, 30]
    assert {bar.feed for bar in bars} == {"iex"}
    assert bars[0].payload_hash == "de38fb0c9934106f57db3dca6ceb9a1ebe622a857e2e3df13658a6b075ee7d9f"


def test_alpaca_receipt_timestamp_is_after_each_paginated_response(monkeypatch) -> None:
    pages = {
        None: _json_fixture("alpaca_bars_page_1.json"),
        "page-2": _json_fixture("alpaca_bars_page_2.json"),
    }
    events: list[str] = []
    receipts = iter(
        (
            datetime(2026, 8, 22, 10, 20, tzinfo=UTC),
            datetime(2026, 8, 22, 10, 25, tzinfo=UTC),
        )
    )

    def respond(request: httpx.Request) -> httpx.Response:
        events.append("response")
        return httpx.Response(200, json=pages[request.url.params.get("page_token")])

    def clock() -> datetime:
        events.append("clock")
        return next(receipts)

    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    provider = AlpacaBarProvider(httpx.Client(transport=httpx.MockTransport(respond)), clock=clock)
    bars = list(
        provider.fetch(
            BarRequest(
                symbol="AAPL",
                interval=BarInterval.FIVE_MINUTES,
                start=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
                end=datetime(2026, 8, 22, 10, 15, tzinfo=UTC),
            )
        )
    )

    assert events == ["response", "clock", "response", "clock"]
    assert [bar.retrieved_at.minute for bar in bars if bar.retrieved_at is not None] == [20, 20, 25]


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
