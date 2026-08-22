from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from src.models.base import ExpandingFold, ModelRunRecord, ModelSpec
from src.models.baselines import historical_growth_forecast, seasonal_naive_forecast
from src.models.linear import build_linear_pipeline
from src.models.metrics import evaluate_forecasts
from src.models.tree import build_tree_pipeline
from src.utils.provenance import canonical_hash

METADATA_COLUMNS = {
    "company_id",
    "fiscal_quarter",
    "earnings_date",
    "forecast_cutoff_date",
    "horizon_days",
    "maximum_input_available_date",
    "actual_revenue",
    "target_revenue_yoy_log_growth",
    "actual_acceleration",
    "forecast_acceleration",
}


def make_expanding_folds(matrix: pd.DataFrame, minimum_training_observations: int) -> list[ExpandingFold]:
    if minimum_training_observations <= 0:
        raise ValueError("minimum_training_observations must be positive")
    dates = pd.to_datetime(matrix["forecast_cutoff_date"]).dt.date
    unique_dates = sorted(set(dates))
    folds: list[ExpandingFold] = []
    for test_date in unique_dates:
        train_indices = matrix.index[dates < test_date].tolist()
        test_indices = matrix.index[dates == test_date].tolist()
        if len(train_indices) < minimum_training_observations or not test_indices:
            continue
        train_dates = dates.loc[train_indices]
        folds.append(
            ExpandingFold(
                train_indices=train_indices,
                test_indices=test_indices,
                training_start=min(train_dates),
                training_end=max(train_dates),
                test_start=test_date,
                test_end=test_date,
            )
        )
    return folds


def feature_columns_for_ablation(matrix: pd.DataFrame, ablation: str) -> list[str]:
    candidates = [column for column in matrix.columns if column not in METADATA_COLUMNS]
    fundamentals = [column for column in candidates if column.startswith(("revenue_", "seasonal_"))]
    alternative = [column for column in candidates if column.startswith(("wikipedia_", "search_"))]
    macro = [column for column in candidates if column.startswith("macro_")]
    mapping = {
        "fundamentals_only": fundamentals,
        "alternative_only": alternative + [column for column in fundamentals if column == "seasonal_quarter"],
        "fundamentals_alt": fundamentals + alternative,
        "fundamentals_alt_macro": fundamentals + alternative + macro,
    }
    if ablation not in mapping:
        raise ValueError(f"Unsupported ablation: {ablation}")
    return sorted(set(mapping[ablation]))


def _build_pipeline(spec: ModelSpec, feature_columns: list[str], seed: int):
    if spec.name in {"linear", "ols", "ridge", "elastic_net"}:
        parameters = {**spec.parameters, "random_state": seed}
        return build_linear_pipeline(ModelSpec(spec.name, spec.ablation, parameters), feature_columns)
    if spec.name == "gradient_boosting":
        return build_tree_pipeline(spec, feature_columns, seed)
    raise ValueError(f"Unsupported model: {spec.name}")


def _prediction_rows(
    test: pd.DataFrame,
    forecasts: np.ndarray,
    *,
    run_id: str,
    spec: ModelSpec,
    residual_std: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (_, source), forecast in zip(test.iterrows(), forecasts, strict=True):
        latest = source.get("revenue_level_lag1", math.nan)
        interval = 1.2816 * residual_std if not math.isnan(residual_std) else math.nan
        scale = max(abs(float(source.actual_revenue)), 1.0)
        confidence = max(0.0, min(100.0, 100.0 / (1.0 + residual_std / scale)))
        rows.append(
            {
                "run_id": run_id,
                "company_id": source.company_id,
                "fiscal_quarter": source.fiscal_quarter,
                "forecast_cutoff_date": source.forecast_cutoff_date,
                "horizon_days": int(source.horizon_days),
                "model_name": spec.name,
                "ablation": spec.ablation,
                "forecast_revenue": float(forecast),
                "actual_revenue": float(source.actual_revenue),
                "interval_low": float(forecast - interval) if not math.isnan(interval) else math.nan,
                "interval_high": float(forecast + interval) if not math.isnan(interval) else math.nan,
                "confidence_score": confidence,
                "forecast_acceleration": np.sign(forecast - latest) if not pd.isna(latest) else math.nan,
                "actual_acceleration": source.get("actual_acceleration", math.nan),
            }
        )
    return rows


def expanding_window_forecasts(
    matrix: pd.DataFrame,
    model_specs: list[ModelSpec],
    minimum_training_quarters: int,
    *,
    seed: int,
    retain_models: bool = False,
) -> tuple[pd.DataFrame, list[ModelRunRecord]]:
    data = matrix.copy().sort_values(["forecast_cutoff_date", "company_id"]).reset_index(drop=True)
    folds = make_expanding_folds(data, minimum_training_quarters)
    predictions: list[dict[str, Any]] = []
    runs: list[ModelRunRecord] = []
    for fold in folds:
        train = data.loc[fold.train_indices]
        test = data.loc[fold.test_indices]
        for spec in model_specs:
            run_id = canonical_hash([spec.name, spec.ablation, fold.test_start, seed])[:24]
            feature_columns = [
                column for column in feature_columns_for_ablation(data, spec.ablation) if train[column].notna().any()
            ]
            fitted_model = None
            if spec.name == "seasonal_naive":
                forecasts = test.apply(seasonal_naive_forecast, axis=1).to_numpy(dtype=float)
                train_forecasts = train.apply(seasonal_naive_forecast, axis=1).to_numpy(dtype=float)
            elif spec.name == "historical_growth":
                forecasts = test.apply(historical_growth_forecast, axis=1).to_numpy(dtype=float)
                train_forecasts = train.apply(historical_growth_forecast, axis=1).to_numpy(dtype=float)
            else:
                if not feature_columns:
                    continue
                fitted_model = _build_pipeline(spec, feature_columns, seed)
                input_columns = ["company_id", *feature_columns]
                fitted_model.fit(train[input_columns], train["actual_revenue"])
                forecasts = np.asarray(fitted_model.predict(test[input_columns]), dtype=float)
                train_forecasts = np.asarray(fitted_model.predict(train[input_columns]), dtype=float)
            residuals = train_forecasts - train["actual_revenue"].to_numpy(dtype=float)
            residual_std = float(np.std(residuals, ddof=1)) if len(residuals) > 1 else math.nan
            fold_rows = _prediction_rows(test, forecasts, run_id=run_id, spec=spec, residual_std=residual_std)
            predictions.extend(fold_rows)
            metrics = evaluate_forecasts(pd.DataFrame(fold_rows))
            runs.append(
                ModelRunRecord(
                    run_id=run_id,
                    model_name=spec.name,
                    ablation=spec.ablation,
                    training_start=fold.training_start,
                    training_end=fold.training_end,
                    test_start=fold.test_start,
                    test_end=fold.test_end,
                    observations=len(train),
                    feature_columns=feature_columns,
                    test_indices=fold.test_indices,
                    parameters=spec.parameters,
                    metrics=metrics,
                    fitted_model=fitted_model if retain_models else None,
                )
            )
    prediction_frame = pd.DataFrame(predictions)
    if not prediction_frame.empty:
        prediction_frame = prediction_frame.sort_values(
            ["forecast_cutoff_date", "model_name", "ablation", "company_id", "fiscal_quarter"]
        ).reset_index(drop=True)
    return prediction_frame, runs
