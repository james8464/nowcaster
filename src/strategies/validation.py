from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import numpy as np
import pandas as pd

from src.backtest.intraday import IntradayBacktestResult
from src.backtest.metrics import BacktestMetrics, calculate_backtest_metrics
from src.backtest.robustness import deflated_sharpe_probability, doubled_cost_survival
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, StrategyFamily, StrategyMode


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

    def __post_init__(self) -> None:
        if not 0 < self.final_test_fraction < 1:
            raise ValueError("final_test_fraction must be in (0, 1)")
        if self.minimum_train_observations <= 0 or self.validation_observations <= 0:
            raise ValueError("training and validation observations must be positive")
        if any(value < timedelta(0) for value in (self.forecast_horizon, self.publication_delay, self.embargo)):
            raise ValueError("horizon, publication delay, and embargo cannot be negative")
        if self.periods_per_year <= 0 or self.minimum_trades < 0 or self.minimum_development_observations <= 0:
            raise ValueError("period and evidence counts must be valid")
        if not 0 <= self.maximum_drawdown <= 1 or not 0 <= self.minimum_dsr_probability <= 1:
            raise ValueError("promotion probability and drawdown thresholds must be in [0, 1]")

    @property
    def effective_embargo(self) -> timedelta:
        return max(self.embargo, self.forecast_horizon + self.publication_delay)


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
class StrategyRunEvidence:
    backtest: IntradayBacktestResult | None = None
    signals: pd.DataFrame = field(default_factory=pd.DataFrame)
    trial_sharpes: tuple[float, ...] = ()
    causal_audit_passed: bool = True
    calibration_error: float = 0.0
    fold_stability: float | None = None
    expected_edge: float = 0.0
    expected_cost: float = 0.0
    uncertainty: float = 0.0
    unavailable_reason: str | None = None
    error_summary: str | None = None


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
    current_volatility: float = 1.0
    expected_edge: float = 0.0
    expected_cost: float = 0.0
    uncertainty: float = 0.0
    decision_timestamp: datetime | None = None
    data_through: datetime | None = None
    dataset_hash: str = ""
    symbol: str = ""
    interval: BarInterval = BarInterval.ONE_DAY
    mode: StrategyMode = StrategyMode.FROZEN


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    registry: StrategyRegistry
    runs: Mapping[str, StrategyRunEvidence]
    chronology: Sequence[object] | pd.Series
    as_of: datetime
    mode: StrategyMode
    dataset_hash: str
    symbol: str
    interval: BarInterval
    config: ValidationConfig = field(default_factory=ValidationConfig)


Predictor = Callable[[pd.DataFrame, pd.Series, pd.DataFrame], Sequence[float]]


def _timestamps(values: Sequence[object] | pd.Series, *, name: str) -> pd.Series:
    result = pd.Series(pd.to_datetime(values, utc=True, errors="coerce")).reset_index(drop=True)
    if result.empty or result.isna().any():
        raise ValueError(f"{name} must contain valid timestamps")
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
    available = pd.Series(pd.to_datetime(data[outcome_available_column], utc=True, errors="coerce")).reset_index(
        drop=True
    )
    if available.isna().any() or (available < decisions).any():
        raise ValueError("outcomes must become available at or after their decisions")
    folds: list[OuterFold] = []
    development_end = len(boundary.development_index)
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
    available = pd.Series(pd.to_datetime(data[outcome_available_column], utc=True, errors="coerce")).reset_index(
        drop=True
    )
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
    signals["decision_timestamp"] = pd.to_datetime(signals["decision_timestamp"], utc=True)
    signals["data_through"] = pd.to_datetime(signals["data_through"], utc=True)
    eligible = signals.loc[signals["decision_timestamp"] <= pd.Timestamp(as_of)].sort_values(
        "decision_timestamp", kind="stable"
    )
    if eligible.empty:
        return 0, 0.0, None, None
    row = eligible.iloc[-1]
    return int(row["signal"]), float(row["strength"]), row["decision_timestamp"].to_pydatetime(), row[
        "data_through"
    ].to_pydatetime()


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


