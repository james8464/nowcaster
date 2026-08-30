from __future__ import annotations

import json
import math
import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from itertools import product
from types import MappingProxyType
from typing import Literal

import numpy as np
import pandas as pd

from src.backtest.execution import ExecutionAssumptions
from src.backtest.intraday import RiskLimits, run_intraday_backtest
from src.backtest.metrics import calculate_backtest_metrics
from src.backtest.robustness import effective_sample_size
from src.database.engine import Database
from src.deep_research.contracts import contextual_trial_identity, global_trial_identity
from src.learning.grammar import RuleNode, semantic_dedupe
from src.strategies.types import BarInterval, StrategyMode, canonical_hash
from src.strategies.validation import WalkForwardFold

TrialStatus = Literal["succeeded", "failed", "invalid", "budget_stop"]
RuleState = Literal["shadow", "paper", "active", "retired"]

_CONTEXTUAL_REGIME_COLUMNS = (
    "regime_trend_normal",
    "regime_trend_elevated_volatility",
    "regime_range_liquid",
    "regime_stressed_or_illiquid",
)
_CONTEXTUAL_CANDIDATE_FIELDS = (
    "profile_threshold_multiplier",
    "long_holding_horizon_bars",
    "short_holding_horizon_bars",
    "regime_uncertainty_penalty",
    "global_prior_strength",
    "asset_class_prior_strength",
    "profile_prior_strength",
    "asset_prior_strength",
    "asset_regime_prior_strength",
    "risk_penalty",
    "turnover_penalty",
    "prior_penalty",
    "minimum_lower_edge",
    "maximum_correlation",
    "kelly_fraction",
    "minimum_liquidity_quality",
)


@dataclass(frozen=True, slots=True)
class ContextualCandidate:
    """Closed, code-free policy searched by contextual learning."""

    profile_threshold_multiplier: float = 1.0
    long_holding_horizon_bars: int = 1
    short_holding_horizon_bars: int = 1
    regime_uncertainty_penalty: float = 1.0
    global_prior_strength: float = 1_000.0
    asset_class_prior_strength: float = 500.0
    profile_prior_strength: float = 250.0
    asset_prior_strength: float = 100.0
    asset_regime_prior_strength: float = 50.0
    risk_penalty: float = 4.0
    turnover_penalty: float = 0.5
    prior_penalty: float = 0.5
    minimum_lower_edge: float = 0.0
    maximum_correlation: float = 0.75
    kelly_fraction: float = 0.10
    minimum_liquidity_quality: float = 0.80

    def __post_init__(self) -> None:
        integer_values = (self.long_holding_horizon_bars, self.short_holding_horizon_bars)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_values):
            raise ValueError("contextual holding horizons must be integers")
        numeric = tuple(float(getattr(self, name)) for name in _CONTEXTUAL_CANDIDATE_FIELDS)
        if any(not math.isfinite(value) for value in numeric):
            raise ValueError("contextual candidate values must be finite")
        bounds = {
            "profile_threshold_multiplier": (0.75, 1.50),
            "long_holding_horizon_bars": (1, 24),
            "short_holding_horizon_bars": (1, 24),
            "regime_uncertainty_penalty": (0.0, 4.0),
            "global_prior_strength": (100.0, 5_000.0),
            "asset_class_prior_strength": (50.0, 2_500.0),
            "profile_prior_strength": (25.0, 1_250.0),
            "asset_prior_strength": (10.0, 500.0),
            "asset_regime_prior_strength": (5.0, 250.0),
            "risk_penalty": (0.25, 25.0),
            "turnover_penalty": (0.0, 10.0),
            "prior_penalty": (0.0, 10.0),
            "minimum_lower_edge": (0.0, 0.02),
            "maximum_correlation": (0.25, 0.95),
            "kelly_fraction": (0.01, 0.25),
            "minimum_liquidity_quality": (0.50, 1.0),
        }
        for name, (lower, upper) in bounds.items():
            value = float(getattr(self, name))
            if not lower <= value <= upper:
                raise ValueError(f"contextual candidate {name} must be in [{lower}, {upper}]")
        priors = (
            self.global_prior_strength,
            self.asset_class_prior_strength,
            self.profile_prior_strength,
            self.asset_prior_strength,
            self.asset_regime_prior_strength,
        )
        if any(left < right for left, right in zip(priors[:-1], priors[1:], strict=True)):
            raise ValueError("contextual prior strengths must decrease toward specific cells")

    @classmethod
    def defaults(cls) -> ContextualCandidate:
        return cls()

    @property
    def definition(self) -> dict[str, float | int]:
        return {name: getattr(self, name) for name in _CONTEXTUAL_CANDIDATE_FIELDS}

    @property
    def candidate_hash(self) -> str:
        return canonical_hash({"contextual_candidate_version": 1, **self.definition})

    def global_trial_id(self, dataset_hash: str, protocol_hash: str, attempt_ordinal: int = 0) -> str:
        return contextual_trial_identity(
            dataset_hash=dataset_hash,
            protocol_hash=protocol_hash,
            candidate_hash=self.candidate_hash,
            attempt_ordinal=attempt_ordinal,
        )


