from __future__ import annotations

from datetime import date
from pathlib import Path

from src.database.engine import Database
from src.ingestion.earnings import filing_event_proxy_rows, load_earnings_calendar
from src.ingestion.prices import CsvPriceProvider, price_rows


def test_price_and_earnings_rows_persist_with_source_metadata(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures" / "prices" / "sample_prices.csv"
    calendar = tmp_path / "earnings.csv"
    calendar.write_text(
        "ticker,fiscal_quarter,earnings_date,earnings_time,timing_confidence,available_date,source\n"
        "SBUX,2024Q1,2024-02-05,after_close,confirmed,2024-01-20,manual_test\n",
        encoding="utf-8",
    )
    database = Database.from_url(f"duckdb:///{tmp_path / 'market.duckdb'}")
    database.initialize()

    prices = CsvPriceProvider({"SBUX": fixture}).fetch("SBUX", date(2024, 2, 2), date(2024, 2, 8))
    price_count = database.upsert("market_prices_daily", price_rows(prices, source="fixture"))
    event_count = database.upsert("earnings_calendar", load_earnings_calendar(calendar))

    assert price_count == 5
    assert event_count == 1
    assert database.scalar("select adjustment_status from market_prices_daily limit 1") == "provider_adjusted"


def test_filing_event_proxy_is_permanently_labeled_as_proxy():
    rows = filing_event_proxy_rows(
        [{"company_id": "SBUX", "fiscal_quarter": "2024Q1", "available_date": date(2024, 1, 31)}]
    )

    assert rows[0]["earnings_date"] == date(2024, 1, 31)
    assert rows[0]["timing_confidence"] == "sec_filing_date_proxy"
    assert rows[0]["source"] == "sec_filing_event_proxy"
