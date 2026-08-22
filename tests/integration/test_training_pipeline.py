from __future__ import annotations

import pandas as pd

from src.models.base import ModelSpec
from src.models.metrics import evaluate_forecasts
from src.models.validation import expanding_window_forecasts


def test_training_pipeline_compares_baseline_and_alternative_model(model_matrix):
    specs = [
        ModelSpec(name="seasonal_naive", ablation="fundamentals_only", parameters={}),
        ModelSpec(name="ridge", ablation="fundamentals_alt", parameters={"alpha": 1.0}),
        ModelSpec(name="gradient_boosting", ablation="fundamentals_alt_macro", parameters={"max_iter": 30}),
    ]

    predictions, runs = expanding_window_forecasts(model_matrix, specs, 12, seed=42)
    metrics = predictions.groupby(["model_name", "ablation"]).apply(evaluate_forecasts, include_groups=False)

    assert set(predictions["model_name"]) == {"seasonal_naive", "ridge", "gradient_boosting"}
    assert all(run.observations >= 12 for run in runs)
    assert all(pd.notna(item["mae"]) for item in metrics)
