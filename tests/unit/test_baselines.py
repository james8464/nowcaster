from __future__ import annotations

import math

import pandas as pd
import pytest

from src.models.baselines import historical_growth_forecast, seasonal_naive_forecast


def test_seasonal_naive_applies_only_eligible_recent_growth_adjustment():
    row = pd.Series({"revenue_year_ago": 100.0, "revenue_yoy_log_growth_lag1": math.log(1.1)})

    assert seasonal_naive_forecast(row) == pytest.approx(110.0)


def test_seasonal_naive_falls_back_to_year_ago_when_growth_missing():
    row = pd.Series({"revenue_year_ago": 100.0, "revenue_yoy_log_growth_lag1": float("nan")})

    assert seasonal_naive_forecast(row) == 100.0


def test_historical_growth_uses_recent_qoq_log_growth():
    row = pd.Series({"revenue_level_lag1": 120.0, "revenue_qoq_log_growth_lag1": math.log(1.05)})

    assert historical_growth_forecast(row) == pytest.approx(126.0)
