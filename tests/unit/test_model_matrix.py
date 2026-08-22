from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from src.models.matrix import build_model_matrix


def test_model_matrix_pivots_features_and_joins_actual_target_without_losing_cutoff_audit():
    feature_rows = pd.DataFrame(
        [
            {
                "company_id": "SBUX",
                "fiscal_quarter": "2024Q1",
                "earnings_date": date(2024, 2, 1),
                "forecast_cutoff_date": date(2024, 1, 25),
                "horizon_days": 7,
                "feature_name": "revenue_year_ago",
                "feature_value": 100.0,
                "maximum_input_available_date": date(2023, 2, 1),
            },
            {
                "company_id": "SBUX",
                "fiscal_quarter": "2024Q1",
                "earnings_date": date(2024, 2, 1),
                "forecast_cutoff_date": date(2024, 1, 25),
                "horizon_days": 7,
                "feature_name": "wikipedia_pageviews_trailing_mean",
                "feature_value": 1200.0,
                "maximum_input_available_date": date(2024, 1, 25),
            },
        ]
    )
    financials = pd.DataFrame([{"company_id": "SBUX", "fiscal_quarter": "2024Q1", "revenue": 110.0}])

    matrix = build_model_matrix(feature_rows, financials)

    assert matrix.iloc[0].actual_revenue == 110.0
    assert matrix.iloc[0].target_revenue_yoy_log_growth == pytest.approx(math.log(1.1))
    assert matrix.iloc[0].wikipedia_pageviews_trailing_mean == 1200.0
    assert matrix.iloc[0].maximum_input_available_date == date(2024, 1, 25)
