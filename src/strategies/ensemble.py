from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

import pandas as pd

from src.backtest.robustness import deflated_sharpe_probability
from src.database.engine import Database
from src.strategies.types import BarInterval, StrategyFamily, StrategyMode, canonical_hash
from src.strategies.validation import EvaluationStatus, StrategyEvaluation


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


@dataclass(frozen=True, slots=True)
class ComponentContribution:
    strategy_id: str
    weight: float
    signal: int
    normalized_vote: float
    contribution: float


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
    weights: tuple[EvidenceWeight, ...]
    contributions: tuple[ComponentContribution, ...]
    decision_hash: str


def _require_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is not UTC:
        raise ValueError(f"{name} must be an explicit UTC datetime")
    return value


def _evidence_score(evaluation: StrategyEvaluation, config: EnsembleConfig) -> tuple[float, dict[str, Any]]:
    eligible = (
        evaluation.status is EvaluationStatus.EVALUATED
        and evaluation.promotion.promoted
        and evaluation.causal_audit_passed
        and evaluation.cost_survives is True
        and evaluation.development_sharpe is not None
        and len(evaluation.trial_sharpes) >= 2
        and evaluation.observations >= 3
    )
    if not eligible:
        return 0.0, {"eligible": False, "reason": "promotion, causal, cost, or observed-trial evidence gate failed"}
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
        "trial_sharpes": list(evaluation.trial_sharpes),
        "trial_count": len(evaluation.trial_sharpes),
        "deflated_sharpe_probability": dsr,
        "multiple_testing_source": "observed_trial_sharpes",
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


def compute_evidence_weights(
    evaluations: Sequence[StrategyEvaluation],
    *,
    as_of: datetime,
    config: EnsembleConfig = DEFAULT_ENSEMBLE_CONFIG,
) -> tuple[EvidenceWeight, ...]:
    _require_utc(as_of, "as_of")
    if len({evaluation.strategy_id for evaluation in evaluations}) != len(evaluations):
        raise ValueError("strategy evaluations must have unique identifiers")
    scored = [_evidence_score(evaluation, config) for evaluation in evaluations]
    eligible = [index for index, (score, _) in enumerate(scored) if score > 0]
    desired: dict[str, float] = {}
    if eligible:
        score_total = sum(scored[index][0] for index in eligible)
        prior = 1 / len(eligible)
        for index in eligible:
            evaluation = evaluations[index]
            evidence_share = scored[index][0] / score_total
            desired[evaluation.strategy_id] = (
                (1 - config.equal_weight_shrinkage) * evidence_share + config.equal_weight_shrinkage * prior
            )
        projected = _project_caps(
            desired,
            {evaluations[index].strategy_id: evaluations[index].family for index in eligible},
            config,
        )
    else:
        prior = 0.0
        projected = {}

    weights: list[EvidenceWeight] = []
    for evaluation, (score, provenance) in zip(evaluations, scored, strict=True):
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
    return tuple(weights)


