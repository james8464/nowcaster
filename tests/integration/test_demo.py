from __future__ import annotations


def test_demo_builds_required_research_tables_from_truthfully_labeled_snapshots(demo_database):
    settings, database = demo_database
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
    assert set(database.frame("select distinct horizon_days from forecasts")["horizon_days"]) == set(
        settings.model.forecast_horizons
    )
    assert (
        database.scalar(
            "select count(*) from features_quarterly where maximum_input_available_date > forecast_cutoff_date"
        )
        == 0
    )
    assert database.scalar("select count(*) from model_runs where training_end >= test_start") == 0
    assert database.scalar("select count(*) from variant_signals") == database.scalar("select count(*) from forecasts")
    assert database.scalar("select count(*) from backtest_results") == 4 * database.scalar(
        "select count(*) from variant_signals"
    )
