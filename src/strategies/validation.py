from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from src.backtest.intraday import IntradayBacktestResult
from src.backtest.metrics import BacktestMetrics, calculate_backtest_metrics
from src.backtest.robustness import deflated_sharpe_probability, doubled_cost_survival
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, StrategyFamily, StrategyMode, canonical_hash


class EvaluationStatus(StrEnum):
    EVALUATED = "evaluated"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    final_test_fraction: float = 0.2
    minimum_train_observations: int = 20
    validation_observations: int = 10
    forecast_horizon: timedelta = timedelta(0)
    publication_delay: timedelta = timedelta(0)
    embargo: timedelta = timedelta(0)
    periods_per_year: int = 252
    minimum_trades: int = 1
    minimum_development_observations: int = 5
    maximum_drawdown: float = 0.5
    minimum_dsr_probability: float = 0.5
    maximum_pbo_probability: float = 0.5
    minimum_parameter_neighbor_positive_fraction: float = 0.5
    minimum_parameter_neighbor_median_ratio: float = 0.5

    def __post_init__(self) -> None:
        if not 0 < self.final_test_fraction < 1:
            raise ValueError("final_test_fraction must be in (0, 1)")
        if self.minimum_train_observations <= 0 or self.validation_observations <= 0:
            raise ValueError("training and validation observations must be positive")
        if any(value < timedelta(0) for value in (self.forecast_horizon, self.publication_delay, self.embargo)):
            raise ValueError("horizon, publication delay, and embargo cannot be negative")
        if self.periods_per_year <= 0 or self.minimum_trades < 0 or self.minimum_development_observations <= 0:
            raise ValueError("period and evidence counts must be valid")
        probabilities = (
            self.maximum_drawdown,
            self.minimum_dsr_probability,
            self.maximum_pbo_probability,
            self.minimum_parameter_neighbor_positive_fraction,
            self.minimum_parameter_neighbor_median_ratio,
        )
        if any(not 0 <= value <= 1 for value in probabilities):
            raise ValueError("promotion probability and drawdown thresholds must be in [0, 1]")

    @property
    def effective_embargo(self) -> timedelta:
        return max(self.embargo, self.forecast_horizon + self.publication_delay)


DEFAULT_VALIDATION_CONFIG = ValidationConfig()


def promotion_reasons(inputs: Mapping[str, Any], config: ValidationConfig) -> tuple[str, ...]:
    reasons: list[str] = []
    if inputs["status"] != EvaluationStatus.EVALUATED.value:
        reasons.append("strategy evaluation did not complete")
    if int(inputs["observations"]) < config.minimum_development_observations:
        reasons.append("insufficient development observations")
    if int(inputs["trades"]) < config.minimum_trades:
        reasons.append("insufficient development trades")
    development_sharpe = inputs["development_sharpe"]
    if development_sharpe is None or not math.isfinite(float(development_sharpe)) or float(development_sharpe) <= 0:
        reasons.append("development Sharpe is not positive")
    maximum_drawdown = inputs["maximum_drawdown"]
    if (
        maximum_drawdown is None
        or not math.isfinite(float(maximum_drawdown))
        or abs(float(maximum_drawdown)) > float(config.maximum_drawdown)
    ):
        reasons.append("development drawdown exceeds the gate")
    fold_stability = inputs["fold_stability"]
    if fold_stability is None or not math.isfinite(float(fold_stability)) or float(fold_stability) < 0.5:
        reasons.append("walk-forward fold stability failed")
    if inputs["cost_survives"] is not True:
        reasons.append("doubled-cost survival failed")
    dsr_probability = inputs["dsr_probability"]
    if dsr_probability is None:
        reasons.append("observed trial Sharpe vector is unavailable")
    elif not math.isfinite(float(dsr_probability)) or float(dsr_probability) < config.minimum_dsr_probability:
        reasons.append("Deflated Sharpe probability failed")
    if inputs["causal_audit_passed"] is not True:
        reasons.append("causal audit failed")
    if inputs.get("robustness_available") is not True:
        reasons.append("robustness diagnostics are unavailable")
    else:
        median_edge = inputs.get("median_walk_forward_net_edge")
        if median_edge is None or not math.isfinite(float(median_edge)) or float(median_edge) <= 0:
            reasons.append("median walk-forward net edge is not positive")
        pbo_probability = inputs.get("pbo_probability")
        if (
            pbo_probability is None
            or not math.isfinite(float(pbo_probability))
            or float(pbo_probability) > config.maximum_pbo_probability
        ):
            reasons.append("CSCV/PBO gate failed")
        if (
            inputs.get("parameter_neighborhood_stable") is not True
            or float(inputs.get("parameter_neighbor_positive_fraction", -1))
            < config.minimum_parameter_neighbor_positive_fraction
            or float(inputs.get("parameter_neighbor_median_ratio", -1)) < config.minimum_parameter_neighbor_median_ratio
        ):
            reasons.append("parameter-neighborhood stability failed")
    return tuple(reasons)


@dataclass(frozen=True, slots=True)
class FinalBoundary:
    final_start: pd.Timestamp
    development_index: tuple[int, ...]
    final_index: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    train_index: tuple[int, ...]
    validation_index: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class OuterFold(WalkForwardFold):
    inner_folds: tuple[WalkForwardFold, ...] = ()


@dataclass(frozen=True, slots=True)
class FrozenProtocolResult:
    boundary: FinalBoundary
    outer_predictions: pd.DataFrame
    final_predictions: pd.DataFrame
    final_training_index: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    promoted: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrialEvidence:
    trial_id: str
    sharpe: float
    training_end: datetime
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class FoldEvidence:
    fold: int
    validation_start: datetime
    validation_end: datetime
    evaluated_at: datetime
    sharpe: float
    calibration_error: float


@dataclass(frozen=True, slots=True)
class StrategyRunEvidence:
    backtest: IntradayBacktestResult | None = None
    signals: pd.DataFrame = field(default_factory=pd.DataFrame)
    trial_evidence: tuple[TrialEvidence, ...] = ()
    fold_evidence: tuple[FoldEvidence, ...] = ()
    robustness: RobustnessEvidence | None = None
    # Legacy aggregate inputs remain constructor-compatible but are rejected by evaluate_registry.
    trial_sharpes: tuple[float, ...] = ()
    causal_audit_passed: bool = True
    calibration_error: float = 0.0
    fold_stability: float | None = None
    expected_edge: float = 0.0
    expected_cost: float = 0.0
    uncertainty: float = 0.0
    unavailable_reason: str | None = None
    error_summary: str | None = None


