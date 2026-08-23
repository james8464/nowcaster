from __future__ import annotations

import json
import math
import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd

from src.backtest.execution import ExecutionAssumptions
from src.backtest.intraday import RiskLimits, run_intraday_backtest
from src.backtest.metrics import calculate_backtest_metrics
from src.database.engine import Database
from src.learning.grammar import RuleNode, semantic_dedupe
from src.strategies.types import BarInterval, StrategyMode, canonical_hash
from src.strategies.validation import WalkForwardFold

TrialStatus = Literal["succeeded", "failed", "invalid", "budget_stop"]
RuleState = Literal["shadow", "paper", "active", "retired"]


def _explicit_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is not UTC:
        raise ValueError(f"{name} must be an explicit UTC datetime")
    return value


@dataclass(frozen=True, slots=True)
class FoldMetrics:
    net_sharpe: float
    maximum_drawdown: float
    turnover: float

    def __post_init__(self) -> None:
        values = (self.net_sharpe, self.maximum_drawdown, self.turnover)
        if any(isinstance(value, bool) or not math.isfinite(float(value)) for value in values):
            raise ValueError("inner-fold metrics must be finite numbers")
        if self.turnover < 0:
            raise ValueError("inner-fold turnover cannot be negative")


@dataclass(frozen=True, slots=True)
class FitnessPenalties:
    drawdown: float = 0.5
    turnover: float = 0.05
    instability: float = 0.25
    complexity: float = 0.01

    def __post_init__(self) -> None:
        values = (self.drawdown, self.turnover, self.instability, self.complexity)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("fitness penalties must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class RuleCandidate:
    rule: RuleNode
    candidate_hash: str = ""
    strategy_id: str = ""
    version: str = ""
    state: RuleState = "shadow"
    discovered_at: datetime | None = None
    evidence_through: datetime | None = None

    def __post_init__(self) -> None:
        identity = canonical_hash({"grammar_version": 1, "rule": self.rule.canonical})
        if self.candidate_hash and self.candidate_hash != identity:
            raise ValueError("candidate hash does not match the canonical rule")
        object.__setattr__(self, "candidate_hash", identity)
        strategy_id = self.strategy_id.strip() or f"learned-{identity[:16]}"
        version = self.version.strip() or f"1.0.0+{identity[:12]}"
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "version", version)
        if self.state not in {"shadow", "paper", "active", "retired"}:
            raise ValueError("candidate state is invalid")
        if self.discovered_at is not None:
            _explicit_utc(self.discovered_at, "candidate discovered_at")
        if self.evidence_through is not None:
            _explicit_utc(self.evidence_through, "candidate evidence_through")
        if (
            self.discovered_at is not None
            and self.evidence_through is not None
            and self.evidence_through < self.discovered_at
        ):
            raise ValueError("candidate evidence cannot precede discovery")

    @classmethod
    def from_rule(cls, experiment: LearningExperiment, rule: RuleNode) -> RuleCandidate:
        identity = canonical_hash({"grammar_version": 1, "rule": rule.canonical})
        run_version = canonical_hash(
            {"learning_run_id": experiment.learning_run_id, "started_at": experiment.started_at}
        )[:8]
        return cls(
            rule=rule,
            version=f"1.0.0+{identity[:8]}.{run_version}",
            discovered_at=experiment.started_at,
            evidence_through=experiment.as_of,
        )


Evaluator = Callable[[RuleCandidate, pd.DataFrame, pd.DataFrame], FoldMetrics]


