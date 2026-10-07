from datetime import timedelta
from decimal import Decimal

from src.intraday.quality import assess_market_feasibility
from tests.unit.test_intraday_strategies import INSTRUMENT, T, bar, quote


def test_feasibility_counts_missing_bars_and_unobserved_account_minutes():
    slots = tuple(T + timedelta(minutes=5 * n) for n in range(4))
    bars = [bar(0, 24000), bar(1, 24001), bar(3, 24003)]
    quotes = [quote(1, 24000), quote(2, 24001), quote(4, 24003)]
    report = assess_market_feasibility(INSTRUMENT, slots, bars, quotes)
    assert report.historical_bar_coverage == Decimal("0.75")
    assert report.account_quote_coverage == Decimal("0.75")
    assert "historical_gap" in report.reasons
    assert "account_feed_gap" in report.reasons
    assert not report.eligible


def test_feasibility_requires_account_quote_identity_and_reports_spread():
    slots = tuple(T + timedelta(minutes=5 * n) for n in range(2))
    bars = [bar(0, 24000), bar(1, 24001)]
    quotes = [quote(1, 24000), quote(2, 24001)]
    report = assess_market_feasibility(INSTRUMENT, slots, bars, quotes)
    assert report.eligible
    assert report.median_spread == Decimal("2")
    altered = quotes[0].model_copy(update={"account_feed_hash": "c" * 64})
    assert not assess_market_feasibility(INSTRUMENT, slots, bars, [altered, quotes[1]]).eligible