def calculate_fold_calibration_error(
    signals: pd.DataFrame,
    outcomes: pd.DataFrame,
    decision_timestamps: Sequence[pd.Timestamp | datetime],
) -> float:
    """Score directional confidence against causally mapped fold outcomes."""
    required_signals = {"decision_timestamp", "signal", "strength"}
    required_outcomes = {"decision_timestamp", "outcome_available_at", "net_return"}
    if missing := required_signals - set(signals.columns):
        raise ValueError(f"fold signals are missing columns: {sorted(missing)}")
    if missing := required_outcomes - set(outcomes.columns):
        raise ValueError(f"fold outcomes are missing columns: {sorted(missing)}")

    expected = pd.DataFrame({"decision_timestamp": pd.to_datetime(list(decision_timestamps), utc=True)})
    signal_rows = signals[["decision_timestamp", "signal", "strength"]].copy()
    signal_rows["decision_timestamp"] = pd.to_datetime(signal_rows["decision_timestamp"], utc=True)
    if signal_rows["decision_timestamp"].duplicated().any():
        raise ValueError("fold signals contain duplicate decision timestamps")
    signal_rows = signal_rows.sort_values("decision_timestamp", kind="stable")
    outcome_rows = outcomes[["decision_timestamp", "outcome_available_at", "net_return"]].copy()
    outcome_rows["decision_timestamp"] = pd.to_datetime(outcome_rows["decision_timestamp"], utc=True)
    if outcome_rows["decision_timestamp"].duplicated().any():
        raise ValueError("fold outcomes contain duplicate decision timestamps")
    expected = expected.sort_values("decision_timestamp", kind="stable")
    joined = pd.merge_asof(
        expected,
        signal_rows,
        on="decision_timestamp",
        direction="backward",
        allow_exact_matches=True,
    ).merge(
        outcome_rows,
        on="decision_timestamp",
        how="left",
        validate="one_to_one",
    )
    if joined[["outcome_available_at", "net_return"]].isna().any().any():
        raise ValueError("fold decisions do not map one-to-one to outcome rows")
    joined[["signal", "strength"]] = joined[["signal", "strength"]].fillna(0.0)

    directions = pd.to_numeric(joined["signal"], errors="raise")
    strengths = pd.to_numeric(joined["strength"], errors="raise")
    returns = pd.to_numeric(joined["net_return"], errors="raise")
    if not directions.isin((-1, 0, 1)).all() or not strengths.between(0, 1).all():
        raise ValueError("fold signals contain invalid direction or strength")
    probability = 0.5 + 0.5 * strengths.where(directions != 0, 0.0)
    favorable = ((directions * returns) > 0).astype(float)
    return float(np.mean(np.square(probability - favorable)))


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    strategy_id: str
    strategy_version: str
    family: StrategyFamily
    status: EvaluationStatus
    status_reason: str
    promotion: PromotionDecision
    development_sharpe: float | None = None
    final_sharpe: float | None = None
    downside_risk: float | None = None
    development_maximum_drawdown: float | None = None
    calibration_error: float | None = None
    fold_stability: float | None = None
    cost_survives: bool | None = None
    observations: int = 0
    trades: int = 0
    dsr_probability: float | None = None
    trial_sharpes: tuple[float, ...] = ()
    causal_audit_passed: bool = False
    current_signal: int = 0
    current_strength: float = 0.0
    current_probability: float = 0.5
    calibration_status: str = "calibrated"
    economic_evidence_status: str = "unavailable"
    current_volatility: float = 1.0
    expected_edge: float = 0.0
    expected_cost: float = 0.0
    uncertainty: float = 0.0
    robustness: RobustnessEvidence | None = None
    decision_timestamp: datetime | None = None
    data_through: datetime | None = None
    dataset_hash: str = ""
    symbol: str = ""
    interval: BarInterval = BarInterval.ONE_DAY
    mode: StrategyMode = StrategyMode.FROZEN
    evidence_provenance: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.calibration_status not in {"calibrated", "unavailable"}:
            raise ValueError("calibration status must be calibrated or unavailable")
        if self.economic_evidence_status not in {"authenticated", "unavailable"}:
            raise ValueError("economic evidence status must be authenticated or unavailable")
        object.__setattr__(self, "evidence_provenance", _deep_freeze(self.evidence_provenance))


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    registry: StrategyRegistry
    runs: Mapping[str, StrategyRunEvidence]
    chronology: Sequence[object] | pd.Series
    outcome_availability: Sequence[object] | pd.Series
    as_of: datetime
    mode: StrategyMode
    dataset_hash: str
    symbol: str
    interval: BarInterval
    cohort_id: str = ""
    config: ValidationConfig = field(default_factory=ValidationConfig)


Predictor = Callable[[pd.DataFrame, pd.Series, pd.DataFrame], Sequence[float]]


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in sorted(value.items(), key=str)})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_deep_freeze(item) for item in value), key=str))
    return value


