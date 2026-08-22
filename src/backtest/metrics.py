from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from src.backtest.portfolio import maximum_drawdown


@dataclass(frozen=True)
class BacktestMetrics:
    cumulative_return: float
    cagr: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    sortino: float
    calmar: float
    maximum_drawdown: float
    hit_rate: float
    profit_factor: float
    turnover: float
    average_gross_exposure: float
    trades: int
    average_holding_period_days: float

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _safe_ratio(numerator: float, denominator: float) -> float:
    if not np.isfinite(denominator) or denominator == 0:
        return float("nan")
    return float(numerator / denominator)


def calculate_backtest_metrics(
    curve: pd.DataFrame,
    positions: pd.DataFrame,
    *,
    periods_per_year: int,
) -> BacktestMetrics:
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    returns = pd.to_numeric(curve.get("net_return", pd.Series(dtype=float)), errors="coerce").dropna()
    if returns.empty:
        return BacktestMetrics(*([float("nan")] * 12), trades=0, average_holding_period_days=float("nan"))
    wealth = (1 + returns).cumprod()
    cumulative = float(wealth.iloc[-1] - 1)
    years = len(returns) / periods_per_year
    cagr = float(wealth.iloc[-1] ** (1 / years) - 1) if years > 0 and wealth.iloc[-1] > 0 else float("nan")
    annual_return = float(returns.mean() * periods_per_year)
    annual_volatility = float(returns.std(ddof=1) * np.sqrt(periods_per_year))
    downside = returns[returns < 0]
    downside_deviation = (
        float(np.sqrt(np.mean(np.square(downside))) * np.sqrt(periods_per_year)) if len(downside) else 0
    )
    drawdown = maximum_drawdown(returns)
    profit = float(returns[returns > 0].sum())
    loss = float(abs(returns[returns < 0].sum()))
    holding = (
        pd.to_datetime(positions["label_end_date"]) - pd.to_datetime(positions["execution_date"])
        if not positions.empty
        else pd.Series(dtype="timedelta64[ns]")
    )
    turnover = float(pd.to_numeric(positions.get("turnover", pd.Series(dtype=float)), errors="coerce").sum())
    exposure = float(
        pd.to_numeric(positions.get("gross_exposure", pd.Series(dtype=float)), errors="coerce").mean()
    )
    return BacktestMetrics(
        cumulative_return=cumulative,
        cagr=cagr,
        annualized_return=annual_return,
        annualized_volatility=annual_volatility,
        sharpe=_safe_ratio(annual_return, annual_volatility),
        sortino=_safe_ratio(annual_return, downside_deviation),
        calmar=_safe_ratio(cagr, abs(drawdown)),
        maximum_drawdown=drawdown,
        hit_rate=float((returns > 0).mean()),
        profit_factor=float("inf") if profit > 0 and loss == 0 else _safe_ratio(profit, loss),
        turnover=turnover,
        average_gross_exposure=exposure,
        trades=len(positions),
        average_holding_period_days=float(holding.dt.total_seconds().div(86_400).mean()),
    )
