from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from src.intraday.contracts import InstrumentSpec, MarketQuote
from src.intraday.live import LiveBarBuilder

T = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
INSTRUMENT = InstrumentSpec(
    provider="oanda_practice",
    broker_symbol="DE30_EUR",
    market="germany40",
    product="cfd",
    quote_currency="EUR",
    point_value=D(1),
)


def quote(seconds: int, bid: str, *, feed="a" * 64) -> MarketQuote:
    at = T + timedelta(seconds=seconds)
    return MarketQuote(
        instrument=INSTRUMENT,
        account_feed_hash=feed,
        observed_at=at,
        received_at=at + timedelta(milliseconds=100),
        bid=D(bid),
        ask=D(bid) + 2,
        status="tradeable",
        source_key=f"quote:{seconds}",
    )


def test_completed_live_bar_uses_only_prior_account_quotes():
    builder = LiveBarBuilder(INSTRUMENT, maximum_quote_gap=timedelta(seconds=180))
    assert builder.accept(quote(1, "100")) is None
    assert builder.accept(quote(120, "104")) is None
    assert builder.accept(quote(299, "102")) is None
    closed = builder.accept(quote(301, "108"))
    assert closed is not None
    assert closed.price_scope == "account_stream"
    assert closed.start == T
    assert closed.end == T + timedelta(minutes=5)
    assert closed.available_at == T + timedelta(seconds=301, milliseconds=100)
    assert closed.bid_open == D(100)
    assert closed.bid_high == D(104)
    assert closed.bid_low == D(100)
    assert closed.bid_close == D(102)


def test_gap_or_feed_identity_change_drops_bar_instead_of_backfilling():
    builder = LiveBarBuilder(INSTRUMENT, maximum_quote_gap=timedelta(seconds=30))
    assert builder.accept(quote(1, "100")) is None
    assert builder.accept(quote(120, "104")) is None
    assert builder.accept(quote(301, "108")) is None
    assert builder.gaps == 1
    assert builder.accept(quote(302, "109", feed="b" * 64)) is None
    assert builder.gaps == 2