@dataclass(frozen=True, slots=True)
class LearningExperiment:
    learning_run_id: str
    dataset_hash: str
    symbol: str
    interval: BarInterval
    started_at: datetime
    as_of: datetime
    sealed_final_start: datetime
    seed: int
    evaluation_budget: int
    inner_folds: tuple[WalkForwardFold, ...]
    indicators: tuple[str, ...]
    thresholds: tuple[float, ...]
    evaluator: Evaluator | None = None
    evaluator_version: str = "1"
    evaluator_cost_contract: str = "net-cost-aware-fold-metrics-v1"
    penalties: FitnessPenalties = field(default_factory=FitnessPenalties)
    seed_rules: tuple[RuleNode, ...] = ()
    max_depth: int = 4
    max_nodes: int = 15
    maximum_lag: int = 2
    execution_assumptions: ExecutionAssumptions = field(default_factory=ExecutionAssumptions)
    risk_limits: RiskLimits = field(default_factory=RiskLimits)
    execution_model_version: str = "task4-intraday-directional-v1"
    return_column: str = "forward_return"
    timestamp_column: str = "decision_timestamp"
    availability_column: str = "available_at"
    outcome_availability_column: str = "outcome_available_at"
    database: Database | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        for name in ("learning_run_id", "dataset_hash", "symbol"):
            normalized = str(getattr(self, name)).strip()
            if not normalized:
                raise ValueError("learning experiment identifiers must not be empty")
            object.__setattr__(self, name, normalized.upper() if name == "symbol" else normalized)
        object.__setattr__(self, "interval", BarInterval(self.interval))
        for name in ("started_at", "as_of", "sealed_final_start"):
            _explicit_utc(getattr(self, name), f"experiment {name}")
        if not self.started_at <= self.as_of < self.sealed_final_start:
            raise ValueError("learning timestamps must precede the sealed final boundary")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("learning seed must be an integer")
        if self.evaluation_budget <= 0:
            raise ValueError("evaluation budget must be positive")
        evaluator_version = self.evaluator_version.strip()
        evaluator_cost_contract = self.evaluator_cost_contract.strip()
        if not evaluator_version or not evaluator_cost_contract:
            raise ValueError("evaluator version and cost contract must not be empty")
        object.__setattr__(self, "evaluator_version", evaluator_version)
        object.__setattr__(self, "evaluator_cost_contract", evaluator_cost_contract)
        if not self.inner_folds:
            raise ValueError("learning requires chronological inner folds")
        names = tuple(sorted({name.strip() for name in self.indicators if name.strip()}))
        thresholds = tuple(sorted({float(value) for value in self.thresholds if math.isfinite(float(value))}))
        if not names or not thresholds:
            raise ValueError("learning requires indicators and a finite bounded parameter grid")
        object.__setattr__(self, "indicators", names)
        object.__setattr__(self, "thresholds", thresholds)
        if self.max_depth < 3 or self.max_nodes < 3 or self.maximum_lag < 1:
            raise ValueError("grammar bounds cannot exclude one lagged comparison")
        if not isinstance(self.execution_assumptions, ExecutionAssumptions):
            raise ValueError("execution_assumptions must use Task 4's typed contract")
        if not isinstance(self.risk_limits, RiskLimits):
            raise ValueError("risk_limits must use Task 4's typed contract")
        if self.execution_assumptions.session_close is not None:
            raise ValueError("learning does not accept an unauthenticated execution callback")
        execution_model_version = self.execution_model_version.strip()
        if not execution_model_version:
            raise ValueError("execution_model_version must not be empty")
        object.__setattr__(self, "execution_model_version", execution_model_version)
        if not self.seed_rules:
            object.__setattr__(
                self,
                "seed_rules",
                (
                    RuleNode.compare(
                        "gt",
                        RuleNode.indicator(names[0], lag=1),
                        RuleNode.number(thresholds[0]),
                    ),
                ),
            )
        if any(not isinstance(rule, RuleNode) for rule in self.seed_rules):
            raise ValueError("seed rules must be typed RuleNode instances")
        ordered_seed_rules = tuple(
            sorted(self.seed_rules, key=lambda rule: (rule.semantic_hash, rule.render()))
        )
        object.__setattr__(
            self,
            "seed_rules",
            semantic_dedupe(ordered_seed_rules),
        )


@dataclass(frozen=True, slots=True)
class LearningTrial:
    ordinal: int
    trial_id: str
    candidate: RuleCandidate
    evaluated_at: datetime
    status: TrialStatus
    fold_metrics: tuple[FoldMetrics, ...] = ()
    fitness: float | None = None
    error_summary: str | None = None


@dataclass(frozen=True, slots=True)
class LearningResult:
    learning_run_id: str
    trials: tuple[LearningTrial, ...]
    candidates: tuple[RuleCandidate, ...]
    best_candidate: RuleCandidate | None
    stopped_reason: str

    @property
    def trial_count(self) -> int:
        return len(self.trials)


def calculate_fitness(
    candidate: RuleCandidate,
    folds: Sequence[FoldMetrics],
    penalties: FitnessPenalties,
) -> float:
    if not folds:
        raise ValueError("fitness requires inner validation fold metrics")
    sharpes = [fold.net_sharpe for fold in folds]
    drawdowns = [abs(fold.maximum_drawdown) for fold in folds]
    turnovers = [fold.turnover for fold in folds]
    instability = statistics.pstdev(sharpes) if len(sharpes) > 1 else 0.0
    mdl_cost = float(candidate.rule.node_count)
    return float(
        statistics.median(sharpes)
        - penalties.drawdown * statistics.median(drawdowns)
        - penalties.turnover * statistics.median(turnovers)
        - penalties.instability * instability
        - penalties.complexity * mdl_cost
    )


def _utc_column(frame: pd.DataFrame, column: str) -> pd.Series:
    timestamps: list[pd.Timestamp] = []
    for value in frame[column]:
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{column} must contain explicit UTC timestamps") from error
        if pd.isna(timestamp) or timestamp.tzinfo is None or str(timestamp.tz) != "UTC":
            raise ValueError(f"{column} must contain explicit UTC timestamps")
        timestamps.append(timestamp.tz_convert("UTC"))
    return pd.Series(timestamps, index=frame.index, dtype="datetime64[ns, UTC]")


