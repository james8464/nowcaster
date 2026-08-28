from __future__ import annotations

import math
import statistics
from collections import deque
from collections.abc import Mapping, Sequence
from statistics import NormalDist
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.strategies.types import canonical_hash

DriftStatus = Literal["stable", "warning", "confirmed", "unavailable"]


class DriftPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_metrics: tuple[str, ...] = (
        "feature_distribution",
        "prediction_distribution",
        "calibration_residual",
        "realized_cost",
        "latency",
        "net_edge",
    )
    minimum_window: int = Field(default=20, ge=5, le=10_000)
    maximum_window_multiplier: int = Field(default=8, ge=2, le=100)
    minimum_ready_metrics: int = Field(default=3, ge=1)
    confidence: float = Field(default=0.99, gt=0.5, lt=1)
    warning_fraction: float = Field(default=0.75, gt=0, lt=1)
    confirmation_updates: int = Field(default=3, ge=1, le=100)

    @model_validator(mode="after")
    def valid_metric_policy(self) -> DriftPolicy:
        if not self.required_metrics or any(not item.strip() for item in self.required_metrics):
            raise ValueError("drift metrics must not be empty")
        if len(set(self.required_metrics)) != len(self.required_metrics):
            raise ValueError("drift metrics must be unique")
        if self.minimum_ready_metrics > len(self.required_metrics):
            raise ValueError("minimum ready drift metrics cannot exceed required metrics")
        return self

    @property
    def policy_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class DriftMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    reference_count: int = Field(ge=0)
    recent_count: int = Field(ge=0)
    reference_mean: float | None = None
    recent_mean: float | None = None
    absolute_shift: float | None = Field(default=None, ge=0)
    standardized_shift: float | None = Field(default=None, ge=0)
    threshold: float | None = Field(default=None, ge=0)
    warning: bool = False
    confirmed: bool = False
    status: DriftStatus


class DriftReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    status: DriftStatus
    metrics: tuple[DriftMetric, ...]
    missing_metrics: tuple[str, ...]
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def confirmed_metrics(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.metrics if item.confirmed)

    @property
    def maximum_standardized_shift(self) -> float | None:
        values = [item.standardized_shift for item in self.metrics if item.standardized_shift is not None]
        return max(values) if values else None


DEFAULT_DRIFT_POLICY = DriftPolicy()
DEFAULT_DRIFT_POLICY_HASH = DEFAULT_DRIFT_POLICY.policy_hash


def _finite_values(values: Sequence[float], *, name: str) -> tuple[float, ...]:
    result = tuple(float(item) for item in values)
    if any(not math.isfinite(item) for item in result):
        raise ValueError(f"drift metric {name} must contain finite values")
    return result


def _compare_metric(
    name: str,
    reference: Sequence[float],
    recent: Sequence[float],
    policy: DriftPolicy,
) -> DriftMetric:
    baseline = _finite_values(reference, name=name)
    current = _finite_values(recent, name=name)
    if len(baseline) < policy.minimum_window or len(current) < policy.minimum_window:
        return DriftMetric(
            name=name,
            reference_count=len(baseline),
            recent_count=len(current),
            status="unavailable",
        )
    baseline_mean = statistics.fmean(baseline)
    current_mean = statistics.fmean(current)
    difference = abs(current_mean - baseline_mean)
    baseline_variance = statistics.variance(baseline) if len(baseline) > 1 else 0.0
    current_variance = statistics.variance(current) if len(current) > 1 else 0.0
    standard_error = math.sqrt(baseline_variance / len(baseline) + current_variance / len(current))
    scale_floor = max(max(map(abs, (*baseline, *current))), 1.0) * 1e-12
    effective_error = max(standard_error, scale_floor)
    critical = NormalDist().inv_cdf((1 + policy.confidence) / 2)
    threshold = critical * effective_error
    standardized = difference / effective_error
    confirmed = difference > threshold
    warning = not confirmed and difference > threshold * policy.warning_fraction
    return DriftMetric(
        name=name,
        reference_count=len(baseline),
        recent_count=len(current),
        reference_mean=baseline_mean,
        recent_mean=current_mean,
        absolute_shift=difference,
        standardized_shift=standardized,
        threshold=threshold,
        warning=warning,
        confirmed=confirmed,
        status="confirmed" if confirmed else "warning" if warning else "stable",
    )


