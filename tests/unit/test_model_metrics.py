from __future__ import annotations

import pandas as pd
import pytest

from src.models.metrics import evaluate_forecasts


def test_metrics_are_hand_calculated_and_mape_excludes_zero_actuals():
    predictions = pd.DataFrame(
        {
            "actual_revenue": [100.0, 200.0, 0.0],
            "forecast_revenue": [90.0, 220.0, 10.0],
            "actual_acceleration": [1, -1, 1],
            "forecast_acceleration": [1, 1, -1],
        }
    )

    metrics = evaluate_forecasts(predictions)

    assert metrics["mae"] == pytest.approx(40 / 3)
    assert metrics["rmse"] == pytest.approx((100 + 400 + 100) ** 0.5 / 3**0.5)
    assert metrics["mape"] == pytest.approx(0.1)
    assert metrics["directional_accuracy"] == pytest.approx(1 / 3)


def test_metrics_report_empty_sample_without_invented_values():
    metrics = evaluate_forecasts(pd.DataFrame())

    assert metrics["n"] == 0
    assert pd.isna(metrics["mae"])
