from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd


def revenue_yoy_log_growth(financials: pd.DataFrame) -> pd.Series:
    if (financials["revenue"] <= 0).any():
        raise ValueError("Revenue must be positive for log growth")
    ordered = financials.sort_values(["company_id", "period_end"])
    lagged = ordered.groupby("company_id", sort=False)["revenue"].shift(4)
    growth = np.log(ordered["revenue"] / lagged)
    return growth.reindex(financials.index)


def aggregate_attention_as_of(
    observations: pd.DataFrame,
    *,
    cutoff: date,
    trailing_days: int = 28,
) -> dict[str, float | date]:
    if trailing_days <= 0:
        raise ValueError("trailing_days must be positive")
    if observations.empty:
        return {
            "trailing_mean": math.nan,
            "trailing_max": math.nan,
            "momentum": math.nan,
            "yoy_growth": math.nan,
            "abnormal_zscore": math.nan,
            "insufficient_history": 1.0,
            "maximum_input_available_date": cutoff,
        }
    frame = observations.copy()
    frame["observation_date"] = pd.to_datetime(frame["observation_date"]).dt.date
    frame["available_date"] = pd.to_datetime(frame["available_date"]).dt.date
    eligible = frame[(frame["available_date"] <= cutoff) & (frame["observation_date"] <= cutoff)].copy()
    if eligible.empty:
        return {
            "trailing_mean": math.nan,
            "trailing_max": math.nan,
            "momentum": math.nan,
            "yoy_growth": math.nan,
            "abnormal_zscore": math.nan,
            "insufficient_history": 1.0,
            "maximum_input_available_date": cutoff,
        }
    trailing_start = cutoff - timedelta(days=trailing_days)
    prior_start = cutoff - timedelta(days=trailing_days * 2)
    trailing = eligible[eligible["observation_date"] >= trailing_start]
    prior = eligible[(eligible["observation_date"] >= prior_start) & (eligible["observation_date"] < trailing_start)]
    year_ago = eligible[
        (eligible["observation_date"] >= trailing_start - timedelta(days=365))
        & (eligible["observation_date"] <= cutoff - timedelta(days=365))
    ]
    trailing_mean = float(trailing["value"].mean()) if not trailing.empty else math.nan
    prior_mean = float(prior["value"].mean()) if not prior.empty else math.nan
    year_ago_mean = float(year_ago["value"].mean()) if not year_ago.empty else math.nan
    expanding_mean = float(eligible["value"].mean())
    expanding_std = float(eligible["value"].std(ddof=1))
    return {
        "trailing_mean": trailing_mean,
        "trailing_max": float(trailing["value"].max()) if not trailing.empty else math.nan,
        "momentum": (trailing_mean / prior_mean - 1) if prior_mean and not math.isnan(prior_mean) else math.nan,
        "yoy_growth": (trailing_mean / year_ago_mean - 1)
        if year_ago_mean and not math.isnan(year_ago_mean)
        else math.nan,
        "abnormal_zscore": (trailing_mean - expanding_mean) / expanding_std
        if expanding_std and not math.isnan(expanding_std)
        else math.nan,
        "insufficient_history": float(len(trailing) < max(2, trailing_days // 2)),
        "maximum_input_available_date": max(eligible["available_date"]),
    }