def _report(metrics: Sequence[DriftMetric], missing: Sequence[str], policy: DriftPolicy) -> DriftReport:
    ordered = tuple(sorted(metrics, key=lambda item: item.name))
    unavailable = tuple(sorted(set(missing) | {item.name for item in ordered if item.status == "unavailable"}))
    ready = sum(item.status != "unavailable" for item in ordered)
    if ready < policy.minimum_ready_metrics:
        status: DriftStatus = "unavailable"
    elif any(item.confirmed for item in ordered):
        status = "confirmed"
    elif any(item.warning for item in ordered):
        status = "warning"
    else:
        status = "stable"
    payload = {
        "schema_version": 1,
        "status": status,
        "metrics": [item.model_dump(mode="json") for item in ordered],
        "missing_metrics": unavailable,
        "policy_hash": policy.policy_hash,
    }
    return DriftReport(**payload, evidence_hash=canonical_hash(payload))


def assess_drift(
    reference: Mapping[str, Sequence[float]],
    recent: Mapping[str, Sequence[float]],
    *,
    policy: DriftPolicy = DEFAULT_DRIFT_POLICY,
) -> DriftReport:
    """Compare sealed reference samples with recent samples without mutating either."""
    metrics: list[DriftMetric] = []
    missing: list[str] = []
    for name in policy.required_metrics:
        if name not in reference or name not in recent:
            missing.append(name)
            continue
        metrics.append(_compare_metric(name, reference[name], recent[name], policy))
    return _report(metrics, missing, policy)


class AdaptiveMeanDrift:
    """Bounded adaptive two-window monitor with latched confirmation."""

    def __init__(
        self,
        *,
        name: str = "adaptive_mean",
        minimum_window: int = 20,
        confidence: float = 0.99,
        warning_fraction: float = 0.75,
        confirmation_updates: int = 3,
        maximum_window_multiplier: int = 8,
    ):
        self.name = name
        self.policy = DriftPolicy(
            required_metrics=(name,),
            minimum_window=minimum_window,
            maximum_window_multiplier=maximum_window_multiplier,
            minimum_ready_metrics=1,
            confidence=confidence,
            warning_fraction=warning_fraction,
            confirmation_updates=confirmation_updates,
        )
        self._values: deque[float] = deque(maxlen=minimum_window * maximum_window_multiplier)
        self._confirmations = 0
        self._latched = False

    def update(self, value: float) -> DriftMetric:
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("adaptive drift input must be finite")
        self._values.append(numeric)
        window = self.policy.minimum_window
        if len(self._values) < window * 2:
            return DriftMetric(
                name=self.name,
                reference_count=max(len(self._values) - window, 0),
                recent_count=min(len(self._values), window),
                status="unavailable",
            )
        values = tuple(self._values)
        metric = _compare_metric(self.name, values[-2 * window : -window], values[-window:], self.policy)
        self._confirmations = self._confirmations + 1 if metric.confirmed else 0
        self._latched = self._latched or self._confirmations >= self.policy.confirmation_updates
        if self._latched:
            return metric.model_copy(update={"confirmed": True, "warning": False, "status": "confirmed"})
        if metric.confirmed:
            return metric.model_copy(update={"confirmed": False, "warning": True, "status": "warning"})
        return metric


class StreamingDriftMonitor:
    """Coordinate bounded adaptive monitors for heterogeneous live metrics."""

    def __init__(self, policy: DriftPolicy = DEFAULT_DRIFT_POLICY):
        self.policy = policy
        self._monitors = {
            name: AdaptiveMeanDrift(
                name=name,
                minimum_window=policy.minimum_window,
                confidence=policy.confidence,
                warning_fraction=policy.warning_fraction,
                confirmation_updates=policy.confirmation_updates,
                maximum_window_multiplier=policy.maximum_window_multiplier,
            )
            for name in policy.required_metrics
        }
        self._latest: dict[str, DriftMetric] = {}

    def update(self, values: Mapping[str, float]) -> DriftReport:
        unknown = set(values) - set(self._monitors)
        if unknown:
            raise ValueError(f"unknown drift metrics: {sorted(unknown)}")
        for name, value in values.items():
            self._latest[name] = self._monitors[name].update(value)
        missing = [name for name in self.policy.required_metrics if name not in self._latest]
        return _report(tuple(self._latest.values()), missing, self.policy)


__all__ = [
    "AdaptiveMeanDrift",
    "DEFAULT_DRIFT_POLICY",
    "DEFAULT_DRIFT_POLICY_HASH",
    "DriftMetric",
    "DriftPolicy",
    "DriftReport",
    "StreamingDriftMonitor",
    "assess_drift",
]
