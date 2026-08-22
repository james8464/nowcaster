from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline


@dataclass(frozen=True)
class LinearExplanation:
    prediction: float
    intercept: float
    contributions: dict[str, float]


def explain_linear(model: Pipeline, row: pd.DataFrame, feature_columns: list[str]) -> LinearExplanation:
    preprocess = model.named_steps["preprocess"]
    estimator = model.named_steps["model"]
    transformed = np.asarray(preprocess.transform(row[["company_id", *feature_columns]]))[0]
    names = preprocess.get_feature_names_out()
    coefficients = np.asarray(estimator.coef_).reshape(-1)
    contributions = {
        str(name): float(value * coefficient)
        for name, value, coefficient in zip(names, transformed, coefficients, strict=True)
    }
    intercept = float(np.asarray(estimator.intercept_).reshape(-1)[0])
    prediction = float(intercept + sum(contributions.values()))
    return LinearExplanation(prediction, intercept, contributions)
