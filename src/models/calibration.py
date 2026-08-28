from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import numpy as np
from scipy.stats import beta as beta_distribution
from scipy.stats import norm
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

CalibrationMethod = Literal["oof_beta_v2", "oof_sigmoid_v2", "oof_isotonic_v2"]


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    method: str
    sample_size: int
    effective_sample_size: float
    positive_count: int
    base_rate: float
    brier_score: float
    log_loss: float
    expected_calibration_error: float
    confidence_low: float
    confidence_high: float
    slice_identity: str


@dataclass(frozen=True, slots=True)
class FittedProbabilityCalibration:
    method: CalibrationMethod
    status: Literal["calibrated", "insufficient"]
    report: CalibrationReport
    _model: Any = None

    def predict(self, probabilities: Sequence[float] | np.ndarray) -> np.ndarray:
        values = _probabilities(probabilities)
        if self.status != "calibrated" or self._model is None:
            return values
        if self.method == "oof_isotonic_v2":
            predicted = self._model.predict(values)
        elif self.method == "oof_beta_v2":
            predicted = self._model.predict_proba(_beta_features(values))[:, 1]
        else:
            predicted = self._model.predict_proba(_logit(values).reshape(-1, 1))[:, 1]
        return np.clip(np.asarray(predicted, dtype=float), 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class SelectiveThreshold:
    status: Literal["selected", "abstain"]
    threshold: float
    coverage: float
    observations: int
    effective_observations: float
    mean_net_edge: float
    lower_net_edge: float
    confidence: float


def _probabilities(values: Sequence[float] | np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    if result.ndim != 1 or not len(result) or not np.isfinite(result).all():
        raise ValueError("probabilities must be a non-empty finite one-dimensional sequence")
    return np.clip(result, 0.0, 1.0)


def _outcomes(values: Sequence[int] | np.ndarray, *, expected: int) -> np.ndarray:
    result = np.asarray(values)
    if result.ndim != 1 or len(result) != expected:
        raise ValueError("probabilities and outcomes must have equal one-dimensional lengths")
    try:
        numeric = result.astype(float)
    except (TypeError, ValueError) as error:
        raise ValueError("outcomes must be binary") from error
    if not np.isfinite(numeric).all() or not np.isin(numeric, (0.0, 1.0)).all():
        raise ValueError("outcomes must be binary")
    return numeric.astype(int)


def _effective_sample_size(values: np.ndarray, *, maximum_lag: int = 20) -> float:
    count = len(values)
    if count < 2:
        return float(count)
    centered = np.asarray(values, dtype=float) - float(np.mean(values))
    denominator = float(np.dot(centered, centered))
    if denominator <= np.finfo(float).eps:
        return float(count)
    correlation_sum = 0.0
    for lag in range(1, min(maximum_lag, count - 1) + 1):
        correlation = float(np.dot(centered[:-lag], centered[lag:]) / denominator)
        if not math.isfinite(correlation) or correlation <= 0:
            break
        correlation_sum += correlation
    effective = count / (1.0 + 2.0 * correlation_sum)
    return float(min(count, max(1.0, effective)))


def calibration_report(
    probabilities: Sequence[float] | np.ndarray,
    outcomes: Sequence[int] | np.ndarray,
    *,
    method: str = "raw",
    bins: int = 10,
    confidence: float = 0.95,
    slice_identity: str = "global",
) -> CalibrationReport:
    probability_values = _probabilities(probabilities)
    outcome_values = _outcomes(outcomes, expected=len(probability_values))
    if bins < 2:
        raise ValueError("calibration bins must be at least two")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")
    if not slice_identity.strip():
        raise ValueError("slice identity is required")

    epsilon = np.finfo(float).eps
    clipped = np.clip(probability_values, epsilon, 1.0 - epsilon)
    brier = float(np.mean(np.square(probability_values - outcome_values)))
    log_loss = float(-np.mean(outcome_values * np.log(clipped) + (1 - outcome_values) * np.log1p(-clipped)))
    bin_indices = np.minimum((probability_values * bins).astype(int), bins - 1)
    expected_error = 0.0
    for index in range(bins):
        mask = bin_indices == index
        if mask.any():
            expected_error += float(mask.mean()) * abs(
                float(probability_values[mask].mean()) - float(outcome_values[mask].mean())
            )

    effective = _effective_sample_size(outcome_values.astype(float))
    base_rate = float(outcome_values.mean())
    effective_successes = base_rate * effective
    tail = (1.0 - confidence) / 2.0
    low = float(beta_distribution.ppf(tail, effective_successes + 1.0, effective - effective_successes + 1.0))
    high = float(beta_distribution.ppf(1.0 - tail, effective_successes + 1.0, effective - effective_successes + 1.0))
    return CalibrationReport(
        method=method,
        sample_size=len(probability_values),
        effective_sample_size=effective,
        positive_count=int(outcome_values.sum()),
        base_rate=base_rate,
        brier_score=brier,
        log_loss=log_loss,
        expected_calibration_error=float(expected_error),
        confidence_low=low,
        confidence_high=high,
        slice_identity=slice_identity,
    )


def _validate_timestamps(timestamps: Sequence[datetime], *, expected: int) -> None:
    values = tuple(timestamps)
    if len(values) != expected:
        raise ValueError("timestamps must align one-to-one with probabilities")
    if any(not isinstance(value, datetime) or value.tzinfo is not UTC for value in values):
        raise ValueError("calibration timestamps require explicit UTC")
    if any(current <= previous for previous, current in zip(values, values[1:], strict=False)):
        raise ValueError("calibration timestamps must be unique and strictly increasing")


def _logit(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, 1e-9, 1.0 - 1e-9)
    return np.log(clipped / (1.0 - clipped))


def _beta_features(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, 1e-9, 1.0 - 1e-9)
    return np.column_stack((np.log(clipped), -np.log1p(-clipped)))


def fit_out_of_fold_calibration(
    probabilities: Sequence[float] | np.ndarray,
    outcomes: Sequence[int] | np.ndarray,
    timestamps: Sequence[datetime],
    *,
    method: Literal["auto", "beta", "sigmoid", "isotonic"] = "auto",
    minimum_observations: int = 100,
    isotonic_minimum: int = 1_000,
    slice_identity: str = "global",
) -> FittedProbabilityCalibration:
    probability_values = _probabilities(probabilities)
    outcome_values = _outcomes(outcomes, expected=len(probability_values))
    _validate_timestamps(timestamps, expected=len(probability_values))
    if minimum_observations < 2 or isotonic_minimum < minimum_observations:
        raise ValueError("calibration sample thresholds are invalid")
    selected = (
        "isotonic"
        if method == "auto" and len(probability_values) >= isotonic_minimum
        else "sigmoid"
        if method == "auto"
        else method
    )
    method_name: CalibrationMethod = {
        "beta": "oof_beta_v2",
        "sigmoid": "oof_sigmoid_v2",
        "isotonic": "oof_isotonic_v2",
    }[selected]
    raw_report = calibration_report(
        probability_values,
        outcome_values,
        method=method_name,
        slice_identity=slice_identity,
    )
    if len(probability_values) < minimum_observations or len(np.unique(outcome_values)) < 2:
        return FittedProbabilityCalibration(method_name, "insufficient", raw_report)

    if selected == "isotonic":
        model: Any = IsotonicRegression(out_of_bounds="clip").fit(probability_values, outcome_values)
        calibrated = np.asarray(model.predict(probability_values), dtype=float)
    else:
        model = LogisticRegression(C=1.0, solver="lbfgs", random_state=0)
        features = (
            _beta_features(probability_values) if selected == "beta" else _logit(probability_values).reshape(-1, 1)
        )
        model.fit(features, outcome_values)
        calibrated = model.predict_proba(features)[:, 1]
    report = calibration_report(
        calibrated,
        outcome_values,
        method=method_name,
        slice_identity=slice_identity,
    )
    return FittedProbabilityCalibration(method_name, "calibrated", report, model)


def selective_threshold(
    probabilities: Sequence[float] | np.ndarray,
    net_returns: Sequence[float] | np.ndarray,
    *,
    minimum_coverage: float = 0.05,
    minimum_observations: int = 30,
    confidence: float = 0.95,
    candidates: Sequence[float] | None = None,
) -> SelectiveThreshold:
    probability_values = _probabilities(probabilities)
    returns = np.asarray(net_returns, dtype=float)
    if returns.ndim != 1 or len(returns) != len(probability_values) or not np.isfinite(returns).all():
        raise ValueError("net returns must be finite and align with probabilities")
    if not 0 < minimum_coverage <= 1 or minimum_observations < 2 or not 0.5 < confidence < 1:
        raise ValueError("selective prediction policy is invalid")
    thresholds = (
        np.asarray(tuple(candidates), dtype=float)
        if candidates is not None
        else np.unique(np.concatenate((np.linspace(0.5, 0.95, 19), probability_values)))
    )
    if thresholds.ndim != 1 or not len(thresholds) or not np.isfinite(thresholds).all():
        raise ValueError("candidate thresholds must be finite")

    z_score = float(norm.ppf(0.5 + confidence / 2.0))
    viable: list[SelectiveThreshold] = []
    for threshold in thresholds:
        if not 0 <= threshold <= 1:
            raise ValueError("candidate thresholds must be in [0, 1]")
        selected = returns[probability_values >= threshold]
        coverage = len(selected) / len(returns)
        if coverage < minimum_coverage or len(selected) < minimum_observations:
            continue
        effective = _effective_sample_size(selected)
        mean = float(selected.mean())
        standard_error = float(selected.std(ddof=1) / math.sqrt(effective)) if len(selected) > 1 else math.inf
        lower = mean - z_score * standard_error
        viable.append(
            SelectiveThreshold(
                status="selected" if lower > 0 else "abstain",
                threshold=float(threshold),
                coverage=coverage,
                observations=len(selected),
                effective_observations=effective,
                mean_net_edge=mean,
                lower_net_edge=lower,
                confidence=confidence,
            )
        )
    positive = [item for item in viable if item.lower_net_edge > 0]
    if positive:
        return max(positive, key=lambda item: (item.lower_net_edge, item.coverage, -item.threshold))
    return SelectiveThreshold("abstain", 1.0, 0.0, 0, 0.0, 0.0, 0.0, confidence)


class RollingProbabilityCalibrator:
    """Backward-compatible isotonic helper for legacy callers."""

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


__all__ = [
    "CalibrationReport",
    "FittedProbabilityCalibration",
    "RollingProbabilityCalibrator",
    "SelectiveThreshold",
    "calibration_report",
    "fit_out_of_fold_calibration",
    "selective_threshold",
]
