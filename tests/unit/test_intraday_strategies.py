from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.intraday.contracts import ConfirmedBar, InstrumentSpec, MarketQuote
from src.intraday.strategies import evaluate_setup

T = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
INSTRUMENT = InstrumentSpec(
    provider="oanda_practice",
    broker_symbol="DE30_EUR",
    market="germany40",
    product="cfd",
    quote_currency="EUR",
    point_value=Decimal("1"),
)


def bar(n, close, *, high=None, low=None):
    close = Decimal(str(close))
    high = Decimal(str(high)) if high is not None else close + 1
    low = Decimal(str(low)) if low is not None else close - 1
    start = T + timedelta(minutes=5 * n)
    return ConfirmedBar(
        instrument=INSTRUMENT,
        start=start,
        end=start + timedelta(minutes=5),
        available_at=start + timedelta(minutes=5),
        bid_open=close,
        bid_high=high,
        bid_low=low,
        bid_close=close,
        ask_open=close + 2,
        ask_high=high + 2,
        ask_low=low + 2,
        ask_close=close + 2,
        source_key=f"bar-{n}",
    )


def quote(n, bid, *, seconds=1):
    at = T + timedelta(minutes=5 * n, seconds=seconds)
    return MarketQuote(
        instrument=INSTRUMENT,
        account_feed_hash="a" * 64,
        observed_at=at,
        received_at=at + timedelta(milliseconds=100),
        bid=Decimal(str(bid)),
        ask=Decimal(str(bid)) + 2,
        status="tradeable",
        source_key=f"quote-{n}",
    )


def test_opening_range_long_uses_confirmed_breakout_and_next_quote():
    bars = [
        bar(0, 100, high=101, low=99),
        bar(1, 100, high=102, low=99),
        bar(2, 101, high=102, low=100),
        bar(3, 104, high=105, low=101),
    ]
    result = evaluate_setup(
        "opening_range_15", bars, quote(4, 104), session_open=T, session_close=T + timedelta(hours=8)
    )
    assert result.status == "ready"
    assert result.direction == "long"
    assert result.entry == Decimal("106")
    assert result.stop < result.entry < result.target
    assert result.decision_at == bars[-1].end
    assert result.entry_at > result.decision_at


def test_opening_range_short_mirrors_levels():
    bars = [
        bar(0, 100, high=101, low=99),
        bar(1, 100, high=102, low=99),
        bar(2, 101, high=102, low=100),
        bar(3, 97, high=101, low=96),
    ]
    result = evaluate_setup(
        "opening_range_15", bars, quote(4, 97), session_open=T, session_close=T + timedelta(hours=8)
    )
    assert result.status == "ready"
    assert result.direction == "short"
    assert result.entry == Decimal("97")
    assert result.target < result.entry < result.stop


def test_unconfirmed_future_or_stale_quote_cannot_create_trade():
    bars = [bar(0, 100), bar(1, 100), bar(2, 100), bar(3, 105)]
    stale = quote(5, 105)
    assert (
        evaluate_setup("opening_range_15", bars, stale, session_open=T, session_close=T + timedelta(hours=8)).status
        == "no_trade"
    )
    future = bars[-1].model_copy(update={"available_at": bars[-1].end + timedelta(seconds=30)})
    assert (
        evaluate_setup(
            "opening_range_15",
            [*bars[:-1], future],
            quote(4, 105),
            session_open=T,
            session_close=T + timedelta(hours=8),
        ).status
        == "no_trade"
    )


def test_range_reversion_can_propose_a_long_in_choppy_market():
    bars = [bar(n, 100 if n % 2 == 0 else 101) for n in range(20)]
    bars.append(bar(20, 97, high=99, low=96))
    result = evaluate_setup(
        "range_reversion", bars, quote(21, 97), session_open=T, session_close=T + timedelta(hours=8)
    )
    assert result.status == "ready"
    assert result.direction == "long"
    assert result.target > result.entry > result.stop


def test_range_reversion_abstains_if_next_quote_has_already_crossed_target():
    bars = [bar(n, 100 if n % 2 == 0 else 101) for n in range(20)]
    bars.append(bar(20, 97, high=99, low=96))
    result = evaluate_setup(
        "range_reversion", bars, quote(21, 101), session_open=T, session_close=T + timedelta(hours=8)
    )
    assert result.status == "no_trade"
    assert result.reason == "invalid_levels"


def test_trend_pullback_requires_confirmed_reclaim_not_future_bar():
    bars = [bar(n, 100 + n * 0.2) for n in range(60)]
    bars.append(bar(60, 109, high=110, low=108))
    bars.append(bar(61, 113, high=114, low=109))
    result = evaluate_setup(
        "trend_pullback", bars, quote(62, 113), session_open=T, session_close=T + timedelta(hours=8)
    )
    assert result.status == "ready"
    assert result.direction == "long"
    assert (
        evaluate_setup(
            "trend_pullback", bars[:-1], quote(61, 109), session_open=T, session_close=T + timedelta(hours=8)
        ).status
        == "no_trade"
    )