def _development_frame(experiment: LearningExperiment, bars: pd.DataFrame) -> pd.DataFrame:
    forbidden = [
        str(column)
        for column in bars.columns
        if str(column).lower().startswith("final_") or "sealed" in str(column).lower()
    ]
    if forbidden:
        raise ValueError(f"sealed or final evidence is forbidden during search: {sorted(forbidden)}")
    generic_columns = (
        experiment.timestamp_column,
        experiment.availability_column,
        experiment.outcome_availability_column,
        "finalized",
        *experiment.indicators,
    )
    required = set(generic_columns)
    execution_columns: tuple[str, ...] = ()
    if experiment.evaluator is None:
        execution_columns = (
            "symbol",
            "open_timestamp",
            "close_timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        )
        required.update(execution_columns)
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"development evidence is missing columns: {sorted(missing)}")
    allowed_order = (*generic_columns, *execution_columns)
    if experiment.return_column in bars:
        allowed_order = (*allowed_order, experiment.return_column)
    selected = tuple(dict.fromkeys(allowed_order))
    frame = bars.loc[:, list(selected)].copy()
    for column in (
        experiment.timestamp_column,
        experiment.availability_column,
        experiment.outcome_availability_column,
    ):
        frame[column] = _utc_column(frame, column)
    if experiment.evaluator is None:
        frame["open_timestamp"] = _utc_column(frame, "open_timestamp")
        frame["close_timestamp"] = _utc_column(frame, "close_timestamp")
        if not frame["symbol"].astype(str).str.upper().eq(experiment.symbol).all():
            raise ValueError("development execution bars must match the experiment symbol")
        execution_numeric = ("open", "high", "low", "close", "volume")
        for column in execution_numeric:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame[list(execution_numeric)].isna().any().any() or not np.isfinite(
            frame[list(execution_numeric)].to_numpy(dtype=float)
        ).all():
            raise ValueError("development execution prices and volume must be finite numbers")
        if (frame["close_timestamp"] <= frame["open_timestamp"]).any() or (frame["volume"] < 0).any():
            raise ValueError("development execution bar chronology or volume is malformed")
    if not frame["finalized"].map(lambda value: value is True or value == 1).all():
        raise ValueError("learning requires finalized development bars")
    if (frame[experiment.timestamp_column] >= experiment.sealed_final_start).any():
        raise ValueError("search cannot inspect rows at or beyond the sealed final boundary")
    if (frame[experiment.availability_column] > experiment.as_of).any():
        raise ValueError("development inputs are not available as-of the learning experiment")
    if (frame[experiment.availability_column] > frame[experiment.timestamp_column]).any():
        raise ValueError("each finalized learner input must be available by its decision time")
    if (frame[experiment.outcome_availability_column] > experiment.as_of).any():
        raise ValueError("development outcomes are not available as-of the learning experiment")
    if (frame[experiment.outcome_availability_column] <= frame[experiment.timestamp_column]).any():
        raise ValueError("development outcomes must be available strictly after their decisions")
    frame = frame.sort_values(experiment.timestamp_column, kind="stable").reset_index(drop=True)
    if frame[experiment.timestamp_column].duplicated().any():
        raise ValueError("development decision timestamps must be unique")
    for indicator in experiment.indicators:
        values = pd.to_numeric(frame[indicator], errors="coerce")
        if values.isna().any() or not np.isfinite(values).all():
            raise ValueError(f"indicator '{indicator}' must contain finite finalized values")
        frame[indicator] = values.astype(float)
    return frame


def _validate_rule_domain(experiment: LearningExperiment, rule: RuleNode) -> None:
    rule.validate_bounds(max_depth=experiment.max_depth, max_nodes=experiment.max_nodes)
    for node in (rule, *tuple(_descendants(rule))):
        if node.operator.value == "indicator":
            if node.name not in experiment.indicators:
                raise ValueError(f"indicator '{node.name}' is not a declared indicator")
            if node.lag < 1 or node.lag > experiment.maximum_lag:
                raise ValueError(
                    f"indicator lag {node.lag} is outside maximum lag {experiment.maximum_lag}"
                )
            if node.parameters:
                raise ValueError("indicator parameters are outside the declared bounded domain")
        elif node.operator.value == "number" and node.value not in experiment.thresholds:
            raise ValueError(f"numeric threshold {node.value} is outside the declared threshold grid")


def _descendants(rule: RuleNode):
    for child in rule.children:
        yield child
        yield from _descendants(child)


def _atomic_rules(experiment: LearningExperiment) -> tuple[RuleNode, ...]:
    atoms = [
        RuleNode.compare(operator, RuleNode.indicator(name, lag=lag), RuleNode.number(threshold))
        for operator in ("gt", "lt")
        for name in experiment.indicators
        for lag in range(1, experiment.maximum_lag + 1)
        for threshold in experiment.thresholds
    ]
    atoms.extend(
        RuleNode.cross(direction, RuleNode.indicator(left, lag=1), RuleNode.indicator(right, lag=1))
        for direction in ("above", "below")
        for left in experiment.indicators
        for right in experiment.indicators
        if left < right
    )
    return tuple(sorted(semantic_dedupe(tuple(atoms)), key=lambda node: node.semantic_hash))


def _candidate_rules(experiment: LearningExperiment) -> tuple[RuleNode, ...]:
    generated = list(_atomic_rules(experiment))
    random.Random(experiment.seed).shuffle(generated)
    return semantic_dedupe(tuple(experiment.seed_rules) + tuple(generated))


