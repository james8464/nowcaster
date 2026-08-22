from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

import pandas as pd

from src.backtest.robustness import deflated_sharpe_probability
from src.database.engine import Database
from src.strategies.types import BarInterval, StrategyFamily, StrategyMode, canonical_hash
from src.strategies.validation import (
    EvaluationStatus,
    StrategyEvaluation,
    ValidationConfig,
    make_outer_folds,
    promotion_reasons,
    select_final_boundary,
)


@dataclass(frozen=True, slots=True)
class EnsembleConfig:
    equal_weight_shrinkage: float = 0.5
    maximum_strategy_weight: float = 0.4
    maximum_family_weight: float = 0.6
    sharpe_clip: float = 2.0
    sample_size_target: int = 100
    fixed_share: float = 0.05
    learning_rate: float = 1.0
    minimum_breadth: int = 2
    minimum_vote_margin: float = 0.15
    minimum_probability: float = 0.55
    cost_buffer_multiplier: float = 1.0

    def __post_init__(self) -> None:
        unit_interval = (
            self.equal_weight_shrinkage,
            self.maximum_strategy_weight,
            self.maximum_family_weight,
            self.fixed_share,
            self.minimum_vote_margin,
            self.minimum_probability,
        )
        if any(not math.isfinite(value) for value in unit_interval):
            raise ValueError("ensemble controls must be finite")
        if not 0 <= self.equal_weight_shrinkage <= 1:
            raise ValueError("equal-weight shrinkage must be in [0, 1]")
        if not 0 < self.maximum_strategy_weight <= 1 or not 0 < self.maximum_family_weight <= 1:
            raise ValueError("weight caps must be in (0, 1]")
        if not 0 <= self.fixed_share < 1:
            raise ValueError("fixed share must be in [0, 1)")
        if not 0 <= self.minimum_vote_margin <= 1 or not 0.5 <= self.minimum_probability <= 1:
            raise ValueError("decision thresholds are outside their probability ranges")
        if self.sharpe_clip <= 0 or self.sample_size_target <= 0 or self.learning_rate <= 0:
            raise ValueError("evidence and learning scales must be positive")
        if self.minimum_breadth <= 0 or self.cost_buffer_multiplier < 0:
            raise ValueError("breadth must be positive and the cost buffer cannot be negative")


DEFAULT_ENSEMBLE_CONFIG = EnsembleConfig()


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in sorted(value.items(), key=str)})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_deep_freeze(item) for item in value), key=str))
    return value


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class EvidenceWeight:
    strategy_id: str
    strategy_version: str
    family: StrategyFamily
    weight: float
    prior_weight: float
    evidence_score: float
    effective_at: datetime
    outcomes_through: datetime | None
    dataset_hash: str
    symbol: str
    interval: BarInterval
    mode: StrategyMode
    provenance: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", _deep_freeze(self.provenance))


@dataclass(frozen=True, slots=True)
class ComponentContribution:
    strategy_id: str
    strategy_version: str
    weight: float
    signal: int
    normalized_vote: float
    contribution: float
    decision_timestamp: datetime
    data_through: datetime
    expected_edge: float
    expected_cost: float
    uncertainty: float


@dataclass(frozen=True, slots=True)
class EnsembleDecision:
    as_of: datetime
    signal: int
    status: str
    reasons: tuple[str, ...]
    probability: float
    vote_margin: float
    expected_net_edge: float
    estimated_cost: float
    uncertainty_buffer: float
    breadth: int
    data_through: datetime | None
    dataset_hash: str
    symbol: str
    interval: BarInterval
    mode: StrategyMode
    component_versions: tuple[tuple[str, str], ...]
    component_states: tuple[Mapping[str, Any], ...]
    weights: tuple[EvidenceWeight, ...]
    contributions: tuple[ComponentContribution, ...]
    config: EnsembleConfig
    decision_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "component_states", tuple(_deep_freeze(item) for item in self.component_states))
        object.__setattr__(self, "decision_hash", canonical_decision_hash(self))


def _config_payload(config: EnsembleConfig) -> dict[str, Any]:
    return {
        "equal_weight_shrinkage": config.equal_weight_shrinkage,
        "maximum_strategy_weight": config.maximum_strategy_weight,
        "maximum_family_weight": config.maximum_family_weight,
        "sharpe_clip": config.sharpe_clip,
        "sample_size_target": config.sample_size_target,
        "fixed_share": config.fixed_share,
        "learning_rate": config.learning_rate,
        "minimum_breadth": config.minimum_breadth,
        "minimum_vote_margin": config.minimum_vote_margin,
        "minimum_probability": config.minimum_probability,
        "cost_buffer_multiplier": config.cost_buffer_multiplier,
    }


def _decision_payload(decision: EnsembleDecision) -> dict[str, Any]:
    return {
        "context": {
            "dataset_hash": decision.dataset_hash,
            "symbol": decision.symbol,
            "interval": decision.interval,
            "mode": decision.mode,
        },
        "as_of": decision.as_of,
        "signal": decision.signal,
        "status": decision.status,
        "reasons": decision.reasons,
        "probability": decision.probability,
        "vote_margin": decision.vote_margin,
        "expected_net_edge": decision.expected_net_edge,
        "estimated_cost": decision.estimated_cost,
        "uncertainty_buffer": decision.uncertainty_buffer,
        "breadth": decision.breadth,
        "data_through": decision.data_through,
        "component_versions": decision.component_versions,
        "config": _config_payload(decision.config),
        "components": decision.component_states,
        "contributions": tuple(
            {
                "strategy_id": contribution.strategy_id,
                "strategy_version": contribution.strategy_version,
                "weight": contribution.weight,
                "signal": contribution.signal,
                "normalized_vote": contribution.normalized_vote,
                "contribution": contribution.contribution,
                "decision_timestamp": contribution.decision_timestamp,
                "data_through": contribution.data_through,
                "expected_edge": contribution.expected_edge,
                "expected_cost": contribution.expected_cost,
                "uncertainty": contribution.uncertainty,
            }
            for contribution in decision.contributions
        ),
        "weights": tuple(
            {
                "strategy_id": weight.strategy_id,
                "strategy_version": weight.strategy_version,
                "family": weight.family,
                "weight": weight.weight,
                "prior_weight": weight.prior_weight,
                "evidence_score": weight.evidence_score,
                "effective_at": weight.effective_at,
                "outcomes_through": weight.outcomes_through,
                "dataset_hash": weight.dataset_hash,
                "symbol": weight.symbol,
                "interval": weight.interval,
                "mode": weight.mode,
                "provenance": weight.provenance,
            }
            for weight in decision.weights
        ),
    }


def canonical_decision_hash(decision: EnsembleDecision) -> str:
    return canonical_hash(_decision_payload(decision))


@dataclass(frozen=True, slots=True)
class _EnsembleContext:
    dataset_hash: str
    symbol: str
    interval: BarInterval
    mode: StrategyMode


def _require_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is not UTC:
        raise ValueError(f"{name} must be an explicit UTC datetime")
    return value


