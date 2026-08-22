from __future__ import annotations

import math

import pandas as pd


def seasonal_naive_forecast(row: pd.Series) -> float:
    year_ago = float(row["revenue_year_ago"])
    growth = row.get("revenue_yoy_log_growth_lag1", math.nan)
    return year_ago if pd.isna(growth) else year_ago * math.exp(float(growth))


def historical_growth_forecast(row: pd.Series) -> float:
    latest = float(row["revenue_level_lag1"])
    growth = row.get("revenue_qoq_log_growth_lag1", math.nan)
    return latest if pd.isna(growth) else latest * math.exp(float(growth))
