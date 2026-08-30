"""Authenticated soft-regime outcome attribution and idempotent online replay."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

import pandas as pd

from src.contextual.types import MarketRegime
from src.strategies.types import canonical_hash


@dataclass(frozen=True, slots=True)
class SoftRegimeOutcome:
    attribution_id: str
    outcome_id: str
    content_hash: str
    context_hash: str
    source_decision_hash: str
    strategy_id: str
    regime: MarketRegime
    credit: float
    net_return: float
    decision_timestamp: datetime
    outcome_available_at: datetime

    @property
    def cell_id(self) -> str:
        return canonical_hash({"context_hash": self.context_hash, "regime": self.regime})


@dataclass(frozen=True, slots=True)
class ContextualOnlineState:
    status: Literal["updated", "all_cash"]
    parent_weights: Mapping[str, float]
    cell_weights: Mapping[str, Mapping[str, float]]
    effective_observations: Mapping[str, float]
    processed_outcome_ids: tuple[str, ...]
    outcome_watermark: datetime | None
    adaptive_learning_rates: tuple[float, ...]
    parent_hash: str
    covariance_hash: str | None
    state_hash: str


def _utc(value: object, label: str) -> datetime:
    timestamp = pd.Timestamp(value).to_pydatetime()
    if timestamp.tzinfo is not UTC:
        raise ValueError(f"{label} must be an explicit UTC datetime")
    return timestamp


def _normalized_posterior(values: Mapping[MarketRegime | str, float]) -> dict[MarketRegime, float]:
    probabilities = {MarketRegime(key): float(value) for key, value in values.items()}
    if set(probabilities) != set(MarketRegime):
        raise ValueError("soft-regime posterior must contain the fixed four-state taxonomy")
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in probabilities.values()) or not math.isclose(
        sum(probabilities.values()), 1.0, rel_tol=0, abs_tol=1e-9
    ):
        raise ValueError("soft-regime posterior must be normalized")
    return probabilities


def attribute_soft_regime_outcome(
    outcome: Mapping[str, object],
    posterior: Mapping[MarketRegime | str, float] | None = None,
) -> tuple[SoftRegimeOutcome, ...]:
    """Split one authenticated outcome across the probabilities stored at its decision."""

    required = {
        "outcome_id",
        "content_hash",
        "context_hash",
        "source_decision_hash",
        "strategy_id",
        "decision_timestamp",
        "outcome_available_at",
        "net_return",
    }
    missing = sorted(required - set(outcome))
    if missing:
        raise ValueError(f"contextual online outcome missing fields: {', '.join(missing)}")
    if isinstance(outcome.get("evidence"), Mapping) and canonical_hash(outcome["evidence"]) != str(
        outcome["content_hash"]
    ):
        raise ValueError("contextual online outcome content authentication failed")
    text = {
        key: str(outcome[key]).strip()
        for key in required
        if key not in {"decision_timestamp", "outcome_available_at", "net_return"}
    }
    if any(not value for value in text.values()):
        raise ValueError("contextual online outcome identity cannot be blank")
    decision = _utc(outcome["decision_timestamp"], "decision_timestamp")
    available = _utc(outcome["outcome_available_at"], "outcome_available_at")
    if available < decision:
        raise ValueError("contextual online outcome cannot be available before its decision")
    net_return = float(outcome["net_return"])
    if not math.isfinite(net_return):
        raise ValueError("contextual online net return must be finite")
    probabilities = _normalized_posterior(posterior or dict(outcome.get("regime_probabilities") or {}))
    records = []
    for regime in MarketRegime:
        credit = probabilities[regime]
        identity = {
            "outcome_id": text["outcome_id"],
            "content_hash": text["content_hash"],
            "context_hash": text["context_hash"],
            "strategy_id": text["strategy_id"],
            "regime": regime,
            "credit": credit,
        }
        records.append(
            SoftRegimeOutcome(
                attribution_id=canonical_hash(identity),
                outcome_id=text["outcome_id"],
                content_hash=text["content_hash"],
                context_hash=text["context_hash"],
                source_decision_hash=text["source_decision_hash"],
                strategy_id=text["strategy_id"],
                regime=regime,
                credit=credit,
                net_return=net_return,
                decision_timestamp=decision,
                outcome_available_at=available,
            )
        )
    return tuple(records)


def _validated_parent(values: Mapping[str, float]) -> dict[str, float]:
    parent = {str(key).strip(): float(value) for key, value in values.items()}
    if not parent or any(not key for key in parent):
        raise ValueError("online replay requires nonblank parent strategies")
    if any(not math.isfinite(value) or value < 0 for value in parent.values()):
        raise ValueError("online parent weights must be finite and nonnegative")
    total = sum(parent.values())
    if total <= 0 or total > 1.0 + 1e-12:
        raise ValueError("online parent weight mass must be in (0, 1]")
    return {key: value / total for key, value in sorted(parent.items())}


def _capped_weights(values: Mapping[str, float], maximum_weight: float) -> dict[str, float] | None:
    if maximum_weight <= 0 or maximum_weight > 1:
        raise ValueError("online maximum strategy weight must be in (0, 1]")
    if maximum_weight * len(values) < 1.0 - 1e-12:
        return None
    remaining = 1.0
    unresolved = set(values)
    result = {key: 0.0 for key in values}
    source = dict(values)
    while unresolved:
        mass = sum(source[key] for key in unresolved)
        if mass <= 0:
            unit = remaining / len(unresolved)
            if unit > maximum_weight + 1e-12:
                return None
            for key in unresolved:
                result[key] = unit
            break
        capped = {key for key in unresolved if remaining * source[key] / mass > maximum_weight + 1e-12}
        if not capped:
            for key in unresolved:
                result[key] = remaining * source[key] / mass
            break
        for key in capped:
            result[key] = maximum_weight
            remaining -= maximum_weight
            unresolved.remove(key)
    return {key: float(f"{max(value, 0.0):.15g}") for key, value in sorted(result.items())}


def replay_contextual_outcomes(
    base: ContextualOnlineState | Mapping[str, float],
    attributed: Sequence[SoftRegimeOutcome],
    *,
    fixed_share: float = 0.05,
    learning_rate_ceiling: float = 4.0,
    parent_strength: float = 50.0,
    maximum_strategy_weight: float = 0.80,
    covariance_status: Literal["estimated", "insufficient", "invalid"] = "estimated",
    covariance_hash: str | None = None,
) -> ContextualOnlineState:
    """Replay each outcome once, shrink cell wealth to its frozen parent, and cap it."""

    if not 0 <= fixed_share < 1 or learning_rate_ceiling <= 0 or parent_strength <= 0:
        raise ValueError("online replay controls are outside safe bounds")
    if isinstance(base, ContextualOnlineState):
        parent = _validated_parent(base.parent_weights)
        cell_weights = {cell: dict(weights) for cell, weights in base.cell_weights.items()}
        observations = dict(base.effective_observations)
        processed = set(base.processed_outcome_ids)
        rates = list(base.adaptive_learning_rates)
        watermark = base.outcome_watermark
    else:
        parent = _validated_parent(base)
        cell_weights = {}
        observations = {}
        processed = set()
        rates = []
        watermark = None
    parent_hash = canonical_hash(parent)

    unique: dict[str, SoftRegimeOutcome] = {}
    for item in attributed:
        previous = unique.get(item.attribution_id)
        if previous is not None and previous != item:
            raise ValueError("contextual attribution identity collision")
        unique[item.attribution_id] = item
    by_outcome: dict[str, list[SoftRegimeOutcome]] = {}
    for item in unique.values():
        by_outcome.setdefault(item.outcome_id, []).append(item)
    ordered = sorted(
        by_outcome.items(),
        key=lambda item: (
            item[1][0].outcome_available_at,
            item[1][0].decision_timestamp,
            item[0],
        ),
    )
    for outcome_id, records in ordered:
        if outcome_id in processed:
            continue
        if {item.regime for item in records} != set(MarketRegime) or not math.isclose(
            sum(item.credit for item in records), 1.0, rel_tol=0, abs_tol=1e-9
        ):
            raise ValueError("each online outcome requires conserved four-regime credit")
        identity = {
            (item.content_hash, item.context_hash, item.source_decision_hash, item.strategy_id, item.net_return)
            for item in records
        }
        if len(identity) != 1:
            raise ValueError("soft-regime records disagree about their source outcome")
        strategy_id = records[0].strategy_id
        if strategy_id not in parent:
            raise ValueError("online outcome strategy is absent from its parent allocation")
        eta = min(
            learning_rate_ceiling,
            math.log(max(len(parent), 2)) / math.sqrt(max(len(processed) + 1, 1)),
        )
        rates.append(float(eta))
        for item in records:
            cell = item.cell_id
            current = cell_weights.get(cell, dict(parent))
            loss = {key: 0.5 if key != strategy_id else min(max(0.5 - item.net_return, 0.0), 1.0) for key in parent}
            wealth = {key: current[key] * math.exp(-eta * item.credit * loss[key]) for key in parent}
            normalizer = sum(wealth.values())
            if not math.isfinite(normalizer) or normalizer <= 0:
                raise ValueError("contextual online update produced invalid mass")
            normalized = {key: value / normalizer for key, value in wealth.items()}
            shared = {key: (1.0 - fixed_share) * normalized[key] + fixed_share * parent[key] for key in parent}
            effective = observations.get(cell, 0.0) + item.credit
            alpha = effective / (effective + parent_strength)
            shrunk = {key: alpha * shared[key] + (1.0 - alpha) * parent[key] for key in parent}
            capped = _capped_weights(shrunk, maximum_strategy_weight)
            cell_weights[cell] = capped or {key: 0.0 for key in parent}
            observations[cell] = effective
        processed.add(outcome_id)
        watermark = (
            max(watermark, records[0].outcome_available_at)
            if watermark is not None
            else records[0].outcome_available_at
        )

    status: Literal["updated", "all_cash"] = "updated"
    if covariance_status != "estimated" or any(sum(weights.values()) <= 0 for weights in cell_weights.values()):
        status = "all_cash"
        cell_weights = {cell: {key: 0.0 for key in parent} for cell in cell_weights}
    frozen_cells = {
        cell: MappingProxyType(dict(sorted(weights.items()))) for cell, weights in sorted(cell_weights.items())
    }
    payload = {
        "status": status,
        "parent_hash": parent_hash,
        "parent_weights": parent,
        "cell_weights": {key: dict(value) for key, value in frozen_cells.items()},
        "effective_observations": dict(sorted(observations.items())),
        "processed_outcome_ids": tuple(sorted(processed)),
        "outcome_watermark": watermark,
        "adaptive_learning_rates": tuple(rates),
        "covariance_hash": covariance_hash,
        "covariance_status": covariance_status,
    }
    return ContextualOnlineState(
        status=status,
        parent_weights=MappingProxyType(parent),
        cell_weights=MappingProxyType(frozen_cells),
        effective_observations=MappingProxyType(dict(sorted(observations.items()))),
        processed_outcome_ids=tuple(sorted(processed)),
        outcome_watermark=watermark,
        adaptive_learning_rates=tuple(rates),
        parent_hash=parent_hash,
        covariance_hash=covariance_hash,
        state_hash=canonical_hash(payload),
    )


__all__ = [
    "ContextualOnlineState",
    "SoftRegimeOutcome",
    "attribute_soft_regime_outcome",
    "replay_contextual_outcomes",
]