def _evaluation_context(evaluations: Sequence[StrategyEvaluation]) -> _EnsembleContext:
    if not evaluations:
        raise ValueError("a homogeneous evaluation context requires at least one strategy")
    if len({evaluation.strategy_id for evaluation in evaluations}) != len(evaluations):
        raise ValueError("strategy evaluations must have unique identifiers")
    contexts = {
        (evaluation.dataset_hash, evaluation.symbol, evaluation.interval, evaluation.mode)
        for evaluation in evaluations
    }
    if len(contexts) != 1:
        raise ValueError("strategy evaluations must use one homogeneous evaluation context")
    dataset_hash, symbol, interval, mode = contexts.pop()
    if not dataset_hash.strip() or not symbol.strip():
        raise ValueError("homogeneous evaluation context identifiers must not be empty")
    if any(not evaluation.strategy_version.strip() for evaluation in evaluations):
        raise ValueError("strategy versions must not be empty")
    return _EnsembleContext(dataset_hash, symbol.strip().upper(), interval, mode)


def _weight_context(weights: Sequence[EvidenceWeight]) -> _EnsembleContext:
    if not weights:
        raise ValueError("a homogeneous weight context requires at least one strategy")
    if len({weight.strategy_id for weight in weights}) != len(weights):
        raise ValueError("evidence weights must have unique strategy identifiers")
    contexts = {(weight.dataset_hash, weight.symbol, weight.interval, weight.mode) for weight in weights}
    snapshots = {(weight.effective_at, weight.outcomes_through) for weight in weights}
    if len(contexts) != 1 or len(snapshots) != 1:
        raise ValueError("evidence weights must use one homogeneous weight context")
    dataset_hash, symbol, interval, mode = contexts.pop()
    if not dataset_hash.strip() or not symbol.strip():
        raise ValueError("homogeneous weight context identifiers must not be empty")
    for weight in weights:
        _require_utc(weight.effective_at, "weight effective_at")
        if weight.outcomes_through is not None:
            _require_utc(weight.outcomes_through, "weight outcomes_through")
        if not weight.strategy_version.strip():
            raise ValueError("strategy versions must not be empty")
        if not math.isfinite(weight.weight) or weight.weight < 0:
            raise ValueError("evidence weights must be finite and non-negative")
    return _EnsembleContext(dataset_hash, symbol.strip().upper(), interval, mode)


def _utc_evidence_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError("sealed evidence timestamps must be timezone-aware")
    return timestamp.tz_convert("UTC")


_VALIDATION_SNAPSHOT_FIELDS = {
    "schema_version",
    "context",
    "chronology",
    "chronology_hash",
    "outcome_availability",
    "outcome_availability_hash",
    "validation_config",
    "final_boundary",
    "fold_plan",
    "trial_records",
    "fold_records",
    "derived",
    "promotion",
}
_PROMOTION_INPUT_FIELDS = {
    "status",
    "development_sharpe",
    "downside_risk",
    "maximum_drawdown",
    "calibration_error",
    "fold_stability",
    "cost_survives",
    "observations",
    "trades",
    "dsr_probability",
    "trial_sharpes",
    "causal_audit_passed",
}