def evaluate_registry(request: EvaluationRequest) -> tuple[StrategyEvaluation, ...]:
    if request.as_of.tzinfo is not UTC:
        raise ValueError("as_of must be an explicit UTC datetime")
    boundary = select_final_boundary(request.chronology, final_test_fraction=request.config.final_test_fraction)
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

        curve = evidence.backtest.equity_curve
        if "timestamp" not in curve or "net_return" not in curve:
            evaluations.append(
                _placeholder_evaluation(
                    request,
                    spec.strategy_id,
                    spec.deterministic_version,
                    spec.family,
                    EvaluationStatus.FAILED,
                    "backtest curve lacks timestamped net returns",
                )
            )
            continue
        timestamps = pd.to_datetime(curve["timestamp"], utc=True)
        development_mask = timestamps < boundary.final_start
        development = _segment_metrics(
            evidence.backtest, development_mask, periods_per_year=request.config.periods_per_year
        )
        final = _segment_metrics(
            evidence.backtest, ~development_mask, periods_per_year=request.config.periods_per_year
        )
        development_returns = pd.to_numeric(curve.loc[development_mask, "net_return"], errors="coerce").dropna()
        downside = development_returns.loc[development_returns < 0]
        downside_risk = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0
        try:
            cost_survives = doubled_cost_survival(curve.loc[development_mask]).survives
        except ValueError:
            cost_survives = False
        dsr: float | None = None
        if len(evidence.trial_sharpes) >= 2 and development.sharpe == development.sharpe:
            dsr = deflated_sharpe_probability(
                development.sharpe,
                observations=len(development_returns),
                trial_sharpes=evidence.trial_sharpes,
                skew=float(development_returns.skew()) if len(development_returns) >= 3 else 0.0,
                kurtosis=float(development_returns.kurtosis() + 3) if len(development_returns) >= 4 else 3.0,
            )
        stability = evidence.fold_stability if evidence.fold_stability is not None else 1.0
        reasons: list[str] = []
        if len(development_returns) < request.config.minimum_development_observations:
            reasons.append("insufficient development observations")
        if development.trades < request.config.minimum_trades:
            reasons.append("insufficient development trades")
        if development.sharpe != development.sharpe or development.sharpe <= 0:
            reasons.append("development Sharpe is not positive")
        invalid_drawdown = development.maximum_drawdown != development.maximum_drawdown
        excessive_drawdown = abs(development.maximum_drawdown) > request.config.maximum_drawdown
        if invalid_drawdown or excessive_drawdown:
            reasons.append("development drawdown exceeds the gate")
        if stability < 0.5:
            reasons.append("walk-forward fold stability failed")
        if not cost_survives:
            reasons.append("doubled-cost survival failed")
        if dsr is None:
            reasons.append("observed trial Sharpe vector is unavailable")
        elif dsr < request.config.minimum_dsr_probability:
            reasons.append("Deflated Sharpe probability failed")
        if not evidence.causal_audit_passed:
            reasons.append("causal audit failed")
        signal, strength, decision_timestamp, data_through = _latest_signal(evidence, request.as_of)
        evaluations.append(
            StrategyEvaluation(
                strategy_id=spec.strategy_id,
                strategy_version=spec.deterministic_version,
                family=spec.family,
                status=EvaluationStatus.EVALUATED,
                status_reason="evaluation completed",
                promotion=PromotionDecision(not reasons, tuple(reasons)),
                development_sharpe=_finite_or_none(development.sharpe),
                final_sharpe=_finite_or_none(final.sharpe),
                downside_risk=downside_risk,
                calibration_error=evidence.calibration_error,
                fold_stability=stability,
                cost_survives=cost_survives,
                observations=len(development_returns),
                trades=development.trades,
                dsr_probability=dsr,
                trial_sharpes=evidence.trial_sharpes,
                causal_audit_passed=evidence.causal_audit_passed,
                current_signal=signal,
                current_strength=strength,
                current_probability=0.5 + signal * strength * 0.5,
                expected_edge=evidence.expected_edge,
                expected_cost=evidence.expected_cost,
                uncertainty=evidence.uncertainty,
                decision_timestamp=decision_timestamp,
                data_through=data_through,
                dataset_hash=request.dataset_hash,
                symbol=request.symbol,
                interval=request.interval,
                mode=request.mode,
            )
        )
    return tuple(evaluations)


__all__ = [
    "EvaluationRequest",
    "EvaluationStatus",
    "FinalBoundary",
    "FrozenProtocolResult",
    "OuterFold",
    "PromotionDecision",
    "StrategyEvaluation",
    "StrategyRunEvidence",
    "ValidationConfig",
    "WalkForwardFold",
    "evaluate_registry",
    "make_outer_folds",
    "run_frozen_protocol",
    "select_final_boundary",
]