@dataclass(frozen=True, slots=True)
class ContextualSearchSpace:
    baseline: ContextualCandidate
    profile_threshold_multipliers: tuple[float, ...]
    long_holding_horizons: tuple[int, ...]
    short_holding_horizons: tuple[int, ...]
    regime_uncertainty_penalties: tuple[float, ...]
    global_prior_strengths: tuple[float, ...]
    asset_class_prior_strengths: tuple[float, ...]
    profile_prior_strengths: tuple[float, ...]
    asset_prior_strengths: tuple[float, ...]
    asset_regime_prior_strengths: tuple[float, ...]
    risk_penalties: tuple[float, ...]
    turnover_penalties: tuple[float, ...]
    prior_penalties: tuple[float, ...]
    minimum_lower_edges: tuple[float, ...]
    maximum_correlations: tuple[float, ...]
    kelly_fractions: tuple[float, ...]
    minimum_liquidity_qualities: tuple[float, ...]

    def __post_init__(self) -> None:
        grids = self.grids
        if any(not values for values in grids.values()):
            raise ValueError("contextual search grids cannot be empty")
        for field_name, values in grids.items():
            if len(values) != len(set(values)):
                raise ValueError(f"contextual search grid {field_name} must be unique")
            for value in values:
                try:
                    replace(self.baseline, **{field_name: value})
                except ValueError as error:
                    raise ValueError(f"invalid contextual search value for {field_name}") from error

    @property
    def grids(self) -> MappingProxyType[str, tuple[float | int, ...]]:
        return MappingProxyType(
            {
                "profile_threshold_multiplier": self.profile_threshold_multipliers,
                "long_holding_horizon_bars": self.long_holding_horizons,
                "short_holding_horizon_bars": self.short_holding_horizons,
                "regime_uncertainty_penalty": self.regime_uncertainty_penalties,
                "global_prior_strength": self.global_prior_strengths,
                "asset_class_prior_strength": self.asset_class_prior_strengths,
                "profile_prior_strength": self.profile_prior_strengths,
                "asset_prior_strength": self.asset_prior_strengths,
                "asset_regime_prior_strength": self.asset_regime_prior_strengths,
                "risk_penalty": self.risk_penalties,
                "turnover_penalty": self.turnover_penalties,
                "prior_penalty": self.prior_penalties,
                "minimum_lower_edge": self.minimum_lower_edges,
                "maximum_correlation": self.maximum_correlations,
                "kelly_fraction": self.kelly_fractions,
                "minimum_liquidity_quality": self.minimum_liquidity_qualities,
            }
        )

    @classmethod
    def conservative(
        cls,
        baseline: ContextualCandidate | None = None,
        *,
        long_horizons: tuple[int, ...] = (1,),
        short_horizons: tuple[int, ...] = (1,),
    ) -> ContextualSearchSpace:
        base = baseline or ContextualCandidate.defaults()

        def values(*items: float | int) -> tuple:
            return tuple(dict.fromkeys(items))

        return cls(
            baseline=base,
            profile_threshold_multipliers=values(base.profile_threshold_multiplier, 0.90, 1.10),
            long_holding_horizons=values(*long_horizons),
            short_holding_horizons=values(*short_horizons),
            regime_uncertainty_penalties=values(base.regime_uncertainty_penalty, 0.50, 1.50, 2.0),
            global_prior_strengths=values(base.global_prior_strength, 500.0, 1_500.0),
            asset_class_prior_strengths=values(base.asset_class_prior_strength, 250.0, 750.0),
            profile_prior_strengths=values(base.profile_prior_strength, 125.0, 375.0),
            asset_prior_strengths=values(base.asset_prior_strength, 50.0, 150.0),
            asset_regime_prior_strengths=values(base.asset_regime_prior_strength, 25.0, 75.0),
            risk_penalties=values(base.risk_penalty, 2.0, 8.0),
            turnover_penalties=values(base.turnover_penalty, 0.25, 1.0),
            prior_penalties=values(base.prior_penalty, 0.25, 1.0),
            minimum_lower_edges=values(base.minimum_lower_edge, 0.00025, 0.0005),
            maximum_correlations=values(base.maximum_correlation, 0.60, 0.85),
            kelly_fractions=values(base.kelly_fraction, 0.05, 0.15),
            minimum_liquidity_qualities=values(base.minimum_liquidity_quality, 0.75, 0.90),
        )


@dataclass(frozen=True, slots=True)
class ContextualLearningExperiment:
    dataset_hash: str
    protocol_hash: str
    as_of: datetime
    sealed_final_start: datetime
    outer_validation_blocks: int = 3
    minimum_train_timestamps: int = 40
    minimum_validation_timestamps: int = 10
    drawdown_penalty: float = 0.50
    turnover_fitness_penalty: float = 0.05
    instability_penalty: float = 0.25
    fragmentation_penalty: float = 0.05
    complexity_penalty: float = 0.005
    concentration_penalty: float = 0.10
    rebalance_cost_rate: float = 0.0017

    def __post_init__(self) -> None:
        for name in ("dataset_hash", "protocol_hash"):
            value = str(getattr(self, name)).strip().lower()
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"contextual experiment {name} must be a lowercase SHA-256 digest")
            object.__setattr__(self, name, value)
        _explicit_utc(self.as_of, "contextual experiment as_of")
        _explicit_utc(self.sealed_final_start, "contextual experiment sealed_final_start")
        if self.as_of >= self.sealed_final_start:
            raise ValueError("contextual development boundary must precede the sealed final start")
        if not 1 <= self.outer_validation_blocks <= 10:
            raise ValueError("contextual outer validation block count must be in [1, 10]")
        if self.minimum_train_timestamps < 20 or self.minimum_validation_timestamps < 5:
            raise ValueError("contextual chronological folds are too small")
        penalties = (
            self.drawdown_penalty,
            self.turnover_fitness_penalty,
            self.instability_penalty,
            self.fragmentation_penalty,
            self.complexity_penalty,
            self.concentration_penalty,
            self.rebalance_cost_rate,
        )
        if any(not math.isfinite(value) or value < 0 for value in penalties):
            raise ValueError("contextual fitness penalties must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class ContextualFoldMetrics:
    net_sharpe: float
    maximum_drawdown: float
    turnover: float
    concentration: float
    fragmentation: float
    observations: int


@dataclass(frozen=True, slots=True)
class ContextualCandidateEvaluation:
    candidate_hash: str
    status: Literal["succeeded"]
    state: Literal["shadow"]
    fold_metrics: tuple[ContextualFoldMetrics, ...]
    fitness: float
    observations: int
    evidence_hash: str


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

    @classmethod
    def from_rule(
        cls,
        experiment: LearningExperiment,
        rule: RuleNode,
        *,
        discovered_at: datetime | None = None,
    ) -> RuleCandidate:
        identity = canonical_hash({"grammar_version": 1, "rule": rule.canonical})
        run_version = canonical_hash(
            {"learning_run_id": experiment.learning_run_id, "started_at": experiment.started_at}
        )[:8]
        return cls(
            rule=rule,
            version=f"1.0.0+{identity[:8]}.{run_version}",
            discovered_at=discovered_at or experiment.event_time(),
            evidence_through=experiment.development_data_through,
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
    development_data_through: datetime | None = None
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
    search_family: str = "interpretable_rule_grammar"
    database: Database | None = field(default=None, compare=False, repr=False)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC), compare=False, repr=False)

    def __post_init__(self) -> None:
        for name in ("learning_run_id", "dataset_hash", "symbol"):
            normalized = str(getattr(self, name)).strip()
            if not normalized:
                raise ValueError("learning experiment identifiers must not be empty")
            object.__setattr__(self, name, normalized.upper() if name == "symbol" else normalized)
        object.__setattr__(self, "interval", BarInterval(self.interval))
        for name in ("started_at", "as_of", "sealed_final_start"):
            _explicit_utc(getattr(self, name), f"experiment {name}")
        development_data_through = self.development_data_through or self.as_of
        _explicit_utc(development_data_through, "experiment development_data_through")
        if development_data_through != self.as_of:
            raise ValueError("as_of must equal the authenticated development data boundary")
        if development_data_through >= self.sealed_final_start:
            raise ValueError("development data must precede the sealed final boundary")
        object.__setattr__(self, "development_data_through", development_data_through)
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("learning seed must be an integer")
        if self.evaluation_budget <= 0:
            raise ValueError("evaluation budget must be positive")
        evaluator_version = self.evaluator_version.strip()
        evaluator_cost_contract = self.evaluator_cost_contract.strip()
        search_family = self.search_family.strip().lower()
        if not evaluator_version or not evaluator_cost_contract or not search_family:
            raise ValueError("evaluator version, cost contract, and search family must not be empty")
        object.__setattr__(self, "evaluator_version", evaluator_version)
        object.__setattr__(self, "evaluator_cost_contract", evaluator_cost_contract)
        object.__setattr__(self, "search_family", search_family)
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
        ordered_seed_rules = tuple(sorted(self.seed_rules, key=lambda rule: (rule.semantic_hash, rule.render())))
        object.__setattr__(
            self,
            "seed_rules",
            semantic_dedupe(ordered_seed_rules),
        )

    def event_time(self) -> datetime:
        return _explicit_utc(self.clock(), "learning event clock")


