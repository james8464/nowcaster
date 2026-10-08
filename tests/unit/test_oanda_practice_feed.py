import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from src.intraday.contracts import InstrumentSpec
from src.intraday.oanda_practice import OandaPracticeFeed


def test_margin_fx_identity_is_not_misreported_as_cfd():
    instrument = InstrumentSpec(
        provider="oanda_practice",
        broker_symbol="EUR_USD",
        market="eurusd",
        product="margin_fx",
        quote_currency="USD",
        point_value=Decimal("1"),
    )

    def handler(request):
        assert request.url.path.endswith("/instruments")
        return httpx.Response(200, json={"instruments": [{"name": "EUR_USD", "type": "CURRENCY"}]})

    feed = OandaPracticeFeed("demo-account", "demo-token", transport=httpx.MockTransport(handler))
    assert feed.verify_instrument(instrument)["type"] == "CURRENCY"


def test_modern_tradeable_flag_is_respected_without_deprecated_status():
    event = {
        "type": "PRICE",
        "instrument": INSTRUMENT.broker_symbol,
        "time": "2026-10-06T08:00:00Z",
        "tradeable": True,
        "bids": [{"price": "24000"}],
        "asks": [{"price": "24002"}],
    }
    feed = OandaPracticeFeed("practice-account", "dummy-token")
    quote = feed.parse_price_event(INSTRUMENT, json.dumps(event), received_at=T + timedelta(milliseconds=100))
    assert quote.status == "tradeable"


T = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
INSTRUMENT = InstrumentSpec(
    provider="oanda_practice",
    broker_symbol="DE30_EUR",
    market="germany40",
    product="cfd",
    quote_currency="EUR",
    point_value=Decimal("1"),
)


def test_practice_feed_parses_only_completed_bid_ask_candles():
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "candles": [
                    {
                        "time": "2026-10-06T08:00:00Z",
                        "complete": True,
                        "bid": {"o": "24000", "h": "24006", "l": "23999", "c": "24004"},
                        "ask": {"o": "24002", "h": "24008", "l": "24001", "c": "24006"},
                    },
                    {
                        "time": "2026-10-06T08:05:00Z",
                        "complete": False,
                        "bid": {"o": "24004", "h": "24005", "l": "24002", "c": "24003"},
                        "ask": {"o": "24006", "h": "24007", "l": "24004", "c": "24005"},
                    },
                ]
            },
        )

    feed = OandaPracticeFeed("practice-account", "dummy-token", transport=httpx.MockTransport(reply))
    bars = feed.fetch_candles(INSTRUMENT, T, T + timedelta(minutes=10), received_at=T + timedelta(minutes=11))
    assert len(bars) == 1
    assert bars[0].bid_close == Decimal("24004")
    assert bars[0].available_at == T + timedelta(minutes=11)
    assert bars[0].price_scope == "historical_base"
    assert calls[0].url.host == "api-fxpractice.oanda.com"
    assert calls[0].url.params["price"] == "BA"
    assert calls[0].url.params["granularity"] == "M5"
    assert calls[0].headers["authorization"] == "Bearer dummy-token"


def test_candle_fetch_skips_only_provider_overlap_before_unaligned_start():
    def candle(at: str) -> dict:
        return {
            "time": at,
            "complete": True,
            "bid": {"o": "24000", "h": "24002", "l": "23999", "c": "24001"},
            "ask": {"o": "24003", "h": "24005", "l": "24002", "c": "24004"},
        }

    def reply(request):
        return httpx.Response(
            200,
            json={
                "candles": [
                    candle("2026-10-06T07:55:00Z"),
                    candle("2026-10-06T08:00:00Z"),
                ]
            },
        )

    feed = OandaPracticeFeed("practice-account", "dummy-token", transport=httpx.MockTransport(reply))
    bars = feed.fetch_candles(
        INSTRUMENT,
        T - timedelta(seconds=1),
        T + timedelta(minutes=5),
        received_at=T + timedelta(minutes=6),
    )
    assert [bar.start for bar in bars] == [T]


def test_practice_feed_parses_account_quote_and_ignores_heartbeat():
    feed = OandaPracticeFeed(
        "practice-account", "dummy-token", transport=httpx.MockTransport(lambda _: httpx.Response(200))
    )
    line = json.dumps(
        {
            "type": "PRICE",
            "instrument": "DE30_EUR",
            "time": "2026-10-06T08:00:00Z",
            "status": "tradeable",
            "bids": [{"price": "24000"}],
            "asks": [{"price": "24002"}],
        }
    )
    quote = feed.parse_price_event(INSTRUMENT, line, received_at=T + timedelta(milliseconds=100))
    assert quote.bid == Decimal("24000")
    assert quote.ask == Decimal("24002")
    assert quote.account_feed_hash == hashlib.sha256(b"practice-account").hexdigest()
    assert feed.parse_price_event(INSTRUMENT, '{"type":"HEARTBEAT"}', received_at=T) is None


