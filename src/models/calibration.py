from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression


class RollingProbabilityCalibrator:
    def __init__(self, minimum_observations: int = 100):
        if minimum_observations <= 0:
            raise ValueError("minimum_observations must be positive")
        self.minimum_observations = minimum_observations
        self.status = "unfitted"
        self._model: IsotonicRegression | None = None

    def fit(self, probabilities: np.ndarray, outcomes: np.ndarray) -> RollingProbabilityCalibrator:
        probability_values = np.asarray(probabilities, dtype=float)
        outcome_values = np.asarray(outcomes, dtype=int)
        valid = np.isfinite(probability_values) & np.isin(outcome_values, [0, 1])
        probability_values = np.clip(probability_values[valid], 0.0, 1.0)
        outcome_values = outcome_values[valid]
        if len(probability_values) < self.minimum_observations or len(np.unique(outcome_values)) < 2:
            self.status = "insufficient"
            self._model = None
            return self
        self._model = IsotonicRegression(out_of_bounds="clip").fit(probability_values, outcome_values)
        self.status = "calibrated"
        return self

    def predict(self, probabilities: np.ndarray) -> np.ndarray:
        probability_values = np.clip(np.asarray(probabilities, dtype=float), 0.0, 1.0)
        if self._model is None:
            return probability_values
        return np.asarray(self._model.predict(probability_values), dtype=float)
