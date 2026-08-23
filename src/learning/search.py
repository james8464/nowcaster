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

from src.backtest.portfolio import maximum_drawdown
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
    penalties: FitnessPenalties = field(default_factory=FitnessPenalties)
    seed_rules: tuple[RuleNode, ...] = ()
    max_depth: int = 4
    max_nodes: int = 15
    maximum_lag: int = 2
    periods_per_year: int = 252
    transaction_cost_bps: float = 1.0
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
        if not evaluator_version:
            raise ValueError("evaluator_version must not be empty")
        object.__setattr__(self, "evaluator_version", evaluator_version)
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
        if self.periods_per_year <= 0 or self.transaction_cost_bps < 0:
            raise ValueError("evaluation periods and transaction costs are invalid")
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
    required = {
        experiment.timestamp_column,
        experiment.availability_column,
        experiment.outcome_availability_column,
        "finalized",
        *experiment.indicators,
    }
    if experiment.evaluator is None:
        required.add(experiment.return_column)
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"development evidence is missing columns: {sorted(missing)}")
    frame = bars.copy()
    for column in (
        experiment.timestamp_column,
        experiment.availability_column,
        experiment.outcome_availability_column,
    ):
        frame[column] = _utc_column(frame, column)
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
    if (frame[experiment.outcome_availability_column] < frame[experiment.timestamp_column]).any():
        raise ValueError("development outcomes cannot be available before their decisions")
    frame = frame.sort_values(experiment.timestamp_column, kind="stable").reset_index(drop=True)
    if frame[experiment.timestamp_column].duplicated().any():
        raise ValueError("development decision timestamps must be unique")
    for indicator in experiment.indicators:
        values = pd.to_numeric(frame[indicator], errors="coerce")
        if values.isna().any() or not np.isfinite(values).all():
            raise ValueError(f"indicator '{indicator}' must contain finite finalized values")
        frame[indicator] = values.astype(float)
    return frame


def _candidate_rules(experiment: LearningExperiment) -> tuple[RuleNode, ...]:
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
    ordered_atoms = sorted(semantic_dedupe(tuple(atoms)), key=lambda node: node.semantic_hash)
    structures = [
        constructor(left, right)
        for constructor in (RuleNode.all_of, RuleNode.any_of)
        for position, left in enumerate(ordered_atoms[:12])
        for right in ordered_atoms[position + 1 : 12]
    ]
    generated = list(semantic_dedupe(tuple(ordered_atoms + structures)))
    random.Random(experiment.seed).shuffle(generated)
    return semantic_dedupe(tuple(experiment.seed_rules) + tuple(generated))


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
) -> FoldMetrics:
    combined = pd.concat([train, validation], ignore_index=True).sort_values(
        experiment.timestamp_column, kind="stable"
    )
    active = candidate.rule.evaluate(combined).astype(float)
    validation_mask = combined[experiment.timestamp_column].isin(validation[experiment.timestamp_column])
    positions = active.loc[validation_mask]
    gross_returns = pd.to_numeric(combined.loc[validation_mask, experiment.return_column], errors="coerce")
    if gross_returns.isna().any() or not np.isfinite(gross_returns).all():
        raise ValueError("development forward returns must be finite")
    turnover = positions.diff().abs().fillna(positions.abs())
    net_returns = positions.to_numpy() * gross_returns.to_numpy() - turnover.to_numpy() * (
        experiment.transaction_cost_bps / 10_000
    )
    net = pd.Series(net_returns, dtype=float)
    volatility = float(net.std(ddof=1))
    sharpe = (
        float(net.mean() / volatility * math.sqrt(experiment.periods_per_year))
        if volatility > 0 and math.isfinite(volatility)
        else 0.0
    )
    return FoldMetrics(
        net_sharpe=sharpe,
        maximum_drawdown=float(maximum_drawdown(net)),
        turnover=float(turnover.sum()),
    )


def _trial_payload(experiment: LearningExperiment, trial: LearningTrial) -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_hash": _experiment_hash(experiment),
        "ordinal": trial.ordinal,
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
    }


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
                "periods_per_year": experiment.periods_per_year,
                "transaction_cost_bps": experiment.transaction_cost_bps,
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
            },
        }
    )


def _persist_trial(experiment: LearningExperiment, trial: LearningTrial) -> None:
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
                "candidate": _trial_payload(experiment, trial),
                "fitness": trial.fitness,
                "status": trial.status,
                "error_summary": trial.error_summary,
                "source": "interpretable_learning",
                "source_version": "1",
                "created_at": trial.evaluated_at,
            }
        ],
    )


def _json_payload(value: object) -> dict[str, object]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("persisted candidate payload is malformed")
    return parsed