def _root_validation_snapshot_is_auditable(evaluation: StrategyEvaluation) -> bool:
    provenance = evaluation.evidence_provenance
    snapshot = provenance["validation_snapshot"]
    if not isinstance(snapshot, Mapping) or set(snapshot) != _VALIDATION_SNAPSHOT_FIELDS:
        return False
    schema_version = snapshot["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
        return False
    if provenance["validation_snapshot_hash"] != canonical_hash(snapshot):
        return False
    context = {
        "dataset_hash": evaluation.dataset_hash,
        "strategy_id": evaluation.strategy_id,
        "strategy_version": evaluation.strategy_version,
        "family": evaluation.family.value,
        "symbol": evaluation.symbol,
        "interval": evaluation.interval.value,
        "mode": evaluation.mode.value,
    }
    if canonical_hash(snapshot["context"]) != canonical_hash(context):
        return False
    chronology = tuple(_utc_evidence_timestamp(value) for value in snapshot["chronology"])
    availability = tuple(_utc_evidence_timestamp(value) for value in snapshot["outcome_availability"])
    if not chronology or len(chronology) != len(availability):
        return False
    if tuple(sorted(chronology)) != chronology or len(set(chronology)) != len(chronology):
        return False
    if snapshot["chronology_hash"] != canonical_hash(chronology):
        return False
    if snapshot["outcome_availability_hash"] != canonical_hash(availability):
        return False
    config_record = snapshot["validation_config"]
    config = ValidationConfig(
        final_test_fraction=float(config_record["final_test_fraction"]),
        minimum_train_observations=int(config_record["minimum_train_observations"]),
        validation_observations=int(config_record["validation_observations"]),
        forecast_horizon=timedelta(seconds=float(config_record["forecast_horizon_seconds"])),
        publication_delay=timedelta(seconds=float(config_record["publication_delay_seconds"])),
        embargo=timedelta(seconds=float(config_record["embargo_seconds"])),
        periods_per_year=int(config_record["periods_per_year"]),
        minimum_trades=int(config_record["minimum_trades"]),
        minimum_development_observations=int(config_record["minimum_development_observations"]),
        maximum_drawdown=float(config_record["maximum_drawdown"]),
        minimum_dsr_probability=float(config_record["minimum_dsr_probability"]),
    )
    expected_config = {
        "final_test_fraction": config.final_test_fraction,
        "minimum_train_observations": config.minimum_train_observations,
        "validation_observations": config.validation_observations,
        "forecast_horizon_seconds": config.forecast_horizon.total_seconds(),
        "publication_delay_seconds": config.publication_delay.total_seconds(),
        "embargo_seconds": config.embargo.total_seconds(),
        "periods_per_year": config.periods_per_year,
        "minimum_trades": config.minimum_trades,
        "minimum_development_observations": config.minimum_development_observations,
        "maximum_drawdown": config.maximum_drawdown,
        "minimum_dsr_probability": config.minimum_dsr_probability,
    }
    if canonical_hash(config_record) != canonical_hash(expected_config):
        return False
    boundary = select_final_boundary(chronology, final_test_fraction=config.final_test_fraction)
    expected_boundary = {
        "final_start": boundary.final_start.to_pydatetime(),
        "development_index": boundary.development_index,
        "final_index": boundary.final_index,
    }
    if canonical_hash(snapshot["final_boundary"]) != canonical_hash(expected_boundary):
        return False
    if _utc_evidence_timestamp(provenance["sealed_boundary"]) != boundary.final_start:
        return False
    validation_data = pd.DataFrame(
        {
            "decision_timestamp": chronology,
            "outcome_available_at": availability,
        }
    )
    folds = make_outer_folds(validation_data, boundary=boundary, config=config)
    expected_plan = tuple(
        {
            "fold": fold_number,
            "train_index": fold.train_index,
            "validation_index": fold.validation_index,
            "inner_folds": tuple(
                {"train_index": inner.train_index, "validation_index": inner.validation_index}
                for inner in fold.inner_folds
            ),
        }
        for fold_number, fold in enumerate(folds)
    )
    if canonical_hash(snapshot["fold_plan"]) != canonical_hash(expected_plan):
        return False
    trial_records = tuple(snapshot["trial_records"])
    fold_records = tuple(snapshot["fold_records"])
    if canonical_hash(trial_records) != canonical_hash(provenance["trials"]):
        return False
    if canonical_hash(fold_records) != canonical_hash(provenance["folds"]):
        return False
    if len(fold_records) != len(expected_plan):
        return False
    for record, plan in zip(fold_records, expected_plan, strict=True):
        validation_index = plan["validation_index"]
        if int(record["fold"]) != int(plan["fold"]):
            return False
        if _utc_evidence_timestamp(record["validation_start"]) != chronology[validation_index[0]]:
            return False
        if _utc_evidence_timestamp(record["validation_end"]) != chronology[validation_index[-1]]:
            return False
    derived = snapshot["derived"]
    if not isinstance(derived, Mapping) or set(derived) != _PROMOTION_INPUT_FIELDS:
        return False
    if canonical_hash(derived) != canonical_hash(provenance["promotion_inputs"]):
        return False
    expected_reasons = promotion_reasons(derived, config)
    expected_promotion = {"promoted": not expected_reasons, "reasons": expected_reasons}
    if canonical_hash(snapshot["promotion"]) != canonical_hash(expected_promotion):
        return False
    return canonical_hash(snapshot["promotion"]) == canonical_hash(provenance["promotion_decision"])


def _sealed_evidence_is_auditable(evaluation: StrategyEvaluation) -> bool:
    provenance = evaluation.evidence_provenance
    try:
        if not _root_validation_snapshot_is_auditable(evaluation):
            return False
        if provenance.get("sealed") is not True or provenance.get("trial_source") != "timestamped_trial_evidence":
            return False
        boundary = _utc_evidence_timestamp(provenance["sealed_boundary"])
        trials = tuple(provenance["trials"])
        folds = tuple(provenance["folds"])
        if provenance["trial_evidence_hash"] != canonical_hash(trials):
            return False
        if provenance["fold_evidence_hash"] != canonical_hash(folds):
            return False

        trial_ids: set[str] = set()
        trial_sharpes: list[float] = []
        for trial in trials:
            trial_id = str(trial["trial_id"])
            sharpe = float(trial["sharpe"])
            training_end = _utc_evidence_timestamp(trial["training_end"])
            evaluated_at = _utc_evidence_timestamp(trial["evaluated_at"])
            if (
                not trial_id
                or trial_id in trial_ids
                or not math.isfinite(sharpe)
                or training_end > evaluated_at
                or evaluated_at >= boundary
            ):
                return False
            trial_ids.add(trial_id)
            trial_sharpes.append(sharpe)
        if tuple(trial_sharpes) != tuple(evaluation.trial_sharpes):
            return False

        fold_ids: set[int] = set()
        fold_sharpes: list[float] = []
        calibration_errors: list[float] = []
        for fold in folds:
            fold_id = int(fold["fold"])
            validation_start = _utc_evidence_timestamp(fold["validation_start"])
            validation_end = _utc_evidence_timestamp(fold["validation_end"])
            evaluated_at = _utc_evidence_timestamp(fold["evaluated_at"])
            fold_sharpe = float(fold["sharpe"])
            calibration_error = float(fold["calibration_error"])
            if (
                fold_id < 0
                or fold_id in fold_ids
                or not validation_start < validation_end <= evaluated_at < boundary
                or not math.isfinite(fold_sharpe)
                or not math.isfinite(calibration_error)
                or not 0 <= calibration_error <= 1
            ):
                return False
            fold_ids.add(fold_id)
            fold_sharpes.append(fold_sharpe)
            calibration_errors.append(calibration_error)
        stability = sum(value > 0 for value in fold_sharpes) / len(fold_sharpes) if fold_sharpes else 0.0
        calibration = sum(calibration_errors) / len(calibration_errors) if calibration_errors else 1.0
        if evaluation.fold_stability is None or evaluation.calibration_error is None:
            return False
        if not math.isclose(float(evaluation.fold_stability), stability, abs_tol=1e-15):
            return False
        if not math.isclose(float(evaluation.calibration_error), calibration, abs_tol=1e-15):
            return False

        promotion_inputs = {
            "status": evaluation.status.value,
            "development_sharpe": evaluation.development_sharpe,
            "downside_risk": evaluation.downside_risk,
            "maximum_drawdown": evaluation.development_maximum_drawdown,
            "calibration_error": evaluation.calibration_error,
            "fold_stability": evaluation.fold_stability,
            "cost_survives": evaluation.cost_survives,
            "observations": evaluation.observations,
            "trades": evaluation.trades,
            "dsr_probability": evaluation.dsr_probability,
            "trial_sharpes": evaluation.trial_sharpes,
            "causal_audit_passed": evaluation.causal_audit_passed,
        }
        promotion_decision = {
            "promoted": evaluation.promotion.promoted,
            "reasons": evaluation.promotion.reasons,
        }
        promotion_payload = {
            "promotion_inputs": provenance["promotion_inputs"],
            "promotion_decision": provenance["promotion_decision"],
        }
        if provenance["promotion_evidence_hash"] != canonical_hash(promotion_payload):
            return False
        if canonical_hash(provenance["promotion_inputs"]) != canonical_hash(promotion_inputs):
            return False
        return canonical_hash(provenance["promotion_decision"]) == canonical_hash(promotion_decision)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _evidence_score(evaluation: StrategyEvaluation, config: EnsembleConfig) -> tuple[float, dict[str, Any]]:
    sealed = _sealed_evidence_is_auditable(evaluation)
    eligible = (
        evaluation.status is EvaluationStatus.EVALUATED
        and evaluation.promotion.promoted
        and evaluation.causal_audit_passed
        and evaluation.cost_survives is True
        and evaluation.development_sharpe is not None
        and len(evaluation.trial_sharpes) >= 2
        and evaluation.observations >= 3
        and sealed
    )
    if not eligible:
        return 0.0, {
            "eligible": False,
            "reason": "promotion, causal, cost, or sealed observed-trial evidence gate failed",
            "validation_evidence": evaluation.evidence_provenance,
        }
    sharpe = float(evaluation.development_sharpe)
    if not math.isfinite(sharpe) or sharpe <= 0:
        return 0.0, {"eligible": False, "reason": "development Sharpe is not positive and finite"}
    try:
        dsr = deflated_sharpe_probability(
            sharpe,
            observations=evaluation.observations,
            trial_sharpes=evaluation.trial_sharpes,
            skew=0.0,
            kurtosis=3.0,
        )
    except ValueError:
        return 0.0, {"eligible": False, "reason": "observed trial Sharpe vector is invalid"}
    sharpe_score = min(sharpe, config.sharpe_clip) / config.sharpe_clip
    downside_score = 1 / (1 + max(float(evaluation.downside_risk or 0), 0))
    calibration_score = 1 - min(max(float(evaluation.calibration_error or 0), 0), 1)
    stability_score = min(max(float(evaluation.fold_stability or 0), 0), 1)
    sample_score = min(evaluation.observations / config.sample_size_target, 1)
    score = sharpe_score * downside_score * calibration_score * stability_score * sample_score * dsr
    provenance = {
        "eligible": True,
        "development_sharpe": sharpe,
        "downside_score": downside_score,
        "calibration_score": calibration_score,
        "stability_score": stability_score,
        "sample_score": sample_score,
        "cost_survives": True,
        "trial_sharpes": tuple(evaluation.trial_sharpes),
        "trial_count": len(evaluation.trial_sharpes),
        "deflated_sharpe_probability": dsr,
        "multiple_testing_source": "observed_trial_sharpes",
        "validation_evidence": evaluation.evidence_provenance,
    }
    return max(float(score), 0.0), provenance


def _allocate_capped(
    targets: Mapping[str, float],
    capacities: Mapping[str, float],
    *,
    total: float,
) -> dict[str, float]:
    if total < 0 or sum(capacities.values()) + 1e-12 < total:
        raise ValueError("weight caps are infeasible for the eligible strategies and families")
    allocated = {key: 0.0 for key in targets}
    active = set(targets)
    remaining = total
    while active and remaining > 1e-14:
        target_sum = sum(max(targets[key], 0.0) for key in active)
        basis = {key: (max(targets[key], 0.0) if target_sum > 0 else 1.0) for key in active}
        basis_sum = sum(basis.values())
        proposals = {key: remaining * basis[key] / basis_sum for key in active}
        saturated = [key for key in active if proposals[key] > capacities[key] - allocated[key] + 1e-14]
        if not saturated:
            for key, proposal in proposals.items():
                allocated[key] += proposal
            remaining = 0.0
            break
        for key in sorted(saturated):
            room = max(capacities[key] - allocated[key], 0.0)
            allocated[key] += room
            remaining -= room
            active.remove(key)
    if remaining > 1e-10:
        raise ValueError("weight caps could not conserve ensemble mass")
    correction = total - sum(allocated.values())
    if abs(correction) > 0 and active:
        key = sorted(active)[0]
        allocated[key] += correction
    return allocated


def _project_caps(
    desired: Mapping[str, float],
    families: Mapping[str, StrategyFamily],
    config: EnsembleConfig,
) -> dict[str, float]:
    by_family: dict[StrategyFamily, list[str]] = {}
    for strategy_id, family in families.items():
        by_family.setdefault(family, []).append(strategy_id)
    family_targets = {
        family.value: sum(desired[strategy_id] for strategy_id in members)
        for family, members in by_family.items()
    }
    family_capacities = {
        family.value: min(config.maximum_family_weight, len(members) * config.maximum_strategy_weight)
        for family, members in by_family.items()
    }
    family_mass = _allocate_capped(family_targets, family_capacities, total=1.0)
    result: dict[str, float] = {}
    for family, members in sorted(by_family.items(), key=lambda item: item[0].value):
        member_targets = {strategy_id: desired[strategy_id] for strategy_id in members}
        member_caps = {strategy_id: config.maximum_strategy_weight for strategy_id in members}
        result.update(_allocate_capped(member_targets, member_caps, total=family_mass[family.value]))
    return result


_ONLINE_PROVENANCE_FIELDS = {
    "weight_cohort_snapshot",
    "weight_cohort_hash",
    "online_method",
    "fixed_share",
    "learning_rate_ceiling",
    "outcomes_through",
    "online_state",
}


def _offline_provenance_hash(provenance: Mapping[str, Any]) -> str:
    offline = {key: value for key, value in provenance.items() if key not in _ONLINE_PROVENANCE_FIELDS}
    return canonical_hash(_deep_thaw(offline))


def _weight_cohort_snapshot(weights: Sequence[EvidenceWeight]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "members": tuple(
            {
                "strategy_id": weight.strategy_id,
                "strategy_version": weight.strategy_version,
                "family": weight.family.value,
                "dataset_hash": weight.dataset_hash,
                "symbol": weight.symbol,
                "interval": weight.interval.value,
                "mode": weight.mode.value,
                "base_weight": weight.weight,
                "prior_weight": weight.prior_weight,
                "evidence_score": weight.evidence_score,
                "initial_effective_at": weight.effective_at.isoformat(),
                "initial_outcomes_through": (
                    weight.outcomes_through.isoformat() if weight.outcomes_through is not None else None
                ),
                "offline_provenance_hash": _offline_provenance_hash(weight.provenance),
            }
            for weight in weights
        ),
    }


def _attach_weight_cohort(weights: Sequence[EvidenceWeight]) -> tuple[EvidenceWeight, ...]:
    snapshot = _weight_cohort_snapshot(weights)
    snapshot_hash = canonical_hash(snapshot)
    attached: list[EvidenceWeight] = []
    for weight in weights:
        provenance = _deep_thaw(weight.provenance)
        provenance["weight_cohort_snapshot"] = snapshot
        provenance["weight_cohort_hash"] = snapshot_hash
        attached.append(replace(weight, provenance=provenance))
    return tuple(attached)


def compute_evidence_weights(
    evaluations: Sequence[StrategyEvaluation],
    *,
    as_of: datetime,
    config: EnsembleConfig = DEFAULT_ENSEMBLE_CONFIG,
) -> tuple[EvidenceWeight, ...]:
    _require_utc(as_of, "as_of")
    _evaluation_context(evaluations)
    ordered = tuple(sorted(evaluations, key=lambda item: (item.strategy_id, item.strategy_version)))
    scored = [_evidence_score(evaluation, config) for evaluation in ordered]
    eligible = [index for index, (score, _) in enumerate(scored) if score > 0]
    desired: dict[str, float] = {}
    if eligible:
        score_total = sum(scored[index][0] for index in eligible)
        prior = 1 / len(eligible)
        for index in eligible:
            evaluation = ordered[index]
            evidence_share = scored[index][0] / score_total
            desired[evaluation.strategy_id] = (
                (1 - config.equal_weight_shrinkage) * evidence_share + config.equal_weight_shrinkage * prior
            )
        projected = _project_caps(
            desired,
            {ordered[index].strategy_id: ordered[index].family for index in eligible},
            config,
        )
    else:
        prior = 0.0
        projected = {}

    weights: list[EvidenceWeight] = []
    for evaluation, (score, provenance) in zip(ordered, scored, strict=True):
        weights.append(
            EvidenceWeight(
                strategy_id=evaluation.strategy_id,
                strategy_version=evaluation.strategy_version,
                family=evaluation.family,
                weight=float(projected.get(evaluation.strategy_id, 0.0)),
                prior_weight=prior if evaluation.strategy_id in projected else 0.0,
                evidence_score=score,
                effective_at=as_of,
                outcomes_through=None,
                dataset_hash=evaluation.dataset_hash,
                symbol=evaluation.symbol,
                interval=evaluation.interval,
                mode=evaluation.mode,
                provenance=MappingProxyType(provenance),
            )
        )
    return _attach_weight_cohort(weights)


def _strict_utc_outcome_column(values: pd.Series, name: str) -> pd.Series:
    timestamps: list[pd.Timestamp] = []
    for value in values:
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"resolved outcome {name} values must be timezone-aware") from error
        if pd.isna(timestamp) or timestamp.tzinfo is None:
            raise ValueError(f"resolved outcome {name} values must be timezone-aware")
        timestamps.append(timestamp.tz_convert("UTC"))
    return pd.Series(timestamps, index=values.index)


