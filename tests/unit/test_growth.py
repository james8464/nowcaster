from __future__ import annotations

import math

import pandas as pd
import pytest

from src.features.aggregation import revenue_yoy_log_growth


def test_revenue_yoy_log_growth_is_company_specific_and_uses_four_quarter_lag():
    frame = pd.DataFrame(
        {
            "company_id": ["A"] * 5 + ["B"] * 5,
            "period_end": pd.date_range("2023-03-31", periods=5, freq="QE").tolist() * 2,
            "revenue": [100, 110, 120, 130, 121, 200, 210, 220, 230, 180],
        }
    )

    growth = revenue_yoy_log_growth(frame)

    assert math.isnan(growth.iloc[3])
    assert growth.iloc[4] == pytest.approx(math.log(1.21))
    assert growth.iloc[9] == pytest.approx(math.log(0.9))


def test_revenue_growth_rejects_non_positive_values():
    frame = pd.DataFrame(
        {
            "company_id": ["A"] * 5,
            "period_end": pd.date_range("2023-03-31", periods=5, freq="QE"),
            "revenue": [100, 110, 0, 130, 121],
        }
    )

    with pytest.raises(ValueError, match="positive"):
        revenue_yoy_log_growth(frame)
