from __future__ import annotations

import math

import numpy as np
import pandas as pd


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