def _outcome_record(row: Any) -> dict[str, Any]:
    identity = {
        "strategy_id": str(row.strategy_id),
        "dataset_hash": str(row.dataset_hash),
        "strategy_version": str(row.strategy_version),
        "symbol": str(row.symbol),
        "interval": str(row.interval),
        "mode": str(row.mode),
        "decision_timestamp": pd.Timestamp(row.decision_timestamp).isoformat(),
        "outcome_available_at": pd.Timestamp(row.outcome_available_at).isoformat(),
    }
    return {
        "outcome_id": canonical_hash(identity),
        **identity,
        "signal": int(row.signal),
        "realized_return": float(row.realized_return),
        "cost": float(row.cost),
    }


_COHORT_MEMBER_FIELDS = {
    "strategy_id",
    "strategy_version",
    "family",
    "dataset_hash",
    "symbol",
    "interval",
    "mode",
    "base_weight",
    "prior_weight",
    "evidence_score",
    "initial_effective_at",
    "initial_outcomes_through",
    "offline_provenance_hash",
}
_OUTCOME_RECORD_FIELDS = {
    "outcome_id",
    "strategy_id",
    "dataset_hash",
    "strategy_version",
    "symbol",
    "interval",
    "mode",
    "decision_timestamp",
    "outcome_available_at",
    "signal",
    "realized_return",
    "cost",
}


