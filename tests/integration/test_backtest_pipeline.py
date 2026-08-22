from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.backtest.event_study import run_event_study
from src.backtest.portfolio import run_event_portfolio


def test_signal_event_study_flows_into_market_neutral_portfolio():
    days = [date(2024, 1, 1) + timedelta(days=index) for index in range(6)]
    prices = pd.DataFrame(
        [
            {"symbol": symbol, "trading_date": day, "adjusted_close": values[index]}
            for symbol, values in {
                "SBUX": [100, 101, 103, 104, 105, 106],
                "MCD": [100, 99, 98, 97, 96, 95],
                "SPY": [100, 100, 100, 100, 100, 100],
            }.items()
            for index, day in enumerate(days)
        ]
    )
    signals = pd.DataFrame(
        [
            {
                "signal_id": "s1",
                "company_id": "SBUX",
                "event_date": date(2024, 1, 2),
                "variant": 0.1,
                "variant_zscore": 1.5,
                "variant_bucket": "strongly_positive",
            },
            {
                "signal_id": "s2",
                "company_id": "MCD",
                "event_date": date(2024, 1, 2),
                "variant": -0.1,
                "variant_zscore": -1.5,
                "variant_bucket": "strongly_negative",
            },
        ]
    )

    study = run_event_study(signals, prices, [(0, 3)], {"market": "SPY"}, bootstrap_samples=100, seed=42)
    portfolio = run_event_portfolio(study.event_returns, transaction_cost_bps=10)

    assert len(study.event_returns) == 2
    assert portfolio.cumulative_return > 0
    assert portfolio.event_returns.iloc[0].net_return < portfolio.event_returns.iloc[0].gross_return


def test_demo_crypto_backtests_preserve_final_test_and_cost_sensitivity(demo_database):
    _, database = demo_database
    runs = database.frame("select * from backtest_runs where asset_class = 'crypto'")
    assert set(runs["symbol"]) == {"BTC-USD", "ETH-USD"}
    assert set(runs["readiness"]) <= {"decision_ready", "research_only", "not_ready"}
    assert (pd.to_datetime(runs["development_end"]) < pd.to_datetime(runs["final_test_start"])).all()
    assert database.scalar("select count(*) from backtest_curve where phase = 'final_test'") > 0
    assert database.scalar("select count(*) from backtest_sensitivity") == 3 * len(runs)
    assert database.scalar("select count(*) from backtest_positions where execution_date <= decision_date") == 0
    assert (
        database.scalar(
            """
            select count(*)
            from backtest_positions positions
            join backtest_runs runs using (backtest_run_id)
            where positions.phase = 'development'
              and positions.label_end_date >= runs.final_test_start
            """
        )
        == 0
    )
