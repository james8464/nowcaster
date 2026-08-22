from __future__ import annotations

from pathlib import Path

from src.config.settings import Settings
from src.database.engine import Database
from src.demo import run_demo


def test_demo_builds_required_research_tables_from_truthfully_labeled_snapshots(tmp_path):
    root = Path(__file__).resolve().parents[2]
    settings = Settings.load(root, mode="demo").model_copy(
        update={"database_url": f"duckdb:///{tmp_path / 'demo.duckdb'}"}
    )

    summary = run_demo(settings)

    assert not summary.failed
    database = Database.from_url(settings.database_url)
    required_nonempty = {
        "companies",
        "financials_quarterly",
        "earnings_calendar",
        "market_prices_daily",
        "alternative_data_daily",
        "features_quarterly",
        "forecasts",
        "consensus_estimates",
        "variant_signals",
        "backtest_results",
    }
    assert all(database.scalar(f"select count(*) from {table}") > 0 for table in required_nonempty)
    source_tables = [
        "financials_quarterly",
        "earnings_calendar",
        "market_prices_daily",
        "alternative_data_daily",
        "features_quarterly",
        "forecasts",
        "consensus_estimates",
        "variant_signals",
        "backtest_results",
    ]
    sources = {
        source for table in source_tables for source in database.frame(f"select distinct source from {table}")["source"]
    }
    assert not any("synthetic" in source.lower() or "fake" in source.lower() for source in sources)
    assert database.scalar("select count(*) from consensus_estimates where mode = 'expectation_proxy'") > 0
    assert (
        database.scalar("select count(*) from earnings_calendar where timing_confidence = 'sec_filing_date_proxy'") > 0
    )