def _validated_weight_cohort(weights: Sequence[EvidenceWeight]) -> Mapping[str, Any]:
    snapshots = [_deep_thaw(weight.provenance.get("weight_cohort_snapshot")) for weight in weights]
    hashes = [weight.provenance.get("weight_cohort_hash") for weight in weights]
    if not snapshots or len({canonical_hash(snapshot) for snapshot in snapshots}) != 1 or len(set(hashes)) != 1:
        raise ValueError("evidence weight cohort snapshots must be homogeneous")
    snapshot = snapshots[0]
    if not isinstance(snapshot, dict) or set(snapshot) != {"schema_version", "members"}:
        raise ValueError("evidence weight cohort snapshot is invalid")
    if snapshot["schema_version"] != 1 or hashes[0] != canonical_hash(snapshot):
        raise ValueError("evidence weight cohort hash is invalid")
    members = tuple(snapshot["members"])
    if len(members) != len(weights):
        raise ValueError("evidence weight cohort membership is invalid")
    for weight, member in zip(weights, members, strict=True):
        if not isinstance(member, dict) or set(member) != _COHORT_MEMBER_FIELDS:
            raise ValueError("evidence weight cohort member is invalid")
        current = {
            "strategy_id": weight.strategy_id,
            "strategy_version": weight.strategy_version,
            "family": weight.family.value,
            "dataset_hash": weight.dataset_hash,
            "symbol": weight.symbol,
            "interval": weight.interval.value,
            "mode": weight.mode.value,
            "prior_weight": weight.prior_weight,
            "evidence_score": weight.evidence_score,
            "offline_provenance_hash": _offline_provenance_hash(weight.provenance),
        }
        expected = {key: member[key] for key in current}
        if canonical_hash(current) != canonical_hash(expected):
            raise ValueError("evidence weight cohort no longer matches its original members")
    return snapshot


def _normalized_persisted_outcome(
    record: Mapping[str, Any],
    *,
    weight_by_strategy: Mapping[str, EvidenceWeight],
    as_of: datetime,
) -> dict[str, Any]:
    if set(record) != _OUTCOME_RECORD_FIELDS:
        raise ValueError("persisted outcome record schema is invalid")
    try:
        decision_timestamp = _utc_evidence_timestamp(record["decision_timestamp"])
        outcome_available_at = _utc_evidence_timestamp(record["outcome_available_at"])
        signal = int(record["signal"])
        realized_return = float(record["realized_return"])
        cost = float(record["cost"])
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("persisted outcome record values are invalid") from error
    if isinstance(record["signal"], bool) or float(record["signal"]) != signal or signal not in (-1, 0, 1):
        raise ValueError("persisted outcome signal is invalid")
    if not math.isfinite(realized_return) or not math.isfinite(cost) or cost < 0:
        raise ValueError("persisted outcome numeric values are invalid")
    if outcome_available_at < decision_timestamp or outcome_available_at > pd.Timestamp(as_of):
        raise ValueError("persisted outcome chronology is invalid")
    strategy_id = str(record["strategy_id"]).strip()
    weight = weight_by_strategy.get(strategy_id)
    context = {
        "dataset_hash": str(record["dataset_hash"]).strip(),
        "strategy_version": str(record["strategy_version"]).strip(),
        "symbol": str(record["symbol"]).strip().upper(),
        "interval": str(record["interval"]).strip(),
        "mode": str(record["mode"]).strip(),
    }
    if weight is None or tuple(context.values()) != (
        weight.dataset_hash,
        weight.strategy_version,
        weight.symbol,
        weight.interval.value,
        weight.mode.value,
    ):
        raise ValueError("persisted outcome context is foreign to its weight cohort")
    identity = {
        "strategy_id": strategy_id,
        **context,
        "decision_timestamp": decision_timestamp.isoformat(),
        "outcome_available_at": outcome_available_at.isoformat(),
    }
    outcome_id = canonical_hash(identity)
    if str(record["outcome_id"]) != outcome_id:
        raise ValueError("persisted outcome identity does not match its canonical content")
    return {
        "outcome_id": outcome_id,
        **identity,
        "signal": signal,
        "realized_return": realized_return,
        "cost": cost,
    }


