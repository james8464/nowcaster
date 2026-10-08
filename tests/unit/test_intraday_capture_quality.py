from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.intraday.capture_quality import summarize_capture
from tests.unit.test_intraday_strategies import INSTRUMENT

START = datetime(2026, 10, 8, 9, tzinfo=UTC)


def event(minute: int, *, bid: str = "24000", ask: str = "24002", tradeable: bool = True):
    observed = START + timedelta(minutes=minute)
    return {
        "kind": "quote",
        "at": observed.isoformat(),
        "payload": {
            "broker_symbol": INSTRUMENT.broker_symbol,
            "observed_at": observed.isoformat(),
            "bid": bid,
            "ask": ask,
            "tradeable": str(tradeable),
        },
    }


def test_live_capture_reports_missing_five_minute_intervals_and_spread_without_promoting():
    result = summarize_capture(INSTRUMENT, START, START + timedelta(minutes=20), (event(1), event(6)))
    assert result.expected_intervals == 4
    assert result.covered_intervals == 2
    assert result.tradeable_quote_count == 2
    assert result.coverage == Decimal("0.5")
    assert result.median_spread == Decimal("2")
    assert result.p95_spread == Decimal("2")
    assert result.price_scope == "account_stream_observation"
    assert result.paper_eligible is False


def test_live_capture_excludes_nontradeable_and_other_product_quotes():
    foreign = event(2)
    foreign["payload"]["broker_symbol"] = "SPX500_USD"
    result = summarize_capture(
        INSTRUMENT, START, START + timedelta(minutes=10), (event(1, tradeable=False), foreign, event(6))
    )
    assert result.expected_intervals == 2
    assert result.covered_intervals == 1
    assert result.quote_count == 2
    assert result.tradeable_quote_count == 1


def test_live_capture_separates_out_of_window_quotes_from_invalid_quotes():
    after_close = event(11)
    invalid = event(1, bid="24005", ask="24002")
    result = summarize_capture(INSTRUMENT, START, START + timedelta(minutes=10), (after_close, invalid))
    assert result.covered_intervals == 0
    assert result.quote_count == 2
    assert result.out_of_window_quote_count == 1
    assert result.invalid_quote_count == 1
    assert result.median_spread is None


def test_live_capture_does_not_use_after_close_spread_or_count_as_tradeable():
    result = summarize_capture(
        INSTRUMENT,
        START,
        START + timedelta(minutes=10),
        (event(1, bid="24000", ask="24002"), event(11, bid="24000", ask="24020")),
    )
    assert result.quote_count == 2
    assert result.tradeable_quote_count == 1
    assert result.out_of_window_quote_count == 1
    assert result.invalid_quote_count == 0
    assert result.median_spread == Decimal("2")