def fixed_share_update(
    weights: Sequence[EvidenceWeight],
    resolved_outcomes: pd.DataFrame,
    *,
    as_of: datetime,
    config: EnsembleConfig = DEFAULT_ENSEMBLE_CONFIG,
) -> tuple[EvidenceWeight, ...]:
    _require_utc(as_of, "as_of")
    if not weights or sum(weight.weight for weight in weights) <= 0:
        return tuple(weights)
    if len({weight.strategy_id for weight in weights}) != len(weights):
        raise ValueError("evidence weights must have unique strategy identifiers")
    required = {"strategy_id", "decision_timestamp", "outcome_available_at", "signal", "realized_return", "cost"}
    missing = required - set(resolved_outcomes.columns)
    if missing:
        if resolved_outcomes.empty:
            return tuple(weights)
        raise ValueError(f"resolved outcomes are missing columns: {sorted(missing)}")
    outcomes = resolved_outcomes.copy()
    outcomes["decision_timestamp"] = pd.to_datetime(outcomes["decision_timestamp"], utc=True, errors="coerce")
    outcomes["outcome_available_at"] = pd.to_datetime(outcomes["outcome_available_at"], utc=True, errors="coerce")
    outcomes["signal"] = pd.to_numeric(outcomes["signal"], errors="coerce")
    outcomes["realized_return"] = pd.to_numeric(outcomes["realized_return"], errors="coerce")
    outcomes["cost"] = pd.to_numeric(outcomes["cost"], errors="coerce")
    if outcomes[list(required - {"strategy_id"})].isna().any().any():
        raise ValueError("resolved outcomes must be complete and finite")
    if (outcomes["outcome_available_at"] < outcomes["decision_timestamp"]).any():
        raise ValueError("an outcome cannot resolve before its decision")
    known = {weight.strategy_id for weight in weights}
    previous_updates = [weight.outcomes_through for weight in weights if weight.outcomes_through is not None]
    update_cutoff = max(previous_updates) if previous_updates else datetime.min.replace(tzinfo=UTC)
    outcomes = outcomes.loc[
        outcomes["strategy_id"].isin(known)
        & (outcomes["outcome_available_at"] <= pd.Timestamp(as_of))
        & (outcomes["outcome_available_at"] > pd.Timestamp(update_cutoff))
    ].sort_values(["outcome_available_at", "decision_timestamp", "strategy_id"], kind="stable")
    if outcomes.empty:
        return tuple(weights)

    current = {weight.strategy_id: weight.weight for weight in weights}
    prior_total = sum(weight.prior_weight for weight in weights)
    prior = {
        weight.strategy_id: (weight.prior_weight / prior_total if prior_total > 0 else weight.weight)
        for weight in weights
    }
    families = {weight.strategy_id: weight.family for weight in weights if weight.weight > 0}
    outcomes_through: datetime | None = None
    cumulative_mixability_gap = 0.0
    adaptive_learning_rates: list[float] = []
    for effective_at, group in outcomes.groupby("outcome_available_at", sort=True):
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

    updated: list[EvidenceWeight] = []
    for weight in weights:
        provenance = dict(weight.provenance)
        provenance.update(
            {
                "online_method": "specialist_fixed_share_adaptive_hedge",
                "fixed_share": config.fixed_share,
                "learning_rate_ceiling": config.learning_rate,
                "adaptive_learning_rates": adaptive_learning_rates,
                "cumulative_mixability_gap": cumulative_mixability_gap,
                "outcomes_through": outcomes_through.isoformat() if outcomes_through else None,
            }
        )
        updated.append(
            replace(
                weight,
                weight=float(current.get(weight.strategy_id, 0.0)),
                effective_at=outcomes_through or weight.effective_at,
                outcomes_through=outcomes_through,
                provenance=MappingProxyType(provenance),
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
    for evaluation in evaluations:
        decision_time = pd.Timestamp(evaluation.decision_timestamp) if evaluation.decision_timestamp else None
        data_time = pd.Timestamp(evaluation.data_through) if evaluation.data_through else None
        if decision_time is not None and decision_time > pd.Timestamp(as_of):
            raise ValueError("future component decision cannot enter current inference")
        if data_time is not None and (
            data_time > pd.Timestamp(as_of) or (decision_time is not None and data_time > decision_time)
        ):
            raise ValueError("future component data cannot enter current inference")
        if evaluation.current_signal not in (-1, 0, 1):
            raise ValueError("component signals must be -1, 0, or 1")
        numeric_state = (
            evaluation.current_strength,
            evaluation.current_probability,
            evaluation.current_volatility,
            evaluation.expected_edge,
            evaluation.expected_cost,
            evaluation.uncertainty,
        )
        if not all(math.isfinite(float(value)) for value in numeric_state):
            raise ValueError("component decision state must be finite")
        if not 0 <= evaluation.current_strength <= 1 or not 0 <= evaluation.current_probability <= 1:
            raise ValueError("component strength and probability must be in [0, 1]")
        if evaluation.current_volatility <= 0:
            raise ValueError("component volatility must be positive")
        if min(evaluation.expected_edge, evaluation.expected_cost, evaluation.uncertainty) < 0:
            raise ValueError("component edge, cost, and uncertainty cannot be negative")
    by_strategy = {evaluation.strategy_id: evaluation for evaluation in evaluations}
    contributions: list[ComponentContribution] = []
    active: list[tuple[StrategyEvaluation, EvidenceWeight, float]] = []
    for weight in weights:
        evaluation = by_strategy.get(weight.strategy_id)
        if evaluation is None or weight.weight <= 0 or evaluation.current_signal == 0:
            continue
        volatility = max(float(evaluation.current_volatility), 1e-12)
        normalized_vote = float(max(min(evaluation.current_signal * evaluation.current_strength / volatility, 1), -1))
        contribution = weight.weight * normalized_vote
        contributions.append(
            ComponentContribution(
                weight.strategy_id,
                weight.weight,
                evaluation.current_signal,
                normalized_vote,
                contribution,
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
    data_times = [evaluation.data_through for evaluation, _, _ in active if evaluation.data_through is not None]
    data_through = max(data_times) if data_times else None
    hash_payload = {
        "as_of": as_of,
        "signal": signal,
        "status": status,
        "reasons": reasons,
        "probability": probability,
        "vote_margin": vote_margin,
        "expected_net_edge": net_edge,
        "breadth": breadth,
        "components": [
            {
                "strategy_id": contribution.strategy_id,
                "weight": contribution.weight,
                "signal": contribution.signal,
                "normalized_vote": contribution.normalized_vote,
            }
            for contribution in contributions
        ],
    }
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
        weights=tuple(weights),
        contributions=tuple(contributions),
        decision_hash=canonical_hash(hash_payload),
    )


def persist_evidence_weights(database: Database, weights: Sequence[EvidenceWeight]) -> int:
    rows: list[dict[str, Any]] = []
    for weight in weights:
        provenance = dict(weight.provenance)
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
    "combine_current_signals",
    "compute_evidence_weights",
    "fixed_share_update",
    "persist_evidence_weights",
]