@dataclass(frozen=True, slots=True)
class LearningTrial:
    ordinal: int
    trial_id: str
    candidate: RuleCandidate
    evaluated_at: datetime
    received_at: datetime
    status: TrialStatus
    fold_metrics: tuple[FoldMetrics, ...] = ()
    fitness: float | None = None
    error_summary: str | None = None
    global_trial_id: str = ""


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


def generate_contextual_candidates(
    search_space: ContextualSearchSpace,
    *,
    seed: int,
    budget: int,
) -> tuple[ContextualCandidate, ...]:
    """Generate baseline, one-at-a-time neighbours, then seeded combinations."""

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("contextual search seed must be an integer")
    if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 100_000:
        raise ValueError("contextual search budget must be in [1, 100000]")
    candidates: list[ContextualCandidate] = []
    seen: set[str] = set()

    def add(candidate: ContextualCandidate) -> None:
        if candidate.candidate_hash not in seen and len(candidates) < budget:
            seen.add(candidate.candidate_hash)
            candidates.append(candidate)

    add(search_space.baseline)
    for field_name, values in search_space.grids.items():
        for value in values:
            try:
                add(replace(search_space.baseline, **{field_name: value}))
            except ValueError:
                continue
            if len(candidates) == budget:
                return tuple(candidates)

    generator = random.Random(seed)
    fields = tuple(search_space.grids)
    maximum_random_attempts = max(2_000, budget * 100)
    for _ in range(maximum_random_attempts):
        values = {name: generator.choice(search_space.grids[name]) for name in fields}
        try:
            add(replace(search_space.baseline, **values))
        except ValueError:
            continue
        if len(candidates) == budget:
            return tuple(candidates)

    grids = tuple(search_space.grids[name] for name in fields)
    for combination in product(*grids):
        try:
            add(replace(search_space.baseline, **dict(zip(fields, combination, strict=True))))
        except ValueError:
            continue
        if len(candidates) == budget:
            break
    return tuple(candidates)


def _contextual_learning_frame(
    outcomes: pd.DataFrame,
    experiment: ContextualLearningExperiment,
) -> pd.DataFrame:
    forbidden = sorted(
        str(column)
        for column in outcomes.columns
        if str(column).lower().startswith("final_") or "sealed" in str(column).lower()
    )
    if forbidden:
        raise ValueError(f"sealed or final evidence is forbidden during contextual search: {forbidden}")
    required = {
        "outcome_id",
        "strategy_id",
        "symbol",
        "asset_class",
        "profile",
        "direction",
        "decision_timestamp",
        "outcome_available_at",
        "net_return",
        "eligibility_quality",
        *_CONTEXTUAL_REGIME_COLUMNS,
    }
    missing = sorted(required - set(outcomes.columns))
    if missing:
        raise ValueError(f"contextual learning evidence is missing columns: {missing}")
    if outcomes.empty:
        raise ValueError("contextual learning requires resolved outcomes")
    frame = outcomes.loc[:, sorted(required)].copy()
    frame["holding_horizon_bars"] = outcomes.get("holding_horizon_bars", 1)
    horizons = pd.to_numeric(frame["holding_horizon_bars"], errors="coerce")
    if not horizons.between(1, 24).all() or not horizons.eq(horizons.round()).all():
        raise ValueError("contextual holding horizon must be an observed integer in [1, 24]")
    frame["holding_horizon_bars"] = horizons.astype(int)
    if frame["outcome_id"].astype(str).duplicated().any():
        raise ValueError("contextual learning outcome identities must be unique")
    for column in ("decision_timestamp", "outcome_available_at"):
        timestamps = []
        for value in frame[column]:
            timestamp = pd.Timestamp(value)
            if pd.isna(timestamp) or timestamp.tzinfo is None or str(timestamp.tz) != "UTC":
                raise ValueError(f"contextual {column} must contain explicit UTC timestamps")
            timestamps.append(timestamp.tz_convert("UTC"))
        frame[column] = pd.Series(timestamps, index=frame.index, dtype="datetime64[ns, UTC]")
    if (frame["decision_timestamp"] >= pd.Timestamp(experiment.sealed_final_start)).any():
        raise ValueError("sealed contextual rows are forbidden during search")
    if (frame["decision_timestamp"] > pd.Timestamp(experiment.as_of)).any():
        raise ValueError("contextual decisions are not available at the learning boundary")
    if (frame["outcome_available_at"] > pd.Timestamp(experiment.as_of)).any():
        raise ValueError("contextual outcomes are not available at the learning boundary")
    if (frame["outcome_available_at"] <= frame["decision_timestamp"]).any():
        raise ValueError("contextual outcomes must resolve strictly after their decisions")
    text_columns = ("outcome_id", "strategy_id", "symbol", "asset_class", "profile", "direction")
    if any(frame[column].astype(str).str.strip().eq("").any() for column in text_columns):
        raise ValueError("contextual learning identity fields cannot be blank")
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    if not frame["direction"].astype(str).isin({"long", "short"}).all():
        raise ValueError("contextual learning direction must be long or short")
    if frame.duplicated(["decision_timestamp", "symbol", "direction", "strategy_id", "holding_horizon_bars"]).any():
        raise ValueError("contextual execution outcomes must be unique for each decision and horizon")
    numeric_columns = ("net_return", "eligibility_quality", *_CONTEXTUAL_REGIME_COLUMNS)
    numeric = frame.loc[:, numeric_columns].apply(pd.to_numeric, errors="coerce").astype(float)
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("contextual learning values must be finite")
    frame.loc[:, numeric_columns] = numeric
    if (frame["net_return"] <= -1).any():
        raise ValueError("contextual execution outcomes must preserve positive unlevered equity")
    if not frame["eligibility_quality"].between(0, 1).all():
        raise ValueError("contextual eligibility quality must be in [0, 1]")
    probabilities = frame.loc[:, _CONTEXTUAL_REGIME_COLUMNS].to_numpy(dtype=float)
    if (
        (probabilities < 0).any()
        or (probabilities > 1).any()
        or not np.allclose(probabilities.sum(axis=1), 1.0, rtol=0, atol=1e-8)
    ):
        raise ValueError("contextual regime probabilities must be normalized")
    return frame.sort_values(
        ["decision_timestamp", "symbol", "direction", "strategy_id", "outcome_id"],
        kind="stable",
    ).reset_index(drop=True)


