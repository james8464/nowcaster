from __future__ import annotations

import pandas as pd

from src.backtest.metrics import calculate_backtest_metrics
from src.backtest.portfolio import simulate_crypto_portfolio


def _positions() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=80, freq="7D")
    return pd.DataFrame(
        {
            "signal_id": [f"signal-{index}" for index in range(len(dates))],
            "symbol": "BTC-USD",
            "decision_date": dates.date,
            "execution_date": (dates + pd.Timedelta(days=1)).date,
            "label_end_date": (dates + pd.Timedelta(days=6)).date,
            "posture": ["long_research" if index % 3 else "short_research" for index in range(len(dates))],
            "realized_return": [0.02 if index % 3 else -0.01 for index in range(len(dates))],
            "forecast_volatility": 0.025,
        }
    )


def test_costs_and_one_bar_lag_reduce_crypto_return() -> None:
    result = simulate_crypto_portfolio(_positions(), fee_bps=10, slippage_bps=5, target_volatility=0.15)
    assert result.net_cumulative_return < result.gross_cumulative_return
    execution = pd.to_datetime(result.positions["execution_date"])
    decision = pd.to_datetime(result.positions["decision_date"])
    assert (execution > decision).all()


def test_metrics_cover_risk_return_and_execution() -> None:
    result = simulate_crypto_portfolio(_positions(), fee_bps=10, slippage_bps=5, target_volatility=0.15)
    metrics = calculate_backtest_metrics(result.curve, result.positions, periods_per_year=365)
    assert metrics.trades == 80
    assert metrics.maximum_drawdown <= 0
    assert metrics.profit_factor > 0
    assert metrics.average_holding_period_days == 5


def test_overlapping_positions_do_not_reuse_the_same_capital() -> None:
    positions = _positions().iloc[:8].copy()
    dates = pd.date_range("2024-01-01", periods=len(positions), freq="D")
    positions["decision_date"] = dates.date
    positions["execution_date"] = (dates + pd.Timedelta(days=1)).date
    positions["label_end_date"] = (dates + pd.Timedelta(days=6)).date
    result = simulate_crypto_portfolio(positions)
    assert len(result.positions) == 2
    ordered = result.positions.sort_values("execution_date")
    prior_exits = pd.to_datetime(ordered["label_end_date"]).shift(1)
    assert (pd.to_datetime(ordered["execution_date"])[1:] >= prior_exits[1:]).all()