def _next_rule(experiment: LearningExperiment, trials: Sequence[LearningTrial]) -> RuleNode | None:
    initial = _candidate_rules(experiment)
    ordinal = len(trials)
    warmup = min(4, len(initial))
    if ordinal < warmup:
        return initial[ordinal]

    seen = {trial.candidate.candidate_hash for trial in trials}
    ranked = sorted(
        (trial for trial in trials if trial.status == "succeeded" and trial.fitness is not None),
        key=lambda trial: (-float(trial.fitness), trial.candidate.candidate_hash),
    )
    variants: list[RuleNode] = []
    atoms = _atomic_rules(experiment)
    for parent_trial in ranked[:4]:
        parent = parent_trial.candidate.rule
        mates = [atom for atom in atoms if atom.semantic_hash != parent.semantic_hash]
        random.Random(
            int(
                canonical_hash(
                    {
                        "seed": experiment.seed,
                        "ordinal": ordinal,
                        "parent": parent.semantic_hash,
                    }
                )[:16],
                16,
            )
        ).shuffle(mates)
        for mate in mates:
            variants.extend((RuleNode.all_of(parent, mate), RuleNode.any_of(parent, mate)))
        # Atomic alternatives are the bounded parameter/operator/lag mutations.
        variants.extend(mates)
    for position, left in enumerate(ranked[:4]):
        for right in ranked[position + 1 : 4]:
            variants.extend(
                (
                    RuleNode.all_of(left.candidate.rule, right.candidate.rule),
                    RuleNode.any_of(left.candidate.rule, right.candidate.rule),
                )
            )
    variants.extend(initial[warmup:])
    for rule in semantic_dedupe(tuple(variants)):
        candidate_hash = RuleCandidate.from_rule(experiment, rule).candidate_hash
        if candidate_hash in seen:
            continue
        try:
            _validate_rule_domain(experiment, rule)
        except ValueError:
            continue
        return rule
    return None