def test_practice_feed_rejects_mismatched_product_or_missing_token():
    with pytest.raises(ValueError, match="token"):
        OandaPracticeFeed("practice-account", "")
    feed = OandaPracticeFeed(
        "practice-account", "dummy-token", transport=httpx.MockTransport(lambda _: httpx.Response(200))
    )
    with pytest.raises(ValueError, match="instrument"):
        feed.parse_price_event(INSTRUMENT, '{"type":"PRICE","instrument":"SPX500_USD"}', received_at=T)


def test_broker_product_must_be_discovered_in_the_demo_account():
    def reply(request):
        assert request.url.path == "/v3/accounts/practice-account/instruments"
        return httpx.Response(
            200,
            json={
                "instruments": [
                    {"name": "DE30_EUR", "type": "CFD", "displayName": "Germany 40"},
                ]
            },
        )

    feed = OandaPracticeFeed("practice-account", "dummy-token", transport=httpx.MockTransport(reply))
    assert feed.verify_instrument(INSTRUMENT)["displayName"] == "Germany 40"
    with pytest.raises(ValueError, match="not available"):
        feed.verify_instrument(INSTRUMENT.model_copy(update={"broker_symbol": "SPX500_USD"}))


def test_account_home_conversion_snapshot_uses_practice_read_routes_and_exact_factors():
    requests = []

    def reply(request):
        requests.append(request)
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json={"account": {"currency": "GBP"}})
        assert request.url.path.endswith("/pricing")
        assert request.url.params["includeHomeConversions"] == "true"
        assert request.url.params["instruments"] == "DE30_EUR"
        return httpx.Response(200, json={
            "time": "2026-10-08T18:25:26.391201013Z",
            "prices": [{"instrument": "DE30_EUR"}],
            "homeConversions": [
                {"currency": "DE30", "accountGain": "20934.936", "accountLoss": "21357.864",
                 "positionValue": "21146.4"},
                {"currency": "EUR", "accountGain": "0.8389656", "accountLoss": "0.8559144",
                 "positionValue": "0.84744"},
            ],
        })

    feed = OandaPracticeFeed("practice-account", "dummy-token", transport=httpx.MockTransport(reply))
    rates = feed.home_conversions((INSTRUMENT,), account_currency="GBP")
    assert len(rates) == 1
    assert rates[0].currency == "EUR"
    assert rates[0].position_value == Decimal("0.84744")
    assert rates[0].account_gain == Decimal("0.8389656")
    assert rates[0].account_loss == Decimal("0.8559144")
    assert rates[0].observed_at == datetime(2026, 10, 8, 18, 25, 26, 391201, tzinfo=UTC)
    paper = rates[0].to_paper_conversion(account_currency="GBP")
    assert paper.from_currency == "EUR"
    assert paper.to_currency == "GBP"
    assert paper.position_value == Decimal("0.84744")
    assert paper.account_gain == Decimal("0.8389656")
    assert paper.account_loss == Decimal("0.8559144")
    assert all(request.url.host == "api-fxpractice.oanda.com" for request in requests)


@pytest.mark.parametrize("bad_conversion", [
    {"currency": "EUR", "accountGain": "0", "accountLoss": "0.85", "positionValue": "0.84"},
    {"currency": "EUR", "accountGain": "0.86", "accountLoss": "0.83", "positionValue": "0.84"},
    {"currency": "EUR", "accountGain": "NaN", "accountLoss": "0.85", "positionValue": "0.84"},
])
def test_account_home_conversion_snapshot_rejects_invalid_factors(bad_conversion):
    def reply(request):
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json={"account": {"currency": "GBP"}})
        return httpx.Response(200, json={"time": "2026-10-08T18:25:26Z", "homeConversions": [bad_conversion]})

    feed = OandaPracticeFeed("practice-account", "dummy-token", transport=httpx.MockTransport(reply))
    with pytest.raises(ValueError, match="conversion"):
        feed.home_conversions((INSTRUMENT,), account_currency="GBP")


def test_account_home_conversion_snapshot_rejects_wrong_account_currency_or_missing_quote_currency():
    def reply(request):
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json={"account": {"currency": "USD"}})
        return httpx.Response(200, json={"time": "2026-10-08T18:25:26Z", "homeConversions": []})

    feed = OandaPracticeFeed("practice-account", "dummy-token", transport=httpx.MockTransport(reply))
    with pytest.raises(ValueError, match="account currency"):
        feed.home_conversions((INSTRUMENT,), account_currency="GBP")

    def missing_quote(request):
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json={"account": {"currency": "GBP"}})
        return httpx.Response(200, json={"time": "2026-10-08T18:25:26Z", "homeConversions": []})

    no_rate = OandaPracticeFeed("practice-account", "dummy-token", transport=httpx.MockTransport(missing_quote))
    with pytest.raises(ValueError, match="conversion missing"):
        no_rate.home_conversions((INSTRUMENT,), account_currency="GBP")