@dataclass(frozen=True, slots=True)
class _SealedEvidence:
    trial_sharpes: tuple[float, ...]
    fold_stability: float
    calibration_error: float
    robustness: RobustnessEvidence | None
    provenance: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RobustnessEvidence:
    median_walk_forward_net_edge: float
    pbo_probability: float
    parameter_neighborhood_stable: bool
    parameter_neighbor_positive_fraction: float
    parameter_neighbor_median_ratio: float
    discovered_at: datetime | None = None
    evaluated_at: datetime | None = None
    development_data_through: datetime | None = None
    sealed_final_start: datetime | None = None
    cohort_id: str = ""
    dataset_hash: str = ""
    validation_config_hash: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)
    receipt_hash: str = ""

    def __post_init__(self) -> None:
        numeric = (
            self.median_walk_forward_net_edge,
            self.pbo_probability,
            self.parameter_neighbor_positive_fraction,
            self.parameter_neighbor_median_ratio,
        )
        if any(not math.isfinite(float(value)) for value in numeric):
            raise ValueError("robustness evidence must be finite")
        if not 0 <= self.pbo_probability <= 1:
            raise ValueError("PBO probability must be in [0, 1]")
        if not 0 <= self.parameter_neighbor_positive_fraction <= 1:
            raise ValueError("parameter neighbor fraction must be in [0, 1]")
        if self.parameter_neighbor_median_ratio < 0:
            raise ValueError("parameter neighbor median ratio cannot be negative")
        if type(self.parameter_neighborhood_stable) is not bool:
            raise ValueError("parameter stability must be a boolean")
        object.__setattr__(self, "provenance", _deep_freeze(self.provenance))
        if self.receipt_hash:
            for name in ("discovered_at", "evaluated_at", "development_data_through", "sealed_final_start"):
                _evidence_timestamp(getattr(self, name), f"robustness {name}")
            if not self.cohort_id.strip() or not self.dataset_hash.strip() or not self.validation_config_hash.strip():
                raise ValueError("robustness receipt context is incomplete")
            if self.receipt_hash != canonical_hash(self._receipt_payload()):
                raise ValueError("robustness receipt hash does not authenticate its evidence")

    def _receipt_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "metrics": {
                "median_walk_forward_net_edge": self.median_walk_forward_net_edge,
                "pbo_probability": self.pbo_probability,
                "parameter_neighborhood_stable": self.parameter_neighborhood_stable,
                "parameter_neighbor_positive_fraction": self.parameter_neighbor_positive_fraction,
                "parameter_neighbor_median_ratio": self.parameter_neighbor_median_ratio,
            },
            "discovered_at": self.discovered_at,
            "evaluated_at": self.evaluated_at,
            "development_data_through": self.development_data_through,
            "sealed_final_start": self.sealed_final_start,
            "cohort_id": self.cohort_id,
            "dataset_hash": self.dataset_hash,
            "validation_config_hash": self.validation_config_hash,
            "provenance": self.provenance,
        }

    @classmethod
    def seal(
        cls,
        *,
        median_walk_forward_net_edge: float,
        pbo_probability: float,
        parameter_neighborhood_stable: bool,
        parameter_neighbor_positive_fraction: float,
        parameter_neighbor_median_ratio: float,
        discovered_at: datetime,
        evaluated_at: datetime,
        development_data_through: datetime,
        sealed_final_start: datetime,
        cohort_id: str,
        dataset_hash: str,
        validation_config_hash: str,
        fold_evidence: Sequence[FoldEvidence],
    ) -> RobustnessEvidence:
        fold_rows = _robustness_fold_rows(fold_evidence)
        provenance = {
            "source": "admissible_development_folds_v1",
            "fold_count": len(fold_rows),
            "fold_evidence_hash": canonical_hash(fold_rows),
        }
        unsealed = cls(
            median_walk_forward_net_edge,
            pbo_probability,
            parameter_neighborhood_stable,
            parameter_neighbor_positive_fraction,
            parameter_neighbor_median_ratio,
            discovered_at,
            evaluated_at,
            development_data_through,
            sealed_final_start,
            cohort_id,
            dataset_hash,
            validation_config_hash,
            provenance,
        )
        return replace(unsealed, receipt_hash=canonical_hash(unsealed._receipt_payload()))


def _robustness_fold_rows(folds: Sequence[FoldEvidence]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "fold": int(item.fold),
            "validation_start": item.validation_start,
            "validation_end": item.validation_end,
            "evaluated_at": item.evaluated_at,
            "sharpe": float(item.sharpe),
            "calibration_error": float(item.calibration_error),
        }
        for item in sorted(folds, key=lambda item: int(item.fold))
    )


def _evidence_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is not UTC:
        raise ValueError(f"malformed {label}: timestamps must be explicit UTC datetimes")
    return value


def _timestamp_values(values: Sequence[object] | pd.Series, *, name: str) -> pd.Series:
    raw = list(values)
    timestamps: list[pd.Timestamp] = []
    for value in raw:
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} must contain explicit UTC timestamps") from error
        if pd.isna(timestamp) or timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            raise ValueError(f"{name} must contain explicit UTC timestamps")
        timestamps.append(timestamp.tz_convert("UTC"))
    if not timestamps:
        raise ValueError(f"{name} must contain explicit UTC timestamps")
    return pd.Series(timestamps).reset_index(drop=True)


def _fold_plan(folds: Sequence[OuterFold]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "fold": fold_number,
            "train_index": fold.train_index,
            "validation_index": fold.validation_index,
            "inner_folds": tuple(
                {
                    "train_index": inner.train_index,
                    "validation_index": inner.validation_index,
                }
                for inner in fold.inner_folds
            ),
        }
        for fold_number, fold in enumerate(folds)
    )


def _config_record(config: ValidationConfig) -> dict[str, Any]:
    return {
        "final_test_fraction": float(config.final_test_fraction),
        "minimum_train_observations": config.minimum_train_observations,
        "validation_observations": config.validation_observations,
        "forecast_horizon_seconds": config.forecast_horizon.total_seconds(),
        "publication_delay_seconds": config.publication_delay.total_seconds(),
        "embargo_seconds": config.embargo.total_seconds(),
        "periods_per_year": config.periods_per_year,
        "minimum_trades": config.minimum_trades,
        "minimum_development_observations": config.minimum_development_observations,
        "maximum_drawdown": float(config.maximum_drawdown),
        "minimum_dsr_probability": float(config.minimum_dsr_probability),
        "maximum_pbo_probability": float(config.maximum_pbo_probability),
        "minimum_parameter_neighbor_positive_fraction": float(config.minimum_parameter_neighbor_positive_fraction),
        "minimum_parameter_neighbor_median_ratio": float(config.minimum_parameter_neighbor_median_ratio),
    }


def validation_policy_hash(config: ValidationConfig) -> str:
    """Return the canonical external trust anchor for one validation policy."""

    return canonical_hash(_config_record(config))