def _contextual_outer_folds(
    frame: pd.DataFrame,
    experiment: ContextualLearningExperiment,
) -> tuple[tuple[pd.DataFrame, pd.DataFrame], ...]:
    timestamps = tuple(sorted(frame["decision_timestamp"].unique()))
    needed = experiment.minimum_train_timestamps + (
        experiment.outer_validation_blocks * experiment.minimum_validation_timestamps
    )
    if len(timestamps) < needed:
        raise ValueError("contextual learning has insufficient timestamps for chronological outer folds")
    remaining = len(timestamps) - experiment.minimum_train_timestamps
    block_size = remaining // experiment.outer_validation_blocks
    folds = []
    for fold_index in range(experiment.outer_validation_blocks):
        start = experiment.minimum_train_timestamps + fold_index * block_size
        end = len(timestamps) if fold_index == experiment.outer_validation_blocks - 1 else start + block_size
        validation_times = timestamps[start:end]
        if len(validation_times) < experiment.minimum_validation_timestamps:
            raise ValueError("contextual validation block is below its minimum timestamp count")
        validation_start = pd.Timestamp(validation_times[0])
        train = frame.loc[
            (frame["decision_timestamp"] < validation_start) & (frame["outcome_available_at"] <= validation_start)
        ].copy()
        validation = frame.loc[frame["decision_timestamp"].isin(validation_times)].copy()
        if train["decision_timestamp"].nunique() < experiment.minimum_train_timestamps:
            raise ValueError("contextual training outcomes are unavailable before validation")
        if train["outcome_available_at"].max() > validation["decision_timestamp"].min():
            raise ValueError("contextual outer fold violates outcome availability chronology")
        folds.append((train, validation))
    return tuple(folds)


def _soft_stats(values: np.ndarray, weights: np.ndarray) -> tuple[float, float, float]:
    mass = float(weights.sum())
    if mass <= 0:
        return 0.0, 0.0, 0.0
    mean = float(np.dot(values, weights) / mass)
    variance = float(np.dot(np.square(values - mean), weights) / mass)
    squared_mass = float(np.square(weights).sum())
    effective = mass * mass / squared_mass if squared_mass > 0 else 0.0
    if len(values):
        effective = min(effective, effective_sample_size(values))
    return mean, max(variance, 0.0), effective


def _contextual_leaf_estimate(
    train: pd.DataFrame,
    row: pd.Series,
    regime_column: str,
    candidate: ContextualCandidate,
) -> tuple[float, float, float]:
    strategy = str(row["strategy_id"])
    direction = str(row["direction"])
    values = train["net_return"].to_numpy(dtype=float)

    def shrink(
        mask: pd.Series,
        weights: np.ndarray,
        parent_mean: float,
        parent_uncertainty: float,
        strength: float,
    ) -> tuple[float, float, float]:
        selected = mask.to_numpy(dtype=bool)
        # Concurrent assets do not constitute independent time observations.
        temporal = pd.DataFrame(
            {
                "time": train.loc[mask, "decision_timestamp"],
                "value": values[selected],
                "weight": weights[selected],
            }
        )
        temporal["weighted_value"] = temporal["value"] * temporal["weight"]
        grouped = temporal.groupby("time").agg(
            value=("weighted_value", "sum"),
            mass=("weight", "sum"),
            weight=("weight", "mean"),
        )
        grouped = grouped.loc[grouped["mass"] > 0]
        local_mean, variance, effective = _soft_stats(
            (grouped["value"] / grouped["mass"]).to_numpy(),
            grouped["weight"].to_numpy(),
        )
        if effective <= 0:
            return parent_mean, max(parent_uncertainty, 0.0), variance
        alpha = effective / (effective + strength)
        mean = alpha * local_mean + (1.0 - alpha) * parent_mean
        sampling = math.sqrt(variance / max(effective, 1.0))
        uncertainty = alpha * sampling + (1.0 - alpha) * parent_uncertainty
        return mean, max(uncertainty, 0.0), variance

    unit = np.ones(len(train), dtype=float)
    strategy_mask = (train["strategy_id"].astype(str) == strategy) & (train["direction"].astype(str) == direction)
    mean, uncertainty, variance = shrink(
        strategy_mask,
        unit,
        0.0,
        0.0,
        candidate.global_prior_strength,
    )
    levels = (
        ("asset_class", candidate.asset_class_prior_strength),
        ("profile", candidate.profile_prior_strength),
        ("symbol", candidate.asset_prior_strength),
    )
    mask = strategy_mask.copy()
    for column, strength in levels:
        mask &= train[column].astype(str) == str(row[column])
        mean, uncertainty, variance = shrink(mask, unit, mean, uncertainty, strength)
    regime_weights = train[regime_column].to_numpy(dtype=float)
    mean, uncertainty, variance = shrink(
        mask,
        regime_weights,
        mean,
        uncertainty,
        candidate.asset_regime_prior_strength,
    )
    return mean - 1.6448536269514722 * uncertainty, uncertainty, max(variance, 1e-12)