def fixed_share_update(
    weights: Sequence[EvidenceWeight],
    resolved_outcomes: pd.DataFrame,
    *,
    as_of: datetime,
    config: EnsembleConfig = DEFAULT_ENSEMBLE_CONFIG,
) -> tuple[EvidenceWeight, ...]:
    _require_utc(as_of, "as_of")
    context = _weight_context(weights)
    ordered_weights = tuple(sorted(weights, key=lambda item: (item.strategy_id, item.strategy_version)))
    cohort = _validated_weight_cohort(ordered_weights)
    weight_by_strategy = {weight.strategy_id: weight for weight in ordered_weights}
    for weight in ordered_weights:
        if as_of < weight.effective_at or (
            weight.outcomes_through is not None and as_of < weight.outcomes_through
        ):
            raise ValueError("as_of cannot precede an evidence weight snapshot or outcome watermark")
        if weight.outcomes_through is not None and weight.outcomes_through > weight.effective_at:
            raise ValueError("an outcome watermark cannot follow its effective weight timestamp")
    if context.mode is StrategyMode.FROZEN:
        raise ValueError("frozen evidence weights cannot receive outcome feedback")

    config_hash = canonical_hash(
        {
            "fixed_share": config.fixed_share,
            "learning_rate": config.learning_rate,
            "maximum_strategy_weight": config.maximum_strategy_weight,
            "maximum_family_weight": config.maximum_family_weight,
        }
    )
    cohort_hash = canonical_hash(cohort)
    expected_base_rows = tuple(
        {"strategy_id": member["strategy_id"], "weight": member["base_weight"]}
        for member in cohort["members"]
    )
    stored_states = [_deep_thaw(weight.provenance.get("online_state", {})) for weight in ordered_weights]
    if len({canonical_hash(state) for state in stored_states}) != 1:
        raise ValueError("evidence weights must share one homogeneous persisted online state")
    stored_state = stored_states[0]
    history_by_id: dict[str, dict[str, Any]] = {}
    if stored_state:
        state_fields = {
            "config_hash",
            "cohort_hash",
            "base_weights",
            "processed_outcome_ids",
            "processed_outcomes",
            "processed_outcomes_hash",
            "adaptive_learning_rates",
            "cumulative_mixability_gap",
            "state_hash",
        }
        if not isinstance(stored_state, dict) or set(stored_state) != state_fields:
            raise ValueError("persisted online state schema is invalid")
        if stored_state["config_hash"] != config_hash:
            raise ValueError("online feedback configuration cannot change during replay")
        if stored_state["cohort_hash"] != cohort_hash:
            raise ValueError("persisted online state does not match the weight cohort")
        base_weight_rows = tuple(stored_state["base_weights"])
        if canonical_hash(base_weight_rows) != canonical_hash(expected_base_rows):
            raise ValueError("persisted online base weights do not match the weight cohort")
        persisted_records = tuple(stored_state["processed_outcomes"])
        if stored_state["processed_outcomes_hash"] != canonical_hash(persisted_records):
            raise ValueError("persisted outcome history content hash is invalid")
        persisted_ids = tuple(str(value) for value in stored_state["processed_outcome_ids"])
        if persisted_ids != tuple(str(record.get("outcome_id", "")) for record in persisted_records):
            raise ValueError("persisted outcome identities do not match their history")
        for raw_record in persisted_records:
            if not isinstance(raw_record, Mapping):
                raise ValueError("persisted outcome record schema is invalid")
            normalized = _normalized_persisted_outcome(
                raw_record,
                weight_by_strategy=weight_by_strategy,
                as_of=as_of,
            )
            if canonical_hash(raw_record) != canonical_hash(normalized):
                raise ValueError("persisted outcome record is not in canonical form")
            outcome_id = normalized["outcome_id"]
            if outcome_id in history_by_id:
                raise ValueError("persisted outcome history contains duplicate identities")
            history_by_id[outcome_id] = normalized
        rates = tuple(float(value) for value in stored_state["adaptive_learning_rates"])
        gap = float(stored_state["cumulative_mixability_gap"])
        if not math.isfinite(gap) or gap < 0 or not all(math.isfinite(rate) and rate > 0 for rate in rates):
            raise ValueError("persisted online learning state is invalid")
        state_payload = dict(stored_state)
        state_hash = state_payload.pop("state_hash")
        if state_hash != canonical_hash(state_payload):
            raise ValueError("persisted online state hash is invalid")
    base_weights = {str(row["strategy_id"]): float(row["weight"]) for row in expected_base_rows}
    if set(base_weights) != set(weight_by_strategy) or any(
        not math.isfinite(value) or value < 0 for value in base_weights.values()
    ):
        raise ValueError("weight cohort base weights are invalid")
    if resolved_outcomes.empty:
        return ordered_weights
    if sum(weight.weight for weight in ordered_weights) <= 0:
        return ordered_weights
    required = {
        "strategy_id",
        "dataset_hash",
        "strategy_version",
        "symbol",
        "interval",
        "mode",
        "decision_timestamp",
        "outcome_available_at",
        "signal",
        "realized_return",
        "cost",
    }
    missing = required - set(resolved_outcomes.columns)
    if missing:
        raise ValueError(f"resolved outcomes are missing columns: {sorted(missing)}")
    outcomes = resolved_outcomes.copy()
    if outcomes[list(required)].isna().any().any():
        raise ValueError("resolved outcomes must be complete and finite")
    outcomes["decision_timestamp"] = _strict_utc_outcome_column(
        outcomes["decision_timestamp"], "decision_timestamp"
    )
    outcomes["outcome_available_at"] = _strict_utc_outcome_column(
        outcomes["outcome_available_at"], "outcome_available_at"
    )
    outcomes["signal"] = pd.to_numeric(outcomes["signal"], errors="coerce")
    outcomes["realized_return"] = pd.to_numeric(outcomes["realized_return"], errors="coerce")
    outcomes["cost"] = pd.to_numeric(outcomes["cost"], errors="coerce")
    numeric_columns = ("signal", "realized_return", "cost")
    if outcomes[["decision_timestamp", "outcome_available_at", *numeric_columns]].isna().any().any():
        raise ValueError("resolved outcomes must be complete and finite")
    if not all(math.isfinite(float(value)) for column in numeric_columns for value in outcomes[column]):
        raise ValueError("resolved outcomes must be complete and finite")
    if not outcomes["signal"].isin((-1, 0, 1)).all():
        raise ValueError("resolved outcome signals must be -1, 0, or 1")
    if (outcomes["cost"] < 0).any():
        raise ValueError("resolved outcome costs must be non-negative")
    if (outcomes["outcome_available_at"] < outcomes["decision_timestamp"]).any():
        raise ValueError("an outcome cannot resolve before its decision")

    outcomes["strategy_id"] = outcomes["strategy_id"].astype(str).str.strip()
    outcomes["dataset_hash"] = outcomes["dataset_hash"].astype(str).str.strip()
    outcomes["strategy_version"] = outcomes["strategy_version"].astype(str).str.strip()
    outcomes["symbol"] = outcomes["symbol"].astype(str).str.strip().str.upper()
    outcomes["interval"] = outcomes["interval"].map(
        lambda value: value.value if isinstance(value, BarInterval) else str(value).strip()
    )
    outcomes["mode"] = outcomes["mode"].map(
        lambda value: value.value if isinstance(value, StrategyMode) else str(value).strip()
    )
    if (outcomes[["strategy_id", "dataset_hash", "strategy_version", "symbol", "interval", "mode"]] == "").any().any():
        raise ValueError("resolved outcome context identifiers must not be empty")

    for row in outcomes.itertuples(index=False):
        weight = weight_by_strategy.get(str(row.strategy_id))
        if weight is None:
            continue
        supplied = (
            str(row.dataset_hash),
            str(row.strategy_version),
            str(row.symbol),
            str(row.interval),
            str(row.mode),
        )
        expected = (
            weight.dataset_hash,
            weight.strategy_version,
            weight.symbol.strip().upper(),
            weight.interval.value,
            weight.mode.value,
        )
        if supplied != expected:
            raise ValueError(f"resolved outcome context does not match evidence weight for {weight.strategy_id}")

    outcomes = outcomes.loc[
        outcomes["strategy_id"].isin(weight_by_strategy)
        & (outcomes["outcome_available_at"] <= pd.Timestamp(as_of))
    ].sort_values(["outcome_available_at", "decision_timestamp", "strategy_id"], kind="stable")
    if outcomes.empty:
        return ordered_weights

    for row in outcomes.itertuples(index=False):
        record = _outcome_record(row)
        outcome_id = record["outcome_id"]
        previous = history_by_id.get(outcome_id)
        if previous is not None and canonical_hash(previous) != canonical_hash(record):
            raise ValueError("resolved outcome identity was replayed with conflicting values")
        history_by_id[outcome_id] = record
    history = tuple(
        sorted(
            history_by_id.values(),
            key=lambda record: (
                record["outcome_available_at"],
                record["decision_timestamp"],
                record["strategy_id"],
                record["outcome_id"],
            ),
        )
    )
    history_frame = pd.DataFrame(history)
    history_frame["outcome_available_at"] = pd.to_datetime(history_frame["outcome_available_at"], utc=True)
    history_frame["decision_timestamp"] = pd.to_datetime(history_frame["decision_timestamp"], utc=True)

    current = base_weights.copy()
    prior_total = sum(weight.prior_weight for weight in ordered_weights)
    prior = {
        weight.strategy_id: (weight.prior_weight / prior_total if prior_total > 0 else weight.weight)
        for weight in ordered_weights
    }
    families = {weight.strategy_id: weight.family for weight in ordered_weights if base_weights[weight.strategy_id] > 0}
    outcomes_through: datetime | None = None
    cumulative_mixability_gap = 0.0
    adaptive_learning_rates: list[float] = []
    for effective_at, group in history_frame.groupby("outcome_available_at", sort=True):
        rewards = (
            (group["signal"] * group["realized_return"] - group["cost"])
            .groupby(group["strategy_id"])
            .mean()
            .clip(-1, 1)
            .to_dict()
        )
        active_ids = sorted(set(rewards) & set(families))
        active_mass = sum(current[strategy_id] for strategy_id in active_ids)
        if active_mass <= 0:
            outcomes_through = pd.Timestamp(effective_at).to_pydatetime()
            continue
        learning_rate = (
            config.learning_rate
            if cumulative_mixability_gap == 0
            else min(config.learning_rate, math.log(max(len(families), 2)) / cumulative_mixability_gap)
        )
        adaptive_learning_rates.append(float(learning_rate))
        conditional = {strategy_id: current[strategy_id] / active_mass for strategy_id in active_ids}
        losses = {strategy_id: (1 - float(rewards[strategy_id])) / 2 for strategy_id in active_ids}
        exponential = {
            strategy_id: conditional[strategy_id] * math.exp(-learning_rate * losses[strategy_id])
            for strategy_id in active_ids
        }
        normalizer = sum(exponential.values())
        if normalizer <= 0 or not math.isfinite(normalizer):
            raise ValueError("online update produced invalid weight mass")
        expected_loss = sum(conditional[strategy_id] * losses[strategy_id] for strategy_id in active_ids)
        mix_loss = -math.log(normalizer) / learning_rate
        cumulative_mixability_gap += max(expected_loss - mix_loss, 0.0)
        wealth = current.copy()
        for strategy_id in active_ids:
            wealth[strategy_id] = active_mass * exponential[strategy_id] / normalizer
        shared = {
            strategy_id: (1 - config.fixed_share) * wealth[strategy_id] + config.fixed_share * prior[strategy_id]
            for strategy_id in families
        }
        current = _project_caps(shared, families, config)
        outcomes_through = pd.Timestamp(effective_at).to_pydatetime()

    online_state = {
        "config_hash": config_hash,
        "cohort_hash": cohort_hash,
        "base_weights": expected_base_rows,
        "processed_outcome_ids": tuple(record["outcome_id"] for record in history),
        "processed_outcomes": history,
        "processed_outcomes_hash": canonical_hash(history),
        "adaptive_learning_rates": tuple(adaptive_learning_rates),
        "cumulative_mixability_gap": cumulative_mixability_gap,
    }
    online_state["state_hash"] = canonical_hash(online_state)
    updated: list[EvidenceWeight] = []
    for weight in ordered_weights:
        provenance = _deep_thaw(weight.provenance)
        provenance.update(
            {
                "online_method": "specialist_fixed_share_adaptive_hedge",
                "fixed_share": config.fixed_share,
                "learning_rate_ceiling": config.learning_rate,
                "outcomes_through": outcomes_through.isoformat() if outcomes_through else None,
                "online_state": online_state,
            }
        )
        updated.append(
            replace(
                weight,
                weight=float(current.get(weight.strategy_id, 0.0)),
                effective_at=outcomes_through or weight.effective_at,
                outcomes_through=outcomes_through,
                provenance=provenance,
            )
        )
    return tuple(updated)