def _seal_development_evidence(
    evidence: StrategyRunEvidence,
    boundary: FinalBoundary,
    chronology: pd.Series,
    expected_folds: Sequence[OuterFold],
    *,
    as_of: datetime,
    dataset_hash: str,
    cohort_id: str,
    validation_config_hash: str,
) -> _SealedEvidence:
    if evidence.trial_sharpes or evidence.fold_stability is not None:
        raise ValueError("unsealed aggregate evidence is forbidden; provide timestamped trial and fold evidence")

    trial_rows: list[dict[str, Any]] = []
    trial_ids: set[str] = set()
    for item in evidence.trial_evidence:
        try:
            trial_id = str(item.trial_id).strip()
            sharpe = float(item.sharpe)
            training_end = _evidence_timestamp(item.training_end, "trial evidence")
            evaluated_at = _evidence_timestamp(item.evaluated_at, "trial evidence")
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(f"malformed trial evidence: {error}") from error
        if not trial_id or trial_id in trial_ids or not math.isfinite(sharpe):
            raise ValueError("malformed trial evidence: IDs must be unique and Sharpes finite")
        if training_end > evaluated_at:
            raise ValueError("malformed trial evidence: training cannot end after evaluation")
        if training_end > as_of or evaluated_at > as_of:
            raise ValueError("trial evidence cannot follow the requested as_of")
        if training_end >= boundary.final_start or evaluated_at >= boundary.final_start:
            raise ValueError("trial evidence crosses the sealed final boundary")
        trial_ids.add(trial_id)
        trial_rows.append(
            {
                "trial_id": trial_id,
                "sharpe": sharpe,
                "training_end": training_end,
                "evaluated_at": evaluated_at,
            }
        )
    trial_rows.sort(key=lambda row: (row["evaluated_at"], row["trial_id"]))

    fold_rows: list[dict[str, Any]] = []
    fold_ids: set[int] = set()
    for item in evidence.fold_evidence:
        try:
            fold = int(item.fold)
            validation_start = _evidence_timestamp(item.validation_start, "fold evidence")
            validation_end = _evidence_timestamp(item.validation_end, "fold evidence")
            evaluated_at = _evidence_timestamp(item.evaluated_at, "fold evidence")
            sharpe = float(item.sharpe)
            calibration_error = float(item.calibration_error)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(f"malformed fold evidence: {error}") from error
        if (
            fold < 0
            or fold in fold_ids
            or not math.isfinite(sharpe)
            or not math.isfinite(calibration_error)
            or not 0 <= calibration_error <= 1
            or not validation_start < validation_end <= evaluated_at
        ):
            raise ValueError("malformed fold evidence: folds, metrics, or chronology are invalid")
        if validation_end >= boundary.final_start or evaluated_at >= boundary.final_start:
            raise ValueError("fold evidence crosses the sealed final boundary")
        if validation_start > as_of or validation_end > as_of or evaluated_at > as_of:
            raise ValueError("fold evidence cannot follow the requested as_of")
        fold_ids.add(fold)
        fold_rows.append(
            {
                "fold": fold,
                "validation_start": validation_start,
                "validation_end": validation_end,
                "evaluated_at": evaluated_at,
                "sharpe": sharpe,
                "calibration_error": calibration_error,
            }
        )
    fold_rows.sort(key=lambda row: (row["validation_start"], row["fold"]))
    if len(fold_rows) != len(expected_folds) or tuple(row["fold"] for row in fold_rows) != tuple(
        range(len(expected_folds))
    ):
        raise ValueError("fold evidence is incomplete for the sealed outer-fold plan")
    for row, expected in zip(fold_rows, expected_folds, strict=True):
        expected_start = chronology.iloc[expected.validation_index[0]].to_pydatetime()
        expected_end = chronology.iloc[expected.validation_index[-1]].to_pydatetime()
        if row["validation_start"] != expected_start or row["validation_end"] != expected_end:
            raise ValueError("fold evidence does not match the sealed outer-fold plan")

    fold_stability = float(sum(row["sharpe"] > 0 for row in fold_rows) / len(fold_rows)) if fold_rows else 0.0
    calibration = (
        float(round(sum(row["calibration_error"] for row in fold_rows) / len(fold_rows), 15)) if fold_rows else 1.0
    )
    robustness = evidence.robustness
    if robustness is not None:
        if not robustness.receipt_hash:
            raise ValueError("robustness diagnostics lack a sealed receipt")
        if (
            robustness.dataset_hash != dataset_hash
            or robustness.cohort_id != cohort_id
            or robustness.validation_config_hash != validation_config_hash
        ):
            raise ValueError("robustness receipt context does not match the evaluation cohort")
        if robustness.sealed_final_start != boundary.final_start.to_pydatetime():
            raise ValueError("robustness receipt final boundary does not match the sealed evaluation")
        if (
            robustness.discovered_at is None
            or robustness.evaluated_at is None
            or robustness.development_data_through is None
            or not robustness.discovered_at <= robustness.evaluated_at < boundary.final_start.to_pydatetime()
            or robustness.development_data_through >= boundary.final_start.to_pydatetime()
        ):
            raise ValueError("robustness receipt crosses the sealed final boundary")
        expected_development_through = max(row["evaluated_at"] for row in fold_rows)
        if robustness.development_data_through != expected_development_through:
            raise ValueError("robustness receipt development boundary does not match admissible folds")
        expected_fold_hash = canonical_hash(fold_rows)
        if (
            robustness.provenance.get("fold_count") != len(fold_rows)
            or robustness.provenance.get("fold_evidence_hash") != expected_fold_hash
        ):
            raise ValueError("robustness receipt fold provenance is malformed or mismatched")
    trial_sharpes = tuple(float(row["sharpe"]) for row in trial_rows)
    provenance = _deep_freeze(
        {
            "sealed": True,
            "sealed_boundary": boundary.final_start.isoformat(),
            "trial_source": "timestamped_trial_evidence",
            "trial_evidence_hash": canonical_hash(trial_rows),
            "fold_evidence_hash": canonical_hash(fold_rows),
            "trials": tuple(trial_rows),
            "folds": tuple(fold_rows),
        }
    )
    return _SealedEvidence(trial_sharpes, fold_stability, calibration, robustness, provenance)


def _timestamps(values: Sequence[object] | pd.Series, *, name: str) -> pd.Series:
    result = _timestamp_values(values, name=name)
    if not result.is_monotonic_increasing or result.duplicated().any():
        raise ValueError(f"{name} must be strictly chronological and unique")
    return result


