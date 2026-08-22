from __future__ import annotations

from datetime import date

import pandas as pd

from src.backtest.portfolio import maximum_drawdown, run_event_portfolio


def _event_returns() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "signal_id": "a",
                "company_id": "SBUX",
                "event_date": date(2024, 1, 1),
                "variant_zscore": 2.0,
                "abnormal_return": 0.04,
                "liquidity_status": "eligible",
            },
            {
                "signal_id": "b",
                "company_id": "MCD",
                "event_date": date(2024, 1, 1),
                "variant_zscore": -2.0,
                "abnormal_return": -0.02,
                "liquidity_status": "eligible",
            },
            {
                "signal_id": "c",
                "company_id": "COST",
                "event_date": date(2024, 2, 1),
                "variant_zscore": 1.5,
                "abnormal_return": -0.01,
                "liquidity_status": "eligible",
            },
            {
                "signal_id": "d",
                "company_id": "MCD",
                "event_date": date(2024, 2, 1),
                "variant_zscore": -1.5,
                "abnormal_return": 0.01,
                "liquidity_status": "eligible",
            },
        ]
    )


def test_round_trip_costs_reduce_long_short_return():
    gross = run_event_portfolio(_event_returns(), transaction_cost_bps=0, slippage_bps=0)
    net = run_event_portfolio(_event_returns(), transaction_cost_bps=10, slippage_bps=5)

    assert net.cumulative_return < gross.cumulative_return
    assert net.event_returns.iloc[0].net_return < net.event_returns.iloc[0].gross_return


def test_portfolio_deduplicates_overlapping_company_event_and_caps_weights():
    frame = pd.concat([_event_returns(), _event_returns().iloc[[0]]], ignore_index=True)
    result = run_event_portfolio(frame, maximum_position_weight=0.5)

    assert result.overlap_rows_removed == 1
    assert result.positions["weight"].abs().max() <= 0.5


def test_maximum_drawdown_uses_compounded_wealth():
    assert maximum_drawdown(pd.Series([0.1, -0.2, 0.05])) < -0.19