def combine_current_signals(
    evaluations: Sequence[StrategyEvaluation],
    weights: Sequence[EvidenceWeight],
    *,
    as_of: datetime,
    config: EnsembleConfig = DEFAULT_ENSEMBLE_CONFIG,
) -> EnsembleDecision:
    _require_utc(as_of, "as_of")
    context = _evaluation_context(evaluations)
    weight_context = _weight_context(weights)
    if context != weight_context:
        raise ValueError("evaluation and weight context must match exactly")
    ordered_evaluations = tuple(sorted(evaluations, key=lambda item: (item.strategy_id, item.strategy_version)))
    ordered_weights = tuple(sorted(weights, key=lambda item: (item.strategy_id, item.strategy_version)))
    evaluation_identity = {
        evaluation.strategy_id: (evaluation.strategy_version, evaluation.family) for evaluation in ordered_evaluations
    }
    weight_identity = {weight.strategy_id: (weight.strategy_version, weight.family) for weight in ordered_weights}
    if evaluation_identity != weight_identity:
        raise ValueError("evaluation and weight strategy identity must match exactly")

    decision_times: dict[str, datetime | None] = {}
    data_times: dict[str, datetime | None] = {}
    for evaluation in ordered_evaluations:
        decision_time = pd.Timestamp(evaluation.decision_timestamp) if evaluation.decision_timestamp else None
        data_time = pd.Timestamp(evaluation.data_through) if evaluation.data_through else None
        if decision_time is not None and decision_time.tzinfo is None:
            raise ValueError("component decision timestamps must be timezone-aware")
        if data_time is not None and data_time.tzinfo is None:
            raise ValueError("component data timestamps must be timezone-aware")
        decision_time = decision_time.tz_convert("UTC") if decision_time is not None else None
        data_time = data_time.tz_convert("UTC") if data_time is not None else None
        if decision_time is not None and decision_time > pd.Timestamp(as_of):
            raise ValueError("future component decision cannot enter current inference")
        if data_time is not None and (
            data_time > pd.Timestamp(as_of) or (decision_time is not None and data_time > decision_time)
        ):
            raise ValueError("future component data cannot enter current inference")
        if evaluation.current_signal not in (-1, 0, 1):
            raise ValueError("component signals must be -1, 0, or 1")
        try:
            numeric_state = tuple(
                float(value)
                for value in (
                    evaluation.current_strength,
                    evaluation.current_probability,
                    evaluation.current_volatility,
                    evaluation.expected_edge,
                    evaluation.expected_cost,
                    evaluation.uncertainty,
                )
            )
        except (TypeError, ValueError) as error:
            raise ValueError("component decision state must be finite") from error
        if not all(math.isfinite(value) for value in numeric_state):
            raise ValueError("component decision state must be finite")
        if not 0 <= evaluation.current_strength <= 1 or not 0 <= evaluation.current_probability <= 1:
            raise ValueError("component strength and probability must be in [0, 1]")
        if evaluation.current_volatility <= 0:
            raise ValueError("component volatility must be positive")
        if min(evaluation.expected_edge, evaluation.expected_cost, evaluation.uncertainty) < 0:
            raise ValueError("component edge, cost, and uncertainty cannot be negative")
        decision_times[evaluation.strategy_id] = decision_time.to_pydatetime() if decision_time is not None else None
        data_times[evaluation.strategy_id] = data_time.to_pydatetime() if data_time is not None else None

    by_strategy = {evaluation.strategy_id: evaluation for evaluation in ordered_evaluations}
    contributions: list[ComponentContribution] = []
    active: list[tuple[StrategyEvaluation, EvidenceWeight, float]] = []
    for weight in ordered_weights:
        evaluation = by_strategy.get(weight.strategy_id)
        if evaluation is None or weight.weight <= 0 or evaluation.current_signal == 0:
            continue
        decision_timestamp = decision_times[evaluation.strategy_id]
        data_through = data_times[evaluation.strategy_id]
        if decision_timestamp is None or data_through is None:
            raise ValueError("actionable component timestamps require decision_timestamp and data_through")
        volatility = max(float(evaluation.current_volatility), 1e-12)
        normalized_vote = float(max(min(evaluation.current_signal * evaluation.current_strength / volatility, 1), -1))
        contribution = weight.weight * normalized_vote
        contributions.append(
            ComponentContribution(
                strategy_id=weight.strategy_id,
                strategy_version=weight.strategy_version,
                weight=weight.weight,
                signal=evaluation.current_signal,
                normalized_vote=normalized_vote,
                contribution=contribution,
                decision_timestamp=decision_timestamp,
                data_through=data_through,
                expected_edge=float(evaluation.expected_edge),
                expected_cost=float(evaluation.expected_cost),
                uncertainty=float(evaluation.uncertainty),
            )
        )
        active.append((evaluation, weight, contribution))

    breadth = len(active)
    active_mass = sum(weight.weight for _, weight, _ in active)
    vote = sum(contribution for _, _, contribution in active)
    vote_margin = abs(vote) / active_mass if active_mass else 0.0
    direction = 1 if vote > 0 else -1 if vote < 0 else 0
    probability = (
        sum(
            weight.weight
            * (evaluation.current_probability if direction >= 0 else 1 - evaluation.current_probability)
            for evaluation, weight, _ in active
        )
        / active_mass
        if active_mass
        else 0.5
    )
    signed_edge = sum(
        weight.weight * evaluation.current_signal * max(float(evaluation.expected_edge), 0.0)
        for evaluation, weight, _ in active
    )
    gross_edge = direction * signed_edge / active_mass if direction and active_mass else 0.0
    estimated_cost = (
        sum(weight.weight * max(float(evaluation.expected_cost), 0.0) for evaluation, weight, _ in active)
        / active_mass
        if active_mass
        else 0.0
    )
    uncertainty = (
        sum(weight.weight * max(float(evaluation.uncertainty), 0.0) for evaluation, weight, _ in active)
        / active_mass
        * config.cost_buffer_multiplier
        if active_mass
        else 0.0
    )
    net_edge = gross_edge - estimated_cost - uncertainty
    reasons: list[str] = []
    if breadth < config.minimum_breadth:
        reasons.append("minimum_breadth")
    if direction == 0 or vote_margin < config.minimum_vote_margin:
        reasons.append("vote_margin")
    if probability < config.minimum_probability:
        reasons.append("probability_calibration")
    if net_edge <= 0:
        reasons.append("cost_buffer")
    signal = 0 if reasons else direction
    status = "abstain" if signal == 0 else "long" if signal > 0 else "short"
    active_data_times = [data_times[evaluation.strategy_id] for evaluation, _, _ in active]
    data_through = max(active_data_times) if active_data_times else None
    component_states = tuple(
        {
            "strategy_id": evaluation.strategy_id,
            "strategy_version": evaluation.strategy_version,
            "family": evaluation.family,
            "status": evaluation.status,
            "promotion": evaluation.promotion.promoted,
            "current_signal": evaluation.current_signal,
            "current_strength": evaluation.current_strength,
            "current_probability": evaluation.current_probability,
            "current_volatility": evaluation.current_volatility,
            "expected_edge": evaluation.expected_edge,
            "expected_cost": evaluation.expected_cost,
            "uncertainty": evaluation.uncertainty,
            "decision_timestamp": decision_times[evaluation.strategy_id],
            "data_through": data_times[evaluation.strategy_id],
        }
        for evaluation in ordered_evaluations
    )
    return EnsembleDecision(
        as_of=as_of,
        signal=signal,
        status=status,
        reasons=tuple(reasons),
        probability=float(probability),
        vote_margin=float(vote_margin),
        expected_net_edge=float(net_edge),
        estimated_cost=float(estimated_cost),
        uncertainty_buffer=float(uncertainty),
        breadth=breadth,
        data_through=data_through,
        dataset_hash=context.dataset_hash,
        symbol=context.symbol,
        interval=context.interval,
        mode=context.mode,
        component_versions=tuple(
            (evaluation.strategy_id, evaluation.strategy_version) for evaluation in ordered_evaluations
        ),
        component_states=component_states,
        weights=ordered_weights,
        contributions=tuple(contributions),
        config=config,
    )