def select_final_boundary(
    chronology: Sequence[object] | pd.Series,
    *,
    final_test_fraction: float,
) -> FinalBoundary:
    if not 0 < final_test_fraction < 1:
        raise ValueError("final_test_fraction must be in (0, 1)")
    timestamps = _timestamps(chronology, name="chronology")
    final_count = max(1, math.ceil(len(timestamps) * final_test_fraction))
    if final_count >= len(timestamps):
        raise ValueError("chronology must leave at least one development observation")
    start = len(timestamps) - final_count
    return FinalBoundary(
        final_start=pd.Timestamp(timestamps.iloc[start]),
        development_index=tuple(range(start)),
        final_index=tuple(range(start, len(timestamps))),
    )


def make_outer_folds(
    data: pd.DataFrame,
    *,
    boundary: FinalBoundary,
    config: ValidationConfig,
    timestamp_column: str = "decision_timestamp",
    outcome_available_column: str = "outcome_available_at",
) -> tuple[OuterFold, ...]:
    required = {timestamp_column, outcome_available_column}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"validation data is missing columns: {sorted(missing)}")
    decisions = _timestamps(data[timestamp_column], name=timestamp_column)
    available = _timestamp_values(data[outcome_available_column], name=outcome_available_column)
    if (available < decisions).any():
        raise ValueError("outcomes must become available at or after their decisions")
    folds: list[OuterFold] = []
    eligible_development = tuple(
        index for index in boundary.development_index if available.iloc[index] < boundary.final_start
    )
    development_end = len(eligible_development)
    if eligible_development != tuple(range(development_end)):
        raise ValueError("development outcome availability must form a chronological prefix")
    for validation_start in range(
        config.minimum_train_observations,
        development_end,
        config.validation_observations,
    ):
        validation_end = min(validation_start + config.validation_observations, development_end)
        cutoff = decisions.iloc[validation_start] - config.effective_embargo
        train = tuple(index for index in range(validation_start) if available.iloc[index] <= cutoff)
        if not train:
            continue
        inner_folds: list[WalkForwardFold] = []
        for inner_start in range(
            config.minimum_train_observations,
            validation_start,
            config.validation_observations,
        ):
            inner_end = min(inner_start + config.validation_observations, validation_start)
            inner_cutoff = decisions.iloc[inner_start] - config.effective_embargo
            inner_train = tuple(index for index in range(inner_start) if available.iloc[index] <= inner_cutoff)
            if inner_train:
                inner_folds.append(WalkForwardFold(inner_train, tuple(range(inner_start, inner_end))))
        folds.append(
            OuterFold(
                train,
                tuple(range(validation_start, validation_end)),
                tuple(inner_folds),
            )
        )
    return tuple(folds)


def _prediction_frame(
    data: pd.DataFrame,
    indices: tuple[int, ...],
    predictions: Sequence[float],
    timestamp_column: str,
) -> pd.DataFrame:
    values = list(predictions)
    if len(values) != len(indices):
        raise ValueError("predictor returned the wrong number of predictions")
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("predictor returned non-finite predictions")
    return pd.DataFrame(
        {
            timestamp_column: pd.to_datetime(data.iloc[list(indices)][timestamp_column], utc=True).tolist(),
            "prediction": [float(value) for value in values],
        }
    )


def run_frozen_protocol(
    data: pd.DataFrame,
    *,
    feature_columns: tuple[str, ...],
    label_column: str,
    predictor: Predictor,
    config: ValidationConfig,
    timestamp_column: str = "decision_timestamp",
    outcome_available_column: str = "outcome_available_at",
) -> FrozenProtocolResult:
    required = {timestamp_column, outcome_available_column, label_column, *feature_columns}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"frozen validation data is missing columns: {sorted(missing)}")
    boundary = select_final_boundary(data[timestamp_column], final_test_fraction=config.final_test_fraction)
    folds = make_outer_folds(
        data,
        boundary=boundary,
        config=config,
        timestamp_column=timestamp_column,
        outcome_available_column=outcome_available_column,
    )
    outer_frames: list[pd.DataFrame] = []
    for fold_number, fold in enumerate(folds):
        train = list(fold.train_index)
        validation = list(fold.validation_index)
        predictions = predictor(
            data.iloc[train].loc[:, list(feature_columns)].copy(),
            data.iloc[train][label_column].copy(),
            data.iloc[validation].loc[:, list(feature_columns)].copy(),
        )
        frame = _prediction_frame(data, fold.validation_index, predictions, timestamp_column)
        frame["fold"] = fold_number
        outer_frames.append(frame)

    decisions = _timestamps(data[timestamp_column], name=timestamp_column)
    available = _timestamp_values(data[outcome_available_column], name=outcome_available_column)
    final_cutoff = boundary.final_start - config.effective_embargo
    final_train = tuple(
        index
        for index in boundary.development_index
        if decisions.iloc[index] < boundary.final_start and available.iloc[index] <= final_cutoff
    )
    final_indices = list(boundary.final_index)
    final_values = predictor(
        data.iloc[list(final_train)].loc[:, list(feature_columns)].copy(),
        data.iloc[list(final_train)][label_column].copy(),
        data.iloc[final_indices].loc[:, list(feature_columns)].copy(),
    )
    final_predictions = _prediction_frame(data, boundary.final_index, final_values, timestamp_column)
    outer_predictions = (
        pd.concat(outer_frames, ignore_index=True)
        if outer_frames
        else pd.DataFrame(columns=[timestamp_column, "prediction", "fold"])
    )
    return FrozenProtocolResult(boundary, outer_predictions, final_predictions, final_train)


def _empty_metrics() -> BacktestMetrics:
    return calculate_backtest_metrics(pd.DataFrame(), pd.DataFrame(), periods_per_year=1)


def _segment_metrics(
    backtest: IntradayBacktestResult,
    mask: pd.Series,
    *,
    periods_per_year: int,
) -> BacktestMetrics:
    curve = backtest.equity_curve.loc[mask].copy()
    if curve.empty:
        return _empty_metrics()
    timestamps = pd.to_datetime(curve["timestamp"], utc=True)
    if backtest.trade_ledger.empty or "execution_timestamp" not in backtest.trade_ledger:
        trades = pd.DataFrame()
    else:
        execution = pd.to_datetime(backtest.trade_ledger["execution_timestamp"], utc=True)
        trades = backtest.trade_ledger.loc[execution.between(timestamps.min(), timestamps.max())].copy()
    return calculate_backtest_metrics(curve, trades, periods_per_year=periods_per_year)