def _maximum_drawdown(returns: Sequence[float]) -> float:
    if not returns:
        return 0.0
    equity = np.cumprod(1.0 + np.asarray(returns, dtype=float))
    peaks = np.maximum.accumulate(np.concatenate(([1.0], equity)))[1:]
    return float(abs(np.min(equity / peaks - 1.0)))


@dataclass(frozen=True, slots=True)
class _ContextualReplayTrade:
    decision: pd.Timestamp
    resolved: pd.Timestamp
    symbol: str
    direction: str
    exposure: float
    net_return: float
    concentration: float
    expected_edge: float


def _replay_contextual_account(trades, decision_clock, rebalance_cost_rate):
    """Reserve shared cash until each real execution resolves; never concatenate assets.

    Only closing valuations are available here. Intratrade drawdown still requires a
    price-path replay and this research score is never a live-readiness receipt.
    """
    clock = [pd.Timestamp(value) for value in decision_clock]
    if not clock:
        return [], 0.0, 0.0
    by_time = {}
    for trade in trades:
        by_time.setdefault(trade.decision, []).append(trade)
    pending = []
    cash = 1.0
    equities = []
    turnovers = []
    concentrations = []
    end = max((trade.resolved for trade in trades), default=clock[-1])
    clock.append(max(end, clock[-1] + pd.Timedelta(microseconds=1)))
    for moment in clock:
        remaining = []
        turnover = 0.0
        prior_equity = cash + sum(stake for _, stake in pending)
        for trade, stake in pending:
            if trade.resolved <= moment:
                cash += stake * (1 + trade.net_return - rebalance_cost_rate)
                turnover += stake / max(prior_equity, 1e-12)
            else:
                remaining.append((trade, stake))
        pending = remaining
        equity = cash + sum(stake for _, stake in pending)
        if equity <= 0:
            raise ValueError("contextual cost-adjusted replay exhausted account equity")
        # Decisions are based on the known training edge, never the realized profit.
        for trade in sorted(
            by_time.get(moment, ()), key=lambda item: (-item.expected_edge, item.symbol, item.direction)
        ):
            if any(open_trade.symbol == trade.symbol for open_trade, _ in pending):
                continue  # One position per asset, with no overlapping capital reuse.
            stake = min(equity * trade.exposure, cash / (1 + rebalance_cost_rate))
            if stake <= 0:
                continue
            cash -= stake * (1 + rebalance_cost_rate)
            turnover += stake / equity
            pending.append((trade, stake))
        equities.append(cash + sum(stake for _, stake in pending))
        turnovers.append(turnover)
        concentrations.append(sum(stake * trade.concentration for trade, stake in pending) / equity)
    # The first allocation cost belongs to the first interval, not a hidden warm-up.
    equities[0] = 1.0
    returns = [equities[index] / equities[index - 1] - 1 for index in range(1, len(equities))]
    return returns, sum(turnovers) / len(returns), float(np.mean(concentrations))