def persist_evidence_weights(database: Database, weights: Sequence[EvidenceWeight]) -> int:
    rows: list[dict[str, Any]] = []
    for weight in weights:
        provenance = _deep_thaw(weight.provenance)
        provenance["outcomes_through"] = weight.outcomes_through.isoformat() if weight.outcomes_through else None
        natural = {
            "dataset_hash": weight.dataset_hash,
            "strategy_id": weight.strategy_id,
            "strategy_version": weight.strategy_version,
            "symbol": weight.symbol,
            "interval": weight.interval.value,
            "mode": weight.mode.value,
            "effective_at": weight.effective_at,
        }
        rows.append(
            {
                "weight_id": canonical_hash(natural),
                "strategy_run_id": canonical_hash({**natural, "kind": "ensemble"}),
                **natural,
                "family": weight.family.value,
                "weight": weight.weight,
                "evidence": provenance,
                "source": "strategy_ensemble",
                "source_version": "1",
                "created_at": weight.effective_at,
            }
        )
    return database.upsert("ensemble_weights", rows)


__all__ = [
    "ComponentContribution",
    "DEFAULT_ENSEMBLE_CONFIG",
    "EnsembleConfig",
    "EnsembleDecision",
    "EvidenceWeight",
    "canonical_decision_hash",
    "combine_current_signals",
    "compute_evidence_weights",
    "fixed_share_update",
    "persist_evidence_weights",
]