def _latest_signal(
    evidence: StrategyRunEvidence,
    as_of: datetime,
) -> tuple[int, float, datetime | None, datetime | None]:
    if evidence.signals.empty:
        return 0, 0.0, None, None
    signals = evidence.signals.copy()
    required = {"decision_timestamp", "data_through", "signal", "strength"}
    if missing := required - set(signals.columns):
        raise ValueError(f"strategy signals are missing columns: {sorted(missing)}")
    signals["decision_timestamp"] = _timestamp_values(signals["decision_timestamp"], name="decision_timestamp")
    signals["data_through"] = _timestamp_values(signals["data_through"], name="data_through")
    eligible = signals.loc[signals["decision_timestamp"] <= pd.Timestamp(as_of)].sort_values(
        "decision_timestamp", kind="stable"
    )
    if eligible.empty:
        return 0, 0.0, None, None
    row = eligible.iloc[-1]
    return (
        int(row["signal"]),
        float(row["strength"]),
        row["decision_timestamp"].to_pydatetime(),
        row["data_through"].to_pydatetime(),
    )


def _placeholder_evaluation(
    request: EvaluationRequest,
    strategy_id: str,
    strategy_version: str,
    family: StrategyFamily,
    status: EvaluationStatus,
    reason: str,
) -> StrategyEvaluation:
    return StrategyEvaluation(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        family=family,
        status=status,
        status_reason=reason,
        promotion=PromotionDecision(False, (reason,)),
        dataset_hash=request.dataset_hash,
        symbol=request.symbol,
        interval=request.interval,
        mode=request.mode,
    )