def _resume_trials(experiment: LearningExperiment, rules: tuple[RuleNode, ...]) -> list[LearningTrial]:
    if experiment.database is None:
        return []
    persisted = experiment.database.frame(
        "select * from learning_trials where learning_run_id = :run_id order by evaluated_at, trial_id",
        {"run_id": experiment.learning_run_id},
    )
    if persisted.empty:
        return []
    expected_context = {
        "dataset_hash": experiment.dataset_hash,
        "symbol": experiment.symbol,
        "interval": experiment.interval.value,
        "mode": StrategyMode.WALK_FORWARD_LEARNING.value,
    }
    trials: list[LearningTrial] = []
    for row in persisted.to_dict(orient="records"):
        if any(str(row[name]) != value for name, value in expected_context.items()):
            raise ValueError("persisted learning ledger context conflicts with the experiment")
        payload = _json_payload(row["candidate"])
        if payload.get("schema_version") != 1:
            raise ValueError("persisted candidate payload version is unsupported")
        if payload.get("experiment_hash") != _experiment_hash(experiment):
            raise ValueError("persisted trial does not authenticate the complete search contract")
        ordinal = payload.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or not 0 <= ordinal < experiment.evaluation_budget:
            raise ValueError("persisted learning trial ordinal is malformed")
        candidate = RuleCandidate.from_rule(experiment, rules[min(ordinal, len(rules) - 1)])
        if payload.get("candidate_hash") != candidate.candidate_hash or payload.get("version") != candidate.version:
            raise ValueError("persisted trial does not match deterministic candidate generation")
        expected_trial_id = canonical_hash(
            {
                "candidate_hash": candidate.candidate_hash,
                "learning_run_id": experiment.learning_run_id,
                "ordinal": ordinal,
            }
        )
        if row["trial_id"] != expected_trial_id or row["candidate_hash"] != candidate.candidate_hash:
            raise ValueError("persisted trial identity is malformed")
        status = str(row["status"])
        if status not in {"succeeded", "failed", "invalid", "budget_stop"}:
            raise ValueError("persisted trial status is malformed")
        metrics_payload = payload.get("fold_metrics", [])
        if not isinstance(metrics_payload, list):
            raise ValueError("persisted fold metrics are malformed")
        metrics = tuple(FoldMetrics(**item) for item in metrics_payload if isinstance(item, dict))
        if len(metrics) != len(metrics_payload):
            raise ValueError("persisted fold metrics are malformed")
        raw_fitness = row["fitness"]
        stored_fitness = None if pd.isna(raw_fitness) else float(raw_fitness)
        if status == "succeeded":
            fitness = calculate_fitness(candidate, metrics, experiment.penalties)
            if stored_fitness is None or not math.isclose(stored_fitness, fitness, rel_tol=1e-6, abs_tol=1e-7):
                raise ValueError("persisted trial fitness does not match its fold evidence")
        else:
            if stored_fitness is not None:
                raise ValueError("failed or invalid persisted trials cannot contain fitness")
            fitness = None
        raw_error = row["error_summary"]
        error_summary = None if pd.isna(raw_error) else str(raw_error)
        trials.append(
            LearningTrial(
                ordinal=ordinal,
                trial_id=expected_trial_id,
                candidate=candidate,
                evaluated_at=experiment.started_at + timedelta(microseconds=ordinal),
                status=status,  # type: ignore[arg-type]
                fold_metrics=metrics,
                fitness=fitness,
                error_summary=error_summary,
            )
        )
    trials.sort(key=lambda trial: trial.ordinal)
    if [trial.ordinal for trial in trials] != list(range(len(trials))):
        raise ValueError("persisted trial ledger must be a contiguous append-only prefix")
    if len(trials) > experiment.evaluation_budget:
        raise ValueError("persisted trial count exceeds the current evaluation budget")
    return trials


def _persist_discovery(
    experiment: LearningExperiment,
    candidate: RuleCandidate,
    trials: Sequence[LearningTrial],
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
                    "experiment_hash": _experiment_hash(experiment),
                    "fitness": best.fitness,
                    "trial_count": len(trials),
                    "trial_ids": [trial.trial_id for trial in trials],
                },
                "source": "interpretable_learning",
                "source_version": "1",
                "created_at": experiment.as_of,
            }
        ],
    )


def discover_rules(experiment: LearningExperiment, development_bars: pd.DataFrame) -> LearningResult:
    """Run deterministic bounded search without touching the sealed final block."""

    frame = _development_frame(experiment, development_bars)
    fold_frames = tuple(_validated_fold(experiment, frame, fold) for fold in experiment.inner_folds)
    rules = _candidate_rules(experiment)
    trials = _resume_trials(experiment, rules)
    successful = [trial.candidate for trial in trials if trial.status == "succeeded"]
    for ordinal in range(len(trials), experiment.evaluation_budget):
        rule = rules[min(ordinal, len(rules) - 1)]
        evaluated_at = experiment.started_at + timedelta(microseconds=ordinal)
        candidate = RuleCandidate.from_rule(experiment, rule)
        trial_id = canonical_hash(
            {
                "candidate_hash": candidate.candidate_hash,
                "learning_run_id": experiment.learning_run_id,
                "ordinal": ordinal,
            }
        )
        if ordinal >= len(rules):
            trial = LearningTrial(
                ordinal,
                trial_id,
                candidate,
                evaluated_at,
                "budget_stop",
                error_summary="bounded semantic candidate space exhausted",
            )
            trials.append(trial)
            _persist_trial(experiment, trial)
            continue
        try:
            candidate.rule.validate_bounds(max_depth=experiment.max_depth, max_nodes=experiment.max_nodes)
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
            _persist_trial(experiment, trial)
            continue
        try:
            fold_metrics: list[FoldMetrics] = []
            for train_source, validation_source in fold_frames:
                train = train_source.copy(deep=True)
                validation = validation_source.copy(deep=True)
                metrics = (
                    experiment.evaluator(candidate, train, validation)
                    if experiment.evaluator is not None
                    else _default_evaluator(experiment, candidate, train, validation)
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
            _persist_trial(experiment, trial)
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
        _persist_trial(experiment, trial)
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
        _persist_discovery(experiment, result.best_candidate, result.trials)
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
