from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.models.base import ModelSpec
from src.models.explain import explain_linear
from src.models.validation import expanding_window_forecasts, make_expanding_folds


@pytest.fixture
def model_matrix():
    rows = []
    dates = pd.date_range("2018-02-01", periods=20, freq="QE")
    for index, cutoff in enumerate(dates):
        for company_index, company in enumerate(("SBUX", "MCD")):
            baseline = 100 + company_index * 50 + index * 2
            attention = 10 + index + company_index
            actual = baseline * (1 + attention / 1000)
            rows.append(
                {
                    "company_id": company,
                    "fiscal_quarter": f"{2018 + index // 4}Q{index % 4 + 1}",
                    "forecast_cutoff_date": cutoff.date(),
                    "horizon_days": 7,
                    "actual_revenue": actual,
                    "revenue_year_ago": baseline - 8,
                    "revenue_level_lag1": baseline - 2,
                    "revenue_qoq_log_growth_lag1": np.log(baseline / (baseline - 2)),
                    "revenue_yoy_log_growth_lag1": np.log(baseline / (baseline - 8)),
                    "seasonal_quarter": index % 4 + 1,
                    "wikipedia_pageviews_trailing_mean": attention,
                    "wikipedia_pageviews_momentum": index / 100,
                    "macro_RSAFS_level": 500 + index,
                    "actual_acceleration": 1 if index % 2 else -1,
                }
            )
    return pd.DataFrame(rows)


def test_expanding_folds_never_train_on_test_or_future_rows(model_matrix):
    folds = make_expanding_folds(model_matrix, minimum_training_observations=12)

    assert folds
    assert all(fold.training_end < fold.test_start for fold in folds)
    assert all(set(fold.train_indices).isdisjoint(fold.test_indices) for fold in folds)


def test_expanding_forecasts_are_deterministic_and_preserve_company_quarter(model_matrix):
    specs = [ModelSpec(name="ridge", ablation="fundamentals_alt", parameters={"alpha": 1.0})]

    first, first_runs = expanding_window_forecasts(model_matrix, specs, 12, seed=42)
    second, second_runs = expanding_window_forecasts(model_matrix, specs, 12, seed=42)

    pd.testing.assert_frame_equal(first, second)
    assert first[["company_id", "fiscal_quarter"]].notna().all().all()
    assert all(run.training_end < run.test_start for run in first_runs)


def test_linear_contributions_sum_to_model_prediction(model_matrix):
    specs = [ModelSpec(name="linear", ablation="fundamentals_alt", parameters={})]
    predictions, runs = expanding_window_forecasts(model_matrix, specs, 12, seed=42, retain_models=True)
    run = next(run for run in runs if run.fitted_model is not None)
    predicted = predictions.loc[predictions["run_id"] == run.run_id].iloc[0]
    test_row = model_matrix[
        (model_matrix["company_id"] == predicted.company_id)
        & (model_matrix["fiscal_quarter"] == predicted.fiscal_quarter)
    ]

    explanation = explain_linear(run.fitted_model, test_row, run.feature_columns)

    assert sum(explanation.contributions.values()) + explanation.intercept == pytest.approx(explanation.prediction)
    assert explanation.prediction == pytest.approx(predicted.forecast_revenue)