def _evaluate_contextual_fold(
    candidate: ContextualCandidate,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    rebalance_cost_rate: float = 0.0017,
) -> ContextualFoldMetrics:
    strategy_returns = train.pivot_table(
        index="decision_timestamp",
        columns="strategy_id",
        values="net_return",
        aggfunc="mean",
    )
    correlations = strategy_returns.corr(min_periods=5)
    previous: dict[tuple[str, str], dict[str, float]] = {}
    trades = []
    capital_slots = max(len(train[["symbol", "direction"]].drop_duplicates()), 1)
    contexts: set[tuple[str, str, str]] = set()
    leaf_cache: dict[tuple[str, ...], tuple[float, float, float]] = {}
    quality_threshold = min(
        candidate.minimum_liquidity_quality * candidate.profile_threshold_multiplier,
        1.0,
    )
    grouped = validation.groupby(["decision_timestamp", "symbol", "direction"], sort=True)
    for (decision, symbol, direction), group in grouped:
        direction = str(direction)
        contexts.add((str(group.iloc[0]["profile"]), str(symbol), direction))
        if float(group["eligibility_quality"].min()) < quality_threshold:
            continue
        posterior = group.iloc[0].loc[list(_CONTEXTUAL_REGIME_COLUMNS)].to_numpy(dtype=float)
        positive_mass = posterior[posterior > 0]
        entropy = float(-np.sum(positive_mass * np.log(positive_mass)) / math.log(4.0))
        estimates: dict[str, tuple[float, float]] = {}
        for _, row in group.iterrows():
            lower = 0.0
            uncertainty = 0.0
            variance = 0.0
            for probability, regime_column in zip(posterior, _CONTEXTUAL_REGIME_COLUMNS, strict=True):
                leaf_key = (
                    str(row["strategy_id"]),
                    str(row["direction"]),
                    str(row["asset_class"]),
                    str(row["profile"]),
                    str(row["symbol"]),
                    regime_column,
                )
                if leaf_key not in leaf_cache:
                    leaf_cache[leaf_key] = _contextual_leaf_estimate(train, row, regime_column, candidate)
                regime_lower, regime_uncertainty, regime_variance = leaf_cache[leaf_key]
                lower += float(probability) * regime_lower
                uncertainty += float(probability) * regime_uncertainty
                variance += float(probability) * regime_variance
            adjusted = lower - candidate.regime_uncertainty_penalty * entropy * uncertainty
            if adjusted >= candidate.minimum_lower_edge:
                estimates[str(row["strategy_id"])] = (adjusted, max(variance, 1e-12))
        ranked = sorted(estimates, key=lambda key: (-estimates[key][0], key))
        selected: list[str] = []
        for strategy_id in ranked:
            if any(
                other in correlations.index
                and strategy_id in correlations.columns
                and pd.notna(correlations.loc[other, strategy_id])
                and abs(float(correlations.loc[other, strategy_id])) > candidate.maximum_correlation
                for other in selected
            ):
                continue
            selected.append(strategy_id)
        if not selected:
            continue
        raw = {
            strategy_id: estimates[strategy_id][0] / (candidate.risk_penalty * estimates[strategy_id][1] + 1e-12)
            for strategy_id in selected
        }
        raw_total = sum(raw.values())
        if raw_total <= 0:
            continue
        target = {key: value / raw_total for key, value in raw.items()}
        equal = 1.0 / len(target)
        target = {
            key: (value + candidate.prior_penalty * equal) / (1.0 + candidate.prior_penalty)
            for key, value in target.items()
        }
        context_key = (str(symbol), direction)
        prior_weights = previous.get(context_key, {})
        smooth = 1.0 / (1.0 + candidate.turnover_penalty)
        universe = set(target) | set(prior_weights)
        weights = {
            key: smooth * target.get(key, 0.0) + (1.0 - smooth) * prior_weights.get(key, 0.0) for key in universe
        }
        weight_total = sum(weights.values())
        weights = {key: value / weight_total for key, value in weights.items()} if weight_total > 0 else {}
        previous[context_key] = weights
        expected_edge = sum(weights[key] * estimates[key][0] for key in weights if key in estimates)
        variance = sum(weights[key] * weights[key] * estimates[key][1] for key in weights if key in estimates)
        exposure = min(
            candidate.kelly_fraction * max(expected_edge, 0.0) / max(candidate.risk_penalty * variance, 1e-12),
            0.25 / math.sqrt(candidate.risk_penalty),
        )
        realized = group.set_index("strategy_id")["net_return"].astype(float).to_dict()
        if any(key not in realized for key, value in weights.items() if value > 0):
            raise ValueError("contextual validation lacks a weighted strategy execution outcome")
        trades.append(
            _ContextualReplayTrade(
                decision=pd.Timestamp(decision),
                resolved=pd.Timestamp(group["outcome_available_at"].max()),
                symbol=str(symbol),
                direction=direction,
                exposure=exposure / capital_slots,
                net_return=sum(weights[key] * realized[key] for key in weights),
                concentration=sum(value * value for value in weights.values()),
                expected_edge=expected_edge,
            )
        )

    sampled_returns, turnover, concentration = _replay_contextual_account(
        trades,
        sorted(validation["decision_timestamp"].unique()),
        rebalance_cost_rate,
    )
    returns = np.asarray(sampled_returns, dtype=float)
    standard_deviation = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / standard_deviation * math.sqrt(len(returns))) if standard_deviation > 0 else 0.0
    return ContextualFoldMetrics(
        net_sharpe=sharpe,
        maximum_drawdown=_maximum_drawdown(sampled_returns),
        turnover=turnover,
        concentration=concentration,
        fragmentation=min(len(contexts) / max(validation["decision_timestamp"].nunique(), 1), 1.0),
        observations=len(sampled_returns),
    )