def _finite_or_none(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def _fit_development_decision_calibration(
    evidence: StrategyRunEvidence,
    mapped_curve: pd.DataFrame,
    development_mask: pd.Series,
    current_signal: int,
) -> tuple[str, float, float, float, float, Mapping[str, Any]]:
    signals = evidence.signals.copy()
    required = {"decision_timestamp", "signal", "strength"}
    if missing := required - set(signals.columns):
        return "unavailable", 0.5, 0.0, 0.0, 0.0, {"reason": f"missing signal fields: {sorted(missing)}"}
    signals["decision_timestamp"] = pd.to_datetime(signals["decision_timestamp"], utc=True)
    outcomes = mapped_curve.loc[development_mask].copy()
    outcomes["decision_timestamp"] = pd.to_datetime(outcomes["decision_timestamp"], utc=True)
    joined = outcomes.merge(
        signals[["decision_timestamp", "signal", "strength"]],
        on="decision_timestamp",
        how="inner",
        validate="one_to_one",
    )
    joined["net_return"] = pd.to_numeric(joined["net_return"], errors="coerce")
    joined["signal"] = pd.to_numeric(joined["signal"], errors="coerce")
    joined = joined.loc[joined["signal"].isin((-1, 1)) & joined["net_return"].notna()].copy()
    if len(joined) < 5:
        return "unavailable", 0.5, 0.0, 0.0, 0.0, {"reason": "insufficient development calibration observations"}
    signed_returns = joined["signal"] * joined["net_return"]
    favorable = signed_returns > 0
    if favorable.nunique() < 2:
        return "unavailable", 0.5, 0.0, 0.0, 0.0, {"reason": "development outcomes contain one class"}
    success_probability = float((int(favorable.sum()) + 1) / (len(favorable) + 2))
    probability = success_probability if current_signal >= 0 else 1 - success_probability
    expected_edge = max(float(signed_returns.mean()), 0.0)
    expected_cost = (
        float(pd.to_numeric(joined.get("cost_return", 0.0), errors="coerce").abs().fillna(0).mean())
        if "cost_return" in joined
        else 0.0
    )
    uncertainty = float(signed_returns.std(ddof=1) / math.sqrt(len(signed_returns)))
    receipt = {
        "method": "development_only_beta_binomial_v1",
        "observations": len(joined),
        "outcomes_through": pd.to_datetime(joined["outcome_available_at"], utc=True).max().to_pydatetime(),
        "successes": int(favorable.sum()),
        "decision_rows_hash": canonical_hash(
            joined[
                [
                    "decision_timestamp",
                    "cost_decision_timestamp",
                    "signal",
                    "strength",
                    "net_return",
                    "cost_return",
                ]
            ].to_dict("records")
        ),
    }
    return "calibrated", probability, expected_edge, expected_cost, uncertainty, receipt


def evaluate_registry(request: EvaluationRequest) -> tuple[StrategyEvaluation, ...]:
    if request.as_of.tzinfo is not UTC:
        raise ValueError("as_of must be an explicit UTC datetime")
    chronology = _timestamps(request.chronology, name="chronology")
    outcome_availability = _timestamp_values(request.outcome_availability, name="outcome_availability")
    if len(outcome_availability) != len(chronology):
        raise ValueError("outcome_availability must align one-to-one with chronology")
    requested_as_of = pd.Timestamp(request.as_of)
    if chronology.max() > requested_as_of or outcome_availability.max() > requested_as_of:
        raise ValueError("sealed chronology and outcome availability cannot follow the requested as_of")
    validation_data = pd.DataFrame(
        {
            "decision_timestamp": chronology,
            "outcome_available_at": outcome_availability,
        }
    )
    boundary = select_final_boundary(chronology, final_test_fraction=request.config.final_test_fraction)
    expected_folds = make_outer_folds(validation_data, boundary=boundary, config=request.config)
    evaluations: list[StrategyEvaluation] = []
    for registered in request.registry.enabled():
        spec = registered.spec
        evidence = request.runs.get(spec.strategy_id)
        if evidence is None:
            evaluations.append(
                _placeholder_evaluation(
                    request,
                    spec.strategy_id,
                    spec.deterministic_version,
                    spec.family,
                    EvaluationStatus.UNAVAILABLE,
                    "no run evidence supplied",
                )
            )
            continue
        if evidence.unavailable_reason is not None:
            evaluations.append(
                _placeholder_evaluation(
                    request,
                    spec.strategy_id,
                    spec.deterministic_version,
                    spec.family,
                    EvaluationStatus.UNAVAILABLE,
                    evidence.unavailable_reason,
                )
            )
            continue
        if evidence.error_summary is not None or evidence.backtest is None:
            reason = evidence.error_summary or "backtest result is missing"
            evaluations.append(
                _placeholder_evaluation(
                    request,
                    spec.strategy_id,
                    spec.deterministic_version,
                    spec.family,
                    EvaluationStatus.FAILED,
                    reason,
                )
            )
            continue

        try:
            sealed_evidence = _seal_development_evidence(
                evidence,
                boundary,
                chronology,
                expected_folds,
                as_of=request.as_of,
                dataset_hash=request.dataset_hash,
                cohort_id=request.cohort_id,
                validation_config_hash=validation_policy_hash(request.config),
            )
        except ValueError as error:
            evaluations.append(
                _placeholder_evaluation(
                    request,
                    spec.strategy_id,
                    spec.deterministic_version,
                    spec.family,
                    EvaluationStatus.FAILED,
                    str(error),
                )
            )
            continue

        curve = evidence.backtest.equity_curve
        required_curve_columns = {
            "timestamp",
            "decision_timestamp",
            "cost_decision_timestamp",
            "outcome_available_at",
            "net_return",
            "cost_return",
        }
        if missing_curve_columns := required_curve_columns - set(curve.columns):
            reason = (
                f"economic cost evidence is missing fields: {sorted(missing_curve_columns)}"
                if missing_curve_columns & {"cost_return", "cost_decision_timestamp"}
                else f"backtest curve lacks causal outcome mapping: {sorted(missing_curve_columns)}"
            )
            evaluations.append(
                _placeholder_evaluation(
                    request,
                    spec.strategy_id,
                    spec.deterministic_version,
                    spec.family,
                    EvaluationStatus.FAILED,
                    reason,
                )
            )
            continue
        mapped_mask = pd.to_datetime(curve["decision_timestamp"], utc=True, errors="coerce").notna()
        if not mapped_mask.any():
            evaluations.append(
                _placeholder_evaluation(
                    request,
                    spec.strategy_id,
                    spec.deterministic_version,
                    spec.family,
                    EvaluationStatus.FAILED,
                    "backtest curve has no mapped decision outcomes",
                )
            )
            continue
        mapped_backtest = IntradayBacktestResult(
            curve.loc[mapped_mask].copy(),
            evidence.backtest.trade_ledger,
            evidence.backtest.rejection_ledger,
            evidence.backtest.metrics,
        )
        mapped_curve = mapped_backtest.equity_curve
        cost_timestamps = pd.to_datetime(
            mapped_curve["cost_decision_timestamp"],
            utc=True,
            errors="coerce",
        )
        costs = pd.to_numeric(mapped_curve["cost_return"], errors="coerce")
        mapped_decisions = pd.to_datetime(mapped_curve["decision_timestamp"], utc=True, errors="coerce")
        if (
            cost_timestamps.isna().any()
            or not cost_timestamps.equals(mapped_decisions)
            or costs.isna().any()
            or not costs.map(math.isfinite).all()
        ):
            evaluations.append(
                _placeholder_evaluation(
                    request,
                    spec.strategy_id,
                    spec.deterministic_version,
                    spec.family,
                    EvaluationStatus.FAILED,
                    "economic cost evidence is missing, non-finite, or decision-misaligned",
                )
            )
            continue
        timestamps = _timestamp_values(mapped_curve["timestamp"], name="backtest timestamp")
        if timestamps.max() > requested_as_of:
            evaluations.append(
                _placeholder_evaluation(
                    request,
                    spec.strategy_id,
                    spec.deterministic_version,
                    spec.family,
                    EvaluationStatus.FAILED,
                    "backtest evidence cannot follow the requested as_of",
                )
            )
            continue
        decision_timestamps = _timestamp_values(
            mapped_curve["decision_timestamp"], name="backtest decision timestamp"
        ).set_axis(mapped_curve.index)
        outcome_timestamps = _timestamp_values(
            mapped_curve["outcome_available_at"], name="backtest outcome availability"
        ).set_axis(mapped_curve.index)
        if (outcome_timestamps < decision_timestamps).any() or outcome_timestamps.max() > requested_as_of:
            evaluations.append(
                _placeholder_evaluation(
                    request,
                    spec.strategy_id,
                    spec.deterministic_version,
                    spec.family,
                    EvaluationStatus.FAILED,
                    "backtest outcome mapping violates requested chronology",
                )
            )
            continue
        if tuple(decision_timestamps) != tuple(chronology) or tuple(outcome_timestamps) != tuple(outcome_availability):
            evaluations.append(
                _placeholder_evaluation(
                    request,
                    spec.strategy_id,
                    spec.deterministic_version,
                    spec.family,
                    EvaluationStatus.FAILED,
                    "backtest outcomes do not match the sealed decision-to-outcome map",
                )
            )
            continue
        development_mask = (decision_timestamps < boundary.final_start) & (outcome_timestamps < boundary.final_start)
        final_mask = decision_timestamps >= boundary.final_start
        development = _segment_metrics(
            mapped_backtest, development_mask, periods_per_year=request.config.periods_per_year
        )
        final = _segment_metrics(mapped_backtest, final_mask, periods_per_year=request.config.periods_per_year)
        development_returns = pd.to_numeric(mapped_curve.loc[development_mask, "net_return"], errors="coerce").dropna()
        downside = development_returns.loc[development_returns < 0]
        downside_risk = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0
        try:
            cost_survives = doubled_cost_survival(mapped_curve.loc[development_mask]).survives
        except ValueError:
            cost_survives = False
        dsr: float | None = None
        if len(sealed_evidence.trial_sharpes) >= 2 and development.sharpe == development.sharpe:
            try:
                dsr = deflated_sharpe_probability(
                    development.sharpe,
                    observations=len(development_returns),
                    trial_sharpes=sealed_evidence.trial_sharpes,
                    skew=float(development_returns.skew()) if len(development_returns) >= 3 else 0.0,
                    kurtosis=float(development_returns.kurtosis() + 3) if len(development_returns) >= 4 else 3.0,
                )
            except ValueError as error:
                evaluations.append(
                    _placeholder_evaluation(
                        request,
                        spec.strategy_id,
                        spec.deterministic_version,
                        spec.family,
                        EvaluationStatus.FAILED,
                        f"malformed trial evidence: {error}",
                    )
                )
                continue
        stability = sealed_evidence.fold_stability
        signal, strength, decision_timestamp, data_through = _latest_signal(evidence, request.as_of)
        (
            calibration_status,
            current_probability,
            expected_edge,
            expected_cost,
            uncertainty,
            decision_calibration,
        ) = _fit_development_decision_calibration(evidence, mapped_curve, development_mask, signal)
        development_sharpe = _finite_or_none(development.sharpe)
        development_maximum_drawdown = _finite_or_none(development.maximum_drawdown)
        final_sharpe = _finite_or_none(final.sharpe)
        promotion_inputs = {
            "status": EvaluationStatus.EVALUATED.value,
            "development_sharpe": development_sharpe,
            "downside_risk": downside_risk,
            "maximum_drawdown": development_maximum_drawdown,
            "calibration_error": sealed_evidence.calibration_error,
            "fold_stability": stability,
            "cost_survives": cost_survives,
            "observations": len(development_returns),
            "trades": development.trades,
            "dsr_probability": dsr,
            "trial_sharpes": sealed_evidence.trial_sharpes,
            "causal_audit_passed": evidence.causal_audit_passed,
            "robustness_available": sealed_evidence.robustness is not None,
            "median_walk_forward_net_edge": (
                sealed_evidence.robustness.median_walk_forward_net_edge if sealed_evidence.robustness else None
            ),
            "pbo_probability": sealed_evidence.robustness.pbo_probability if sealed_evidence.robustness else None,
            "parameter_neighborhood_stable": (
                sealed_evidence.robustness.parameter_neighborhood_stable if sealed_evidence.robustness else None
            ),
            "parameter_neighbor_positive_fraction": (
                sealed_evidence.robustness.parameter_neighbor_positive_fraction if sealed_evidence.robustness else None
            ),
            "parameter_neighbor_median_ratio": (
                sealed_evidence.robustness.parameter_neighbor_median_ratio if sealed_evidence.robustness else None
            ),
        }
        reasons = promotion_reasons(promotion_inputs, request.config)
        promotion = PromotionDecision(not reasons, reasons)
        promotion_record = {"promoted": promotion.promoted, "reasons": promotion.reasons}
        promotion_payload = {
            "promotion_inputs": promotion_inputs,
            "promotion_decision": promotion_record,
        }
        context_record = {
            "dataset_hash": request.dataset_hash,
            "strategy_id": spec.strategy_id,
            "strategy_version": spec.deterministic_version,
            "family": spec.family.value,
            "symbol": request.symbol,
            "interval": request.interval.value,
            "mode": request.mode.value,
        }
        chronology_record = tuple(timestamp.to_pydatetime() for timestamp in chronology)
        availability_record = tuple(timestamp.to_pydatetime() for timestamp in outcome_availability)
        boundary_record = {
            "final_start": boundary.final_start.to_pydatetime(),
            "development_index": boundary.development_index,
            "final_index": boundary.final_index,
        }
        promotion_timestamps = [
            pd.Timestamp(timestamp).to_pydatetime() for timestamp in mapped_curve.loc[development_mask, "timestamp"]
        ]
        promotion_timestamps.extend(item["evaluated_at"] for item in sealed_evidence.provenance["trials"])
        promotion_timestamps.extend(item["evaluated_at"] for item in sealed_evidence.provenance["folds"])
        promotion_evidence_through = max(promotion_timestamps)
        validation_config_record = _config_record(request.config)
        validation_snapshot = {
            "schema_version": 2,
            "context": context_record,
            "chronology": chronology_record,
            "chronology_hash": canonical_hash(chronology_record),
            "outcome_availability": availability_record,
            "outcome_availability_hash": canonical_hash(availability_record),
            "validation_config": validation_config_record,
            "validation_policy_hash": validation_policy_hash(request.config),
            "evaluated_as_of": request.as_of,
            "promotion_evidence_through": promotion_evidence_through,
            "final_boundary": boundary_record,
            "fold_plan": _fold_plan(expected_folds),
            "trial_records": sealed_evidence.provenance["trials"],
            "fold_records": sealed_evidence.provenance["folds"],
            "derived": promotion_inputs,
            "promotion": promotion_record,
        }
        evidence_provenance = _deep_freeze(
            {
                **dict(sealed_evidence.provenance),
                **promotion_payload,
                "promotion_evidence_hash": canonical_hash(promotion_payload),
                "validation_snapshot": validation_snapshot,
                "validation_snapshot_hash": canonical_hash(validation_snapshot),
                "decision_calibration": decision_calibration,
                "decision_calibration_hash": canonical_hash(decision_calibration),
            }
        )
        evaluations.append(
            StrategyEvaluation(
                strategy_id=spec.strategy_id,
                strategy_version=spec.deterministic_version,
                family=spec.family,
                status=EvaluationStatus.EVALUATED,
                status_reason="evaluation completed",
                promotion=promotion,
                development_sharpe=development_sharpe,
                final_sharpe=final_sharpe,
                downside_risk=downside_risk,
                development_maximum_drawdown=development_maximum_drawdown,
                calibration_error=sealed_evidence.calibration_error,
                fold_stability=stability,
                cost_survives=cost_survives,
                observations=len(development_returns),
                trades=development.trades,
                dsr_probability=dsr,
                trial_sharpes=sealed_evidence.trial_sharpes,
                causal_audit_passed=evidence.causal_audit_passed,
                current_signal=signal,
                current_strength=strength,
                current_probability=current_probability,
                calibration_status=calibration_status,
                economic_evidence_status="authenticated",
                expected_edge=expected_edge,
                expected_cost=expected_cost,
                uncertainty=uncertainty,
                robustness=sealed_evidence.robustness,
                decision_timestamp=decision_timestamp,
                data_through=data_through,
                dataset_hash=request.dataset_hash,
                symbol=request.symbol,
                interval=request.interval,
                mode=request.mode,
                evidence_provenance=evidence_provenance,
            )
        )
    return tuple(evaluations)


__all__ = [
    "DEFAULT_VALIDATION_CONFIG",
    "EvaluationRequest",
    "EvaluationStatus",
    "FinalBoundary",
    "FoldEvidence",
    "FrozenProtocolResult",
    "OuterFold",
    "PromotionDecision",
    "RobustnessEvidence",
    "StrategyEvaluation",
    "StrategyRunEvidence",
    "TrialEvidence",
    "ValidationConfig",
    "WalkForwardFold",
    "evaluate_registry",
    "make_outer_folds",
    "run_frozen_protocol",
    "select_final_boundary",
    "validation_policy_hash",
]