def _validated_fold(
    experiment: LearningExperiment,
    frame: pd.DataFrame,
    fold: WalkForwardFold,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    train_indices = tuple(int(index) for index in fold.train_index)
    validation_indices = tuple(int(index) for index in fold.validation_index)
    if not train_indices or not validation_indices:
        raise ValueError("inner folds require non-empty training and validation blocks")
    if min((*train_indices, *validation_indices)) < 0 or max((*train_indices, *validation_indices)) >= len(frame):
        raise ValueError("inner fold indices are outside development evidence")
    if len(set(train_indices)) != len(train_indices) or len(set(validation_indices)) != len(validation_indices):
        raise ValueError("inner fold indices must be unique")
    train = frame.iloc[list(train_indices)].copy()
    validation = frame.iloc[list(validation_indices)].copy()
    validation_start = validation[experiment.timestamp_column].min()
    if train[experiment.timestamp_column].max() >= validation_start:
        raise ValueError("inner folds must be strictly chronological")
    if train[experiment.outcome_availability_column].max() > validation_start:
        raise ValueError("inner training outcomes are unavailable at validation start")
    return train, validation


def _default_evaluator(
    experiment: LearningExperiment,
    candidate: RuleCandidate,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    execution_frame: pd.DataFrame,
) -> FoldMetrics:
    causal_warmup = train.sort_values(experiment.timestamp_column, kind="stable").tail(
        experiment.maximum_lag + 1
    )
    combined = pd.concat([causal_warmup, validation], ignore_index=True).sort_values(
        experiment.timestamp_column, kind="stable"
    )
    active = candidate.rule.evaluate(combined).astype(bool)
    validation_mask = combined[experiment.timestamp_column].isin(validation[experiment.timestamp_column])
    decisions = combined.loc[validation_mask, [experiment.timestamp_column]].copy()
    decisions["strategy_id"] = candidate.strategy_id
    decisions["symbol"] = experiment.symbol
    decisions["decision_timestamp"] = decisions.pop(experiment.timestamp_column)
    decisions["data_through"] = decisions["decision_timestamp"]
    # A Boolean rule is a transparent directional classifier: true is long, false is short.
    decisions["signal"] = np.where(active.loc[validation_mask], 1, -1)
    decisions["strength"] = 1.0
    last_decision = pd.Timestamp(validation[experiment.timestamp_column].max())
    eligible_at = last_decision + experiment.execution_assumptions.latency
    future_execution = execution_frame.loc[
        (execution_frame["open_timestamp"] >= eligible_at)
        & (execution_frame["close_timestamp"] > last_decision)
    ].sort_values("open_timestamp", kind="stable")
    execution_end = (
        pd.Timestamp(future_execution.iloc[0]["close_timestamp"])
        if not future_execution.empty
        else pd.Timestamp(combined["close_timestamp"].max())
    )
    execution_start = pd.Timestamp(causal_warmup["open_timestamp"].min())
    execution_mask = (
        (execution_frame["open_timestamp"] >= execution_start)
        & (execution_frame["close_timestamp"] <= execution_end)
    )
    execution_bars = execution_frame.loc[
        execution_mask,
        [
            "symbol",
            "open_timestamp",
            "close_timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "finalized",
        ],
    ].copy()
    result = run_intraday_backtest(
        execution_bars,
        decisions,
        experiment.execution_assumptions,
        experiment.risk_limits,
        strategy_id=candidate.strategy_id,
        symbol=experiment.symbol,
    )
    validation_start = pd.Timestamp(validation[experiment.timestamp_column].min())
    validation_curve = result.equity_curve.loc[
        (pd.to_datetime(result.equity_curve["timestamp"], utc=True) > validation_start)
        & (pd.to_datetime(result.equity_curve["timestamp"], utc=True) <= execution_end)
    ].copy()
    validation_metrics = calculate_backtest_metrics(
        validation_curve,
        result.trade_ledger,
        periods_per_year=experiment.risk_limits.periods_per_year,
    )
    sharpe = float(validation_metrics.sharpe)
    drawdown = float(validation_metrics.maximum_drawdown)
    turnover = float(validation_metrics.turnover)
    return FoldMetrics(
        net_sharpe=sharpe if math.isfinite(sharpe) else 0.0,
        maximum_drawdown=drawdown if math.isfinite(drawdown) else 0.0,
        turnover=turnover if math.isfinite(turnover) else 0.0,
    )


def _execution_contract(experiment: LearningExperiment) -> dict[str, object]:
    execution = experiment.execution_assumptions
    costs = execution.costs
    risk = experiment.risk_limits
    return {
        "model_version": experiment.execution_model_version,
        "execution": {
            "costs": {
                "maker_fee_bps": costs.maker_fee_bps,
                "taker_fee_bps": costs.taker_fee_bps,
                "commission_per_unit": costs.commission_per_unit,
                "half_spread_bps": costs.half_spread_bps,
                "slippage_bps": costs.slippage_bps,
                "funding_bps_per_period": costs.funding_bps_per_period,
                "borrow_bps_per_period": costs.borrow_bps_per_period,
            },
            "latency_seconds": execution.latency.total_seconds(),
            "tick_size": execution.tick_size,
            "lot_size": execution.lot_size,
            "participation_rate": execution.participation_rate,
            "short_borrow_available": execution.short_borrow_available,
            "flatten_at_session_end": execution.flatten_at_session_end,
        },
        "risk": {
            "initial_cash": risk.initial_cash,
            "maximum_gross_exposure": risk.maximum_gross_exposure,
            "maximum_net_exposure": risk.maximum_net_exposure,
            "maximum_asset_exposure": risk.maximum_asset_exposure,
            "maximum_strategy_exposure": risk.maximum_strategy_exposure,
            "target_volatility": risk.target_volatility,
            "volatility_lookback": risk.volatility_lookback,
            "minimum_volatility_observations": risk.minimum_volatility_observations,
            "periods_per_year": risk.periods_per_year,
        },
    }


_TRIAL_SOURCE = "interpretable_learning"
_TRIAL_SOURCE_VERSION = "2"


def _timestamp_text(value: object) -> str:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError("persisted trial timestamps must be explicit UTC")
    return timestamp.tz_convert("UTC").isoformat()


def _development_digest(frame: pd.DataFrame) -> str:
    return canonical_hash(
        {
            "columns": tuple(str(column) for column in frame.columns),
            "dtypes": tuple(str(dtype) for dtype in frame.dtypes),
            "rows": frame.to_json(
                orient="split",
                date_format="iso",
                date_unit="ns",
                double_precision=15,
            ),
        }
    )


def _trial_payload(
    experiment: LearningExperiment,
    trial: LearningTrial,
    development_digest: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 2,
        "experiment_hash": _experiment_hash(experiment),
        "development_evidence_digest": development_digest,
        "ordinal": trial.ordinal,
        "trial_id": trial.trial_id,
        "candidate_hash": trial.candidate.candidate_hash,
        "strategy_id": trial.candidate.strategy_id,
        "version": trial.candidate.version,
        "state": trial.candidate.state,
        "rule": json.loads(trial.candidate.rule.canonical),
        "rule_text": trial.candidate.rule.render(),
        "fold_metrics": [
            {
                "net_sharpe": metrics.net_sharpe,
                "maximum_drawdown": metrics.maximum_drawdown,
                "turnover": metrics.turnover,
            }
            for metrics in trial.fold_metrics
        ],
        "fold_count": len(trial.fold_metrics),
        "fitness": trial.fitness,
        "status": trial.status,
        "error_summary": trial.error_summary,
        "evaluated_at": _timestamp_text(trial.evaluated_at),
        "learning_run_id": experiment.learning_run_id,
        "dataset_hash": experiment.dataset_hash,
        "symbol": experiment.symbol,
        "interval": experiment.interval.value,
        "mode": StrategyMode.WALK_FORWARD_LEARNING.value,
        "source": _TRIAL_SOURCE,
        "source_version": _TRIAL_SOURCE_VERSION,
        "created_at": _timestamp_text(trial.evaluated_at),
    }
    payload["receipt_hash"] = canonical_hash(payload)
    return payload


def _experiment_hash(experiment: LearningExperiment) -> str:
    evaluator = experiment.evaluator
    evaluator_identity = (
        "default_cost_aware_evaluator"
        if evaluator is None
        else f"{getattr(evaluator, '__module__', '')}.{getattr(evaluator, '__qualname__', type(evaluator).__name__)}"
    )
    return canonical_hash(
        {
            "schema_version": 1,
            "context": {
                "learning_run_id": experiment.learning_run_id,
                "dataset_hash": experiment.dataset_hash,
                "symbol": experiment.symbol,
                "interval": experiment.interval.value,
                "started_at": experiment.started_at,
                "as_of": experiment.as_of,
                "sealed_final_start": experiment.sealed_final_start,
            },
            "search": {
                "seed": experiment.seed,
                "evaluation_budget": experiment.evaluation_budget,
                "inner_folds": [
                    {"train_index": fold.train_index, "validation_index": fold.validation_index}
                    for fold in experiment.inner_folds
                ],
                "indicators": experiment.indicators,
                "thresholds": experiment.thresholds,
                "seed_rules": [rule.canonical for rule in experiment.seed_rules],
                "max_depth": experiment.max_depth,
                "max_nodes": experiment.max_nodes,
                "maximum_lag": experiment.maximum_lag,
                "execution_contract": _execution_contract(experiment),
                "return_column": experiment.return_column,
                "timestamp_column": experiment.timestamp_column,
                "availability_column": experiment.availability_column,
                "outcome_availability_column": experiment.outcome_availability_column,
                "penalties": {
                    "drawdown": experiment.penalties.drawdown,
                    "turnover": experiment.penalties.turnover,
                    "instability": experiment.penalties.instability,
                    "complexity": experiment.penalties.complexity,
                },
                "evaluator": evaluator_identity,
                "evaluator_version": experiment.evaluator_version,
                "evaluator_cost_contract": experiment.evaluator_cost_contract,
            },
        }
    )


def _persist_trial(
    experiment: LearningExperiment,
    trial: LearningTrial,
    development_digest: str,
) -> None:
    if experiment.database is None:
        return
    experiment.database.insert(
        "learning_trials",
        [
            {
                "trial_id": trial.trial_id,
                "learning_run_id": experiment.learning_run_id,
                "candidate_hash": trial.candidate.candidate_hash,
                "dataset_hash": experiment.dataset_hash,
                "symbol": experiment.symbol,
                "interval": experiment.interval.value,
                "mode": StrategyMode.WALK_FORWARD_LEARNING.value,
                "evaluated_at": trial.evaluated_at,
                "candidate": _trial_payload(experiment, trial, development_digest),
                "fitness": trial.fitness,
                "status": trial.status,
                "error_summary": trial.error_summary,
                "source": _TRIAL_SOURCE,
                "source_version": _TRIAL_SOURCE_VERSION,
                "created_at": trial.evaluated_at,
            }
        ],
    )


def _json_payload(value: object) -> dict[str, object]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("persisted candidate payload is malformed")
    return parsed


def _row_optional_text(value: object) -> str | None:
    return None if pd.isna(value) else str(value)


def _resume_trials(experiment: LearningExperiment, development_digest: str) -> list[LearningTrial]:
    if experiment.database is None:
        return []
    persisted = experiment.database.frame(
        "select * from learning_trials where learning_run_id = :run_id",
        {"run_id": experiment.learning_run_id},
    )
    if persisted.empty:
        return []
    rows: list[tuple[int, dict[str, object], dict[str, object]]] = []
    for row in persisted.to_dict(orient="records"):
        payload = _json_payload(row["candidate"])
        receipt = payload.pop("receipt_hash", None)
        if payload.get("schema_version") != 2 or receipt != canonical_hash(payload):
            raise ValueError("persisted trial immutable receipt is malformed")
        if payload.get("experiment_hash") != _experiment_hash(experiment):
            raise ValueError("persisted trial does not authenticate the complete search contract")
        if payload.get("development_evidence_digest") != development_digest:
            raise ValueError("persisted trial receipt does not authenticate current development evidence")
        ordinal = payload.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int):
            raise ValueError("persisted learning trial ordinal is malformed")
        row_fitness = None if pd.isna(row["fitness"]) else float(row["fitness"])
        payload_fitness = payload.get("fitness")
        fitness_matches = (
            row_fitness is None and payload_fitness is None
        ) or (
            row_fitness is not None
            and isinstance(payload_fitness, (float, int))
            and not isinstance(payload_fitness, bool)
            and math.isclose(row_fitness, float(payload_fitness), rel_tol=1e-6, abs_tol=1e-7)
        )
        mirrors = {
            "trial_id": str(row["trial_id"]),
            "learning_run_id": str(row["learning_run_id"]),
            "candidate_hash": str(row["candidate_hash"]),
            "dataset_hash": str(row["dataset_hash"]),
            "symbol": str(row["symbol"]),
            "interval": str(row["interval"]),
            "mode": str(row["mode"]),
            "evaluated_at": _timestamp_text(row["evaluated_at"]),
            "status": str(row["status"]),
            "error_summary": _row_optional_text(row["error_summary"]),
            "source": str(row["source"]),
            "source_version": str(row["source_version"]),
            "created_at": _timestamp_text(row["created_at"]),
        }
        if not fitness_matches or any(payload.get(name) != value for name, value in mirrors.items()):
            raise ValueError("persisted trial row conflicts with its immutable receipt")
        expected_context = {
            "learning_run_id": experiment.learning_run_id,
            "dataset_hash": experiment.dataset_hash,
            "symbol": experiment.symbol,
            "interval": experiment.interval.value,
            "mode": StrategyMode.WALK_FORWARD_LEARNING.value,
            "source": _TRIAL_SOURCE,
            "source_version": _TRIAL_SOURCE_VERSION,
        }
        if any(payload.get(name) != value for name, value in expected_context.items()):
            raise ValueError("persisted trial receipt context conflicts with the experiment")
        payload["receipt_hash"] = receipt
        rows.append((ordinal, payload, row))

    rows.sort(key=lambda item: item[0])
    if [ordinal for ordinal, _, _ in rows] != list(range(len(rows))):
        raise ValueError("persisted trial ledger must be a contiguous append-only prefix")
    if len(rows) > experiment.evaluation_budget:
        raise ValueError("persisted trial count exceeds the current evaluation budget")

    trials: list[LearningTrial] = []
    initial = _candidate_rules(experiment)
    for ordinal, payload, _row in rows:
        if not 0 <= ordinal < experiment.evaluation_budget:
            raise ValueError("persisted learning trial ordinal is malformed")
        expected_rule = _next_rule(experiment, trials)
        candidate = RuleCandidate.from_rule(experiment, expected_rule or initial[-1])
        if (
            payload.get("candidate_hash") != candidate.candidate_hash
            or payload.get("strategy_id") != candidate.strategy_id
            or payload.get("version") != candidate.version
            or payload.get("state") != candidate.state
            or payload.get("rule") != json.loads(candidate.rule.canonical)
            or payload.get("rule_text") != candidate.rule.render()
        ):
            raise ValueError("persisted trial does not match deterministic candidate generation")
        expected_trial_id = canonical_hash(
            {
                "candidate_hash": candidate.candidate_hash,
                "learning_run_id": experiment.learning_run_id,
                "ordinal": ordinal,
            }
        )
        if payload.get("trial_id") != expected_trial_id:
            raise ValueError("persisted trial identity is malformed")
        status = str(payload["status"])
        if status not in {"succeeded", "failed", "invalid", "budget_stop"}:
            raise ValueError("persisted trial status is malformed")
        metrics_payload = payload.get("fold_metrics", [])
        if not isinstance(metrics_payload, list):
            raise ValueError("persisted fold metrics are malformed")
        metrics = tuple(FoldMetrics(**item) for item in metrics_payload if isinstance(item, dict))
        if len(metrics) != len(metrics_payload):
            raise ValueError("persisted fold metrics are malformed")
        if payload.get("fold_count") != len(metrics):
            raise ValueError("persisted fold count is malformed")
        raw_fitness = payload.get("fitness")
        stored_fitness = None if raw_fitness is None else float(raw_fitness)
        error_summary = payload.get("error_summary")
        if status == "succeeded":
            if len(metrics) != len(experiment.inner_folds) or error_summary is not None:
                raise ValueError("successful persisted trial result semantics are malformed")
            fitness = calculate_fitness(candidate, metrics, experiment.penalties)
            if stored_fitness is None or not math.isclose(stored_fitness, fitness, rel_tol=1e-6, abs_tol=1e-7):
                raise ValueError("persisted trial fitness does not match its fold evidence")
        else:
            if metrics or stored_fitness is not None or not isinstance(error_summary, str) or not error_summary:
                raise ValueError("non-success persisted trial result semantics are malformed")
            fitness = None
        try:
            _validate_rule_domain(experiment, candidate.rule)
            expected_invalid = False
        except ValueError:
            expected_invalid = True
        if (status == "invalid") != expected_invalid:
            raise ValueError("persisted invalid status conflicts with deterministic domain validation")
        if (expected_rule is None) != (status == "budget_stop"):
            raise ValueError("persisted budget-stop status conflicts with deterministic generation")
        expected_evaluated_at = experiment.started_at + timedelta(microseconds=ordinal)
        if payload.get("evaluated_at") != _timestamp_text(expected_evaluated_at) or payload.get(
            "created_at"
        ) != _timestamp_text(expected_evaluated_at):
            raise ValueError("persisted trial evaluated_at is not deterministic")
        trials.append(
            LearningTrial(
                ordinal=ordinal,
                trial_id=expected_trial_id,
                candidate=candidate,
                evaluated_at=expected_evaluated_at,
                status=status,  # type: ignore[arg-type]
                fold_metrics=metrics,
                fitness=fitness,
                error_summary=error_summary if isinstance(error_summary, str) else None,
            )
        )
    return trials


