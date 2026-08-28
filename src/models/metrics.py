from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict

import numpy as np
import pandas as pd

from src.models.calibration import calibration_report


def evaluate_forecasts(predictions: pd.DataFrame) -> dict[str, float | int]:
    if predictions.empty:
        return {
            "n": 0,
            "mae": math.nan,
            "rmse": math.nan,
            "mape": math.nan,
            "directional_accuracy": math.nan,
        }
    actual = predictions["actual_revenue"].astype(float)
    forecast = predictions["forecast_revenue"].astype(float)
    errors = forecast - actual
    valid_mape = actual != 0
    directional = math.nan
    if {"actual_acceleration", "forecast_acceleration"} <= set(predictions.columns):
        directional = float(
            (np.sign(predictions["actual_acceleration"]) == np.sign(predictions["forecast_acceleration"])).mean()
        )
    return {
        "n": int(len(predictions)),
        "mae": float(errors.abs().mean()),
        "rmse": float(np.sqrt(np.mean(np.square(errors)))),
        "mape": float((errors[valid_mape].abs() / actual[valid_mape].abs()).mean()) if valid_mape.any() else math.nan,
        "directional_accuracy": directional,
    }


def evaluate_probability_forecasts(
    probabilities: Sequence[float], outcomes: Sequence[int], *, slice_identity: str = "global"
) -> dict[str, float | int | str]:
    """Return literal probability-quality metrics without inventing missing evidence."""
    return asdict(calibration_report(probabilities, outcomes, slice_identity=slice_identity))