def evaluate_contextual_candidate(
    candidate: ContextualCandidate,
    outcomes: pd.DataFrame,
    experiment: ContextualLearningExperiment,
) -> ContextualCandidateEvaluation:
    """Evaluate one closed policy on expanding, strictly chronological outer blocks."""

    frame = _contextual_learning_frame(outcomes, experiment)
    selected_horizons = {"long": candidate.long_holding_horizon_bars, "short": candidate.short_holding_horizon_bars}
    for direction in frame["direction"].unique():
        if selected_horizons[direction] not in set(frame.loc[frame["direction"] == direction, "holding_horizon_bars"]):
            raise ValueError(
                f"no actual execution evidence for {direction} holding horizon {selected_horizons[direction]}"
            )
    frame = frame.loc[frame["holding_horizon_bars"] == frame["direction"].map(selected_horizons)].copy()
    folds = tuple(
        _evaluate_contextual_fold(candidate, train, validation, experiment.rebalance_cost_rate)
        for train, validation in _contextual_outer_folds(frame, experiment)
    )
    sharpes = [item.net_sharpe for item in folds]
    baseline = ContextualCandidate.defaults()
    complexity = sum(getattr(candidate, name) != getattr(baseline, name) for name in _CONTEXTUAL_CANDIDATE_FIELDS)
    fitness = float(
        statistics.median(sharpes)
        - experiment.drawdown_penalty * statistics.median(item.maximum_drawdown for item in folds)
        - experiment.turnover_fitness_penalty * statistics.median(item.turnover for item in folds)
        - experiment.instability_penalty * (statistics.pstdev(sharpes) if len(sharpes) > 1 else 0.0)
        - experiment.fragmentation_penalty * statistics.median(item.fragmentation for item in folds)
        - experiment.concentration_penalty * statistics.median(item.concentration for item in folds)
        - experiment.complexity_penalty * complexity
    )
    evidence = {
        "candidate_hash": candidate.candidate_hash,
        "dataset_hash": experiment.dataset_hash,
        "protocol_hash": experiment.protocol_hash,
        "as_of": experiment.as_of,
        "sealed_final_start": experiment.sealed_final_start,
        "fold_metrics": tuple(asdict(item) for item in folds),
        "fitness": fitness,
        "state": "shadow",
        "accounting_version": 2,
        "rebalance_cost_rate": experiment.rebalance_cost_rate,
    }
    return ContextualCandidateEvaluation(
        candidate_hash=candidate.candidate_hash,
        status="succeeded",
        state="shadow",
        fold_metrics=folds,
        fitness=fitness,
        observations=sum(item.observations for item in folds),
        evidence_hash=canonical_hash(evidence),
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
            "available_at",
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
        execution_timestamps = frame[
            [
                "open_timestamp",
                "close_timestamp",
                experiment.availability_column,
                experiment.timestamp_column,
            ]
        ]
        if (execution_timestamps >= experiment.sealed_final_start).any().any():
            raise ValueError("execution timestamps must precede the sealed final boundary")
        if (execution_timestamps > experiment.as_of).any().any():
            raise ValueError("execution timestamps must be available as-of the learning experiment")
        if (frame["close_timestamp"] > frame[experiment.availability_column]).any():
            raise ValueError("execution bar close must be available no later than its decision")
        execution_numeric = ("open", "high", "low", "close", "volume")
        for column in execution_numeric:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if (
            frame[list(execution_numeric)].isna().any().any()
            or not np.isfinite(frame[list(execution_numeric)].to_numpy(dtype=float)).all()
        ):
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
                raise ValueError(f"indicator lag {node.lag} is outside maximum lag {experiment.maximum_lag}")
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
    causal_warmup = train.sort_values(experiment.timestamp_column, kind="stable").tail(experiment.maximum_lag + 1)
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
        (execution_frame["open_timestamp"] >= eligible_at) & (execution_frame["close_timestamp"] > last_decision)
    ].sort_values("open_timestamp", kind="stable")
    execution_end = (
        pd.Timestamp(future_execution.iloc[0]["close_timestamp"])
        if not future_execution.empty
        else pd.Timestamp(combined["close_timestamp"].max())
    )
    execution_start = pd.Timestamp(causal_warmup["open_timestamp"].min())
    execution_mask = (execution_frame["open_timestamp"] >= execution_start) & (
        execution_frame["close_timestamp"] <= execution_end
    )
    execution_bars = execution_frame.loc[
        execution_mask,
        [
            "symbol",
            "open_timestamp",
            "close_timestamp",
            "available_at",
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
_TRIAL_SOURCE_VERSION = "3"


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
        "schema_version": 3,
        "experiment_hash": _experiment_hash(experiment),
        "search_protocol_hash": _search_protocol_hash(experiment),
        "search_family": experiment.search_family,
        "development_evidence_digest": development_digest,
        "ordinal": trial.ordinal,
        "trial_id": trial.trial_id,
        "global_trial_id": trial.global_trial_id,
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
        "candidate_discovered_at": _timestamp_text(trial.candidate.discovered_at),
        "evaluated_at": _timestamp_text(trial.evaluated_at),
        "learning_run_id": experiment.learning_run_id,
        "dataset_hash": experiment.dataset_hash,
        "sealed_final_start": experiment.sealed_final_start.isoformat(),
        "symbol": experiment.symbol,
        "interval": experiment.interval.value,
        "mode": StrategyMode.WALK_FORWARD_LEARNING.value,
        "source": _TRIAL_SOURCE,
        "source_version": _TRIAL_SOURCE_VERSION,
        "created_at": _timestamp_text(trial.received_at),
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
                "development_data_through": experiment.development_data_through,
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


def _search_protocol_hash(experiment: LearningExperiment) -> str:
    evaluator = experiment.evaluator
    evaluator_identity = (
        "default_cost_aware_evaluator"
        if evaluator is None
        else f"{getattr(evaluator, '__module__', '')}.{getattr(evaluator, '__qualname__', type(evaluator).__name__)}"
    )
    return canonical_hash(
        {
            "schema_version": 1,
            "search_family": experiment.search_family,
            "dataset_hash": experiment.dataset_hash,
            "symbol": experiment.symbol,
            "interval": experiment.interval.value,
            "development_data_through": experiment.development_data_through,
            "sealed_final_start": experiment.sealed_final_start,
            "seed": experiment.seed,
            "inner_folds": [
                {"train_index": fold.train_index, "validation_index": fold.validation_index}
                for fold in experiment.inner_folds
            ],
            "indicators": experiment.indicators,
            "thresholds": experiment.thresholds,
            "seed_rules": [rule.canonical for rule in experiment.seed_rules],
            "grammar_bounds": {
                "max_depth": experiment.max_depth,
                "max_nodes": experiment.max_nodes,
                "maximum_lag": experiment.maximum_lag,
            },
            "execution_contract": _execution_contract(experiment),
            "columns": {
                "return": experiment.return_column,
                "timestamp": experiment.timestamp_column,
                "availability": experiment.availability_column,
                "outcome_availability": experiment.outcome_availability_column,
            },
            "penalties": {
                "drawdown": experiment.penalties.drawdown,
                "turnover": experiment.penalties.turnover,
                "instability": experiment.penalties.instability,
                "complexity": experiment.penalties.complexity,
            },
            "evaluator": evaluator_identity,
            "evaluator_version": experiment.evaluator_version,
            "evaluator_cost_contract": experiment.evaluator_cost_contract,
        }
    )


def _learning_global_trial_id(experiment: LearningExperiment, candidate_hash: str, ordinal: int) -> str:
    return global_trial_identity(
        search_family=experiment.search_family,
        dataset_hash=experiment.dataset_hash,
        protocol_hash=_search_protocol_hash(experiment),
        candidate_hash=candidate_hash,
        attempt_ordinal=ordinal,
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
                "global_trial_id": trial.global_trial_id,
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
                "created_at": trial.received_at,
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
        if payload.get("schema_version") != 3 or receipt != canonical_hash(payload):
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
        fitness_matches = (row_fitness is None and payload_fitness is None) or (
            row_fitness is not None
            and isinstance(payload_fitness, (float, int))
            and not isinstance(payload_fitness, bool)
            and math.isclose(row_fitness, float(payload_fitness), rel_tol=1e-6, abs_tol=1e-7)
        )
        mirrors = {
            "trial_id": str(row["trial_id"]),
            "global_trial_id": str(row["global_trial_id"]),
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
            "search_family": experiment.search_family,
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
        candidate_discovered_at = _explicit_utc(
            pd.Timestamp(payload.get("candidate_discovered_at")).to_pydatetime(),
            "candidate discovery",
        )
        candidate = RuleCandidate.from_rule(
            experiment,
            expected_rule or initial[-1],
            discovered_at=candidate_discovered_at,
        )
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
        expected_global_trial_id = _learning_global_trial_id(experiment, candidate.candidate_hash, ordinal)
        if payload.get("global_trial_id") != expected_global_trial_id:
            raise ValueError("persisted global trial identity is malformed")
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
        evaluated_at = _explicit_utc(
            pd.Timestamp(payload.get("evaluated_at")).to_pydatetime(),
            "trial evaluation",
        )
        received_at = _explicit_utc(
            pd.Timestamp(payload.get("created_at")).to_pydatetime(),
            "trial receipt",
        )
        if not candidate_discovered_at <= evaluated_at <= received_at:
            raise ValueError("persisted trial event chronology is malformed")
        trials.append(
            LearningTrial(
                ordinal=ordinal,
                trial_id=expected_trial_id,
                candidate=candidate,
                evaluated_at=evaluated_at,
                received_at=received_at,
                status=status,  # type: ignore[arg-type]
                fold_metrics=metrics,
                fitness=fitness,
                error_summary=error_summary if isinstance(error_summary, str) else None,
                global_trial_id=expected_global_trial_id,
            )
        )
    return trials


def _persist_discovery(
    experiment: LearningExperiment,
    candidate: RuleCandidate,
    trials: Sequence[LearningTrial],
    development_digest: str,
) -> RuleCandidate:
    rule_id = canonical_hash(
        {
            "learning_run_id": experiment.learning_run_id,
            "rule_hash": candidate.candidate_hash,
            "rule_version": candidate.version,
        }
    )
    if experiment.database is not None:
        existing = experiment.database.frame(
            "select discovered_at from discovered_rules where rule_id = :rule_id",
            {"rule_id": rule_id},
        )
        if not existing.empty:
            discovered_at = pd.Timestamp(existing.iloc[0]["discovered_at"]).to_pydatetime()
            return replace(candidate, discovered_at=discovered_at)
    discovered = replace(candidate, discovered_at=experiment.event_time())
    received_at = experiment.event_time()
    if experiment.database is None:
        return discovered
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
                "discovered_at": discovered.discovered_at,
                "state": "shadow",
                "rule": {
                    "schema_version": 1,
                    "strategy_id": candidate.strategy_id,
                    "canonical": json.loads(candidate.rule.canonical),
                    "plain_language": candidate.rule.render(),
                },
                "evidence": {
                    "development_evidence_through": experiment.development_data_through.isoformat(),
                    "final_boundary": experiment.sealed_final_start.isoformat(),
                    "development_evidence_digest": development_digest,
                    "experiment_hash": _experiment_hash(experiment),
                    "fitness": best.fitness,
                    "trial_count": len(trials),
                    "trial_ids": [trial.trial_id for trial in trials],
                },
                "source": _TRIAL_SOURCE,
                "source_version": _TRIAL_SOURCE_VERSION,
                "created_at": received_at,
            }
        ],
    )
    return discovered


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
        candidate = RuleCandidate.from_rule(experiment, rule or rules[-1])
        trial_id = canonical_hash(
            {
                "candidate_hash": candidate.candidate_hash,
                "learning_run_id": experiment.learning_run_id,
                "ordinal": ordinal,
            }
        )
        global_trial_id = _learning_global_trial_id(experiment, candidate.candidate_hash, ordinal)
        if rule is None:
            evaluated_at = experiment.event_time()
            received_at = experiment.event_time()
            trial = LearningTrial(
                ordinal,
                trial_id,
                candidate,
                evaluated_at,
                received_at,
                "budget_stop",
                error_summary="bounded semantic candidate space exhausted",
                global_trial_id=global_trial_id,
            )
            trials.append(trial)
            _persist_trial(experiment, trial, development_digest)
            continue
        try:
            _validate_rule_domain(experiment, candidate.rule)
        except ValueError as error:
            evaluated_at = experiment.event_time()
            received_at = experiment.event_time()
            trial = LearningTrial(
                ordinal,
                trial_id,
                candidate,
                evaluated_at,
                received_at,
                "invalid",
                error_summary=f"{type(error).__name__}: {error}",
                global_trial_id=global_trial_id,
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
            evaluated_at = experiment.event_time()
            received_at = experiment.event_time()
            trial = LearningTrial(
                ordinal,
                trial_id,
                candidate,
                evaluated_at,
                received_at,
                "failed",
                error_summary=f"{type(error).__name__}: {error}",
                global_trial_id=global_trial_id,
            )
            trials.append(trial)
            _persist_trial(experiment, trial, development_digest)
            continue
        evaluated_at = experiment.event_time()
        received_at = experiment.event_time()
        trial = LearningTrial(
            ordinal,
            trial_id,
            candidate,
            evaluated_at,
            received_at,
            "succeeded",
            tuple(fold_metrics),
            fitness,
            global_trial_id=global_trial_id,
        )
        trials.append(trial)
        _persist_trial(experiment, trial, development_digest)
        successful.append(candidate)
    ranked = sorted(
        (trial for trial in trials if trial.fitness is not None),
        key=lambda trial: (-float(trial.fitness), trial.candidate.candidate_hash),
    )
    best_candidate = ranked[0].candidate if ranked else None
    if best_candidate is not None:
        best_candidate = _persist_discovery(experiment, best_candidate, trials, development_digest)
    result = LearningResult(
        learning_run_id=experiment.learning_run_id,
        trials=tuple(trials),
        candidates=tuple(successful),
        best_candidate=best_candidate,
        stopped_reason="evaluation_budget_exhausted",
    )
    return result


def global_learning_trial_count(database: Database, *, dataset_hash: str, search_family: str) -> int:
    """Count distinct semantic evaluations across learning-run restarts."""
    family = search_family.strip().lower()
    if not family:
        raise ValueError("search_family must not be empty")
    if family == "contextual_policy_search":
        return int(
            database.scalar(
                "select count(distinct global_trial_id) from contextual_learning_trials "
                "where dataset_hash = :dataset_hash",
                {"dataset_hash": dataset_hash},
            )
            or 0
        )
    frame = database.frame(
        "select global_trial_id, candidate from learning_trials where dataset_hash = :dataset_hash",
        {"dataset_hash": dataset_hash},
    )
    if frame.empty:
        return 0
    identities: set[str] = set()
    for row in frame.itertuples(index=False):
        payload = _json_payload(row.candidate)
        if str(payload.get("search_family", "")) == family:
            identities.add(str(row.global_trial_id))
    return len(identities)


__all__ = [
    "ContextualCandidate",
    "ContextualCandidateEvaluation",
    "ContextualFoldMetrics",
    "ContextualLearningExperiment",
    "ContextualSearchSpace",
    "FitnessPenalties",
    "FoldMetrics",
    "LearningExperiment",
    "LearningResult",
    "LearningTrial",
    "RuleCandidate",
    "calculate_fitness",
    "discover_rules",
    "evaluate_contextual_candidate",
    "generate_contextual_candidates",
    "global_learning_trial_count",
]