def _persist_discovery(
    experiment: LearningExperiment,
    candidate: RuleCandidate,
    trials: Sequence[LearningTrial],
    development_digest: str,
) -> None:
    if experiment.database is None:
        return
    rule_id = canonical_hash(
        {
            "learning_run_id": experiment.learning_run_id,
            "rule_hash": candidate.candidate_hash,
            "rule_version": candidate.version,
        }
    )
    if experiment.database.scalar(
        "select count(*) from discovered_rules where rule_id = :rule_id", {"rule_id": rule_id}
    ):
        return
    best = next(trial for trial in trials if trial.candidate == candidate and trial.fitness is not None)
    experiment.database.insert(
        "discovered_rules",
        [
            {
                "rule_id": rule_id,
                "learning_run_id": experiment.learning_run_id,
                "rule_hash": candidate.candidate_hash,
                "rule_version": candidate.version,
                "dataset_hash": experiment.dataset_hash,
                "symbol": experiment.symbol,
                "interval": experiment.interval.value,
                "discovered_at": candidate.discovered_at,
                "state": "shadow",
                "rule": {
                    "schema_version": 1,
                    "strategy_id": candidate.strategy_id,
                    "canonical": json.loads(candidate.rule.canonical),
                    "plain_language": candidate.rule.render(),
                },
                "evidence": {
                    "development_evidence_through": experiment.as_of.isoformat(),
                    "development_evidence_digest": development_digest,
                    "experiment_hash": _experiment_hash(experiment),
                    "fitness": best.fitness,
                    "trial_count": len(trials),
                    "trial_ids": [trial.trial_id for trial in trials],
                },
                "source": _TRIAL_SOURCE,
                "source_version": _TRIAL_SOURCE_VERSION,
                "created_at": experiment.as_of,
            }
        ],
    )


