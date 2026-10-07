from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.intraday.contracts import ConfirmedBar, InstrumentSpec, MarketQuote

T = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)


def instrument() -> InstrumentSpec:
    return InstrumentSpec(
        provider="oanda_practice",
        broker_symbol="DE30_EUR",
        market="germany40",
        product="cfd",
        quote_currency="EUR",
        point_value=Decimal("1"),
    )


def test_historical_bar_is_exploratory_and_available_only_after_close():
    bar = ConfirmedBar(
        instrument=instrument(),
        start=T,
        end=T + timedelta(minutes=5),
        available_at=T + timedelta(minutes=5, seconds=1),
        bid_open=Decimal("24000"),
        bid_high=Decimal("24006"),
        bid_low=Decimal("23999"),
        bid_close=Decimal("24004"),
        ask_open=Decimal("24002"),
        ask_high=Decimal("24008"),
        ask_low=Decimal("24001"),
        ask_close=Decimal("24006"),
        source_key="oanda:DE30_EUR:2026-10-06T08:00:00Z",
    )
    assert bar.price_scope == "historical_base"
    assert bar.available_at > bar.end


def test_future_or_invalid_historical_bar_is_rejected():
    common = dict(
        instrument=instrument(),
        start=T,
        end=T + timedelta(minutes=5),
        bid_open=Decimal("24000"),
        bid_high=Decimal("24006"),
        bid_low=Decimal("23999"),
        bid_close=Decimal("24004"),
        ask_open=Decimal("24002"),
        ask_high=Decimal("24008"),
        ask_low=Decimal("24001"),
        ask_close=Decimal("24006"),
        source_key="bar-1",
    )
    with pytest.raises(ValueError, match="available"):
        ConfirmedBar(**common, available_at=T + timedelta(minutes=4))
    with pytest.raises(ValueError, match="five-minute"):
        ConfirmedBar(**{**common, "end": T + timedelta(minutes=4)}, available_at=T + timedelta(minutes=5))
    with pytest.raises(ValueError, match="OHLC"):
        ConfirmedBar(**{**common, "bid_high": Decimal("24001")}, available_at=T + timedelta(minutes=5))


def test_account_quote_requires_executable_spread_and_identity():
    quote = MarketQuote(
        instrument=instrument(),
        account_feed_hash="a" * 64,
        observed_at=T,
        received_at=T + timedelta(milliseconds=200),
        bid=Decimal("24000"),
        ask=Decimal("24002"),
        status="tradeable",
        source_key="quote-1",
    )
    assert quote.spread == Decimal("2")
    with pytest.raises(ValueError, match="bid.*ask"):
        MarketQuote(**{**quote.model_dump(), "ask": Decimal("23999")})
    with pytest.raises(ValueError, match="received"):
        MarketQuote(**{**quote.model_dump(), "received_at": T - timedelta(seconds=1)})


def test_instrument_identity_disallows_unsupported_or_missing_broker_product():
    with pytest.raises(ValueError):
        InstrumentSpec(**{**instrument().model_dump(), "broker_symbol": ""})
    with pytest.raises(ValueError):
        InstrumentSpec(**{**instrument().model_dump(), "market": "bitcoin_perpetual"})