def discover_rules(experiment: LearningExperiment, development_bars: pd.DataFrame) -> LearningResult:
    """Run deterministic bounded search without touching the sealed final block."""

    frame = _development_frame(experiment, development_bars)
    development_digest = _development_digest(frame)
    fold_frames = tuple(_validated_fold(experiment, frame, fold) for fold in experiment.inner_folds)
    rules = _candidate_rules(experiment)
    trials = _resume_trials(experiment, development_digest)
    successful = [trial.candidate for trial in trials if trial.status == "succeeded"]
    for ordinal in range(len(trials), experiment.evaluation_budget):
        rule = _next_rule(experiment, trials)
        evaluated_at = experiment.started_at + timedelta(microseconds=ordinal)
        candidate = RuleCandidate.from_rule(experiment, rule or rules[-1])
        trial_id = canonical_hash(
            {
                "candidate_hash": candidate.candidate_hash,
                "learning_run_id": experiment.learning_run_id,
                "ordinal": ordinal,
            }
        )
        if rule is None:
            trial = LearningTrial(
                ordinal,
                trial_id,
                candidate,
                evaluated_at,
                "budget_stop",
                error_summary="bounded semantic candidate space exhausted",
            )
            trials.append(trial)
            _persist_trial(experiment, trial, development_digest)
            continue
        try:
            _validate_rule_domain(experiment, candidate.rule)
        except ValueError as error:
            trial = LearningTrial(
                ordinal,
                trial_id,
                candidate,
                evaluated_at,
                "invalid",
                error_summary=f"{type(error).__name__}: {error}",
            )
            trials.append(trial)
            _persist_trial(experiment, trial, development_digest)
            continue
        try:
            fold_metrics: list[FoldMetrics] = []
            for train_source, validation_source in fold_frames:
                train = train_source.copy(deep=True)
                validation = validation_source.copy(deep=True)
                metrics = (
                    experiment.evaluator(candidate, train, validation)
                    if experiment.evaluator is not None
                    else _default_evaluator(experiment, candidate, train, validation, frame)
                )
                if not isinstance(metrics, FoldMetrics):
                    raise TypeError("candidate evaluator must return FoldMetrics")
                fold_metrics.append(metrics)
            fitness = calculate_fitness(candidate, fold_metrics, experiment.penalties)
        except Exception as error:  # each deterministic query must survive into the append-only ledger
            trial = LearningTrial(
                ordinal,
                trial_id,
                candidate,
                evaluated_at,
                "failed",
                error_summary=f"{type(error).__name__}: {error}",
            )
            trials.append(trial)
            _persist_trial(experiment, trial, development_digest)
            continue
        trial = LearningTrial(
            ordinal,
            trial_id,
            candidate,
            evaluated_at,
            "succeeded",
            tuple(fold_metrics),
            fitness,
        )
        trials.append(trial)
        _persist_trial(experiment, trial, development_digest)
        successful.append(candidate)
    ranked = sorted(
        (trial for trial in trials if trial.fitness is not None),
        key=lambda trial: (-float(trial.fitness), trial.candidate.candidate_hash),
    )
    result = LearningResult(
        learning_run_id=experiment.learning_run_id,
        trials=tuple(trials),
        candidates=tuple(successful),
        best_candidate=ranked[0].candidate if ranked else None,
        stopped_reason="evaluation_budget_exhausted",
    )
    if result.best_candidate is not None:
        _persist_discovery(experiment, result.best_candidate, result.trials, development_digest)
    return result


__all__ = [
    "FitnessPenalties",
    "FoldMetrics",
    "LearningExperiment",
    "LearningResult",
    "LearningTrial",
    "RuleCandidate",
    "calculate_fitness",
    "discover_rules",
]
