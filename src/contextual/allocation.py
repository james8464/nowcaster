"""Shrinkage covariance estimation and constrained contextual strategy weights."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal, Protocol

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

from src.config.settings import AllocationPolicyConfig
from src.strategies.types import StrategyFamily, canonical_hash


class ConservativeEstimate(Protocol):
    strategy_id: str
    lower_net_edge: float


@dataclass(frozen=True, slots=True)
class CovarianceEvidence:
    status: Literal["estimated", "insufficient", "invalid"]
    strategy_ids: tuple[str, ...]
    observations: int
    matrix: tuple[tuple[float, ...], ...]
    shrinkage: float | None
    aligned_through: datetime | None
    alignment_hash: str
    evidence_hash: str

    def as_array(self) -> np.ndarray:
        return np.asarray(self.matrix, dtype=float)

    def correlation(self, left: str, right: str) -> float:
        if self.status != "estimated":
            return math.nan
        left_index = self.strategy_ids.index(left)
        right_index = self.strategy_ids.index(right)
        matrix = self.as_array()
        denominator = math.sqrt(matrix[left_index, left_index] * matrix[right_index, right_index])
        return float(matrix[left_index, right_index] / denominator) if denominator > 0 else math.nan


@dataclass(frozen=True, slots=True)
class ContextualWeight:
    strategy_id: str
    family: StrategyFamily
    weight: float
    lower_net_edge: float


@dataclass(frozen=True, slots=True)
class ContextualAllocation:
    allocation_id: str
    status: Literal["allocated", "all_cash"]
    reasons: tuple[str, ...]
    weights: Mapping[str, float]
    weight_evidence: tuple[ContextualWeight, ...]
    cash_weight: float
    effective_strategy_count: float
    covariance: CovarianceEvidence
    objective_value: float | None
    as_of: datetime


AllocationPolicy = AllocationPolicyConfig


def _require_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is not UTC:
        raise ValueError(f"{label} must be an explicit UTC datetime")
    return value


def _canonical_float(value: float) -> float:
    if abs(value) < 1e-15:
        return 0.0
    return float(f"{value:.15g}")


def estimate_strategy_covariance(
    returns: pd.DataFrame,
    as_of: datetime,
    *,
    minimum_overlap: int,
) -> CovarianceEvidence:
    """Estimate PSD covariance from synchronized, causally available OOF returns."""

    _require_utc(as_of, "as_of")
    if minimum_overlap < 2:
        raise ValueError("minimum_overlap must be at least two")
    if returns.columns.duplicated().any() or any(not str(column).strip() for column in returns.columns):
        raise ValueError("strategy return columns must be unique and nonblank")
    if not isinstance(returns.index, pd.DatetimeIndex):
        raise ValueError("strategy returns require a UTC DatetimeIndex")
    if returns.index.tz is None or str(returns.index.tz) != "UTC":
        raise ValueError("strategy return timestamps must be explicit UTC")
    if returns.index.duplicated().any():
        raise ValueError("strategy return timestamps must be unique")
    if (returns.index > pd.Timestamp(as_of)).any():
        raise ValueError("strategy covariance cannot include returns after as_of")

    strategy_ids = tuple(sorted(str(column) for column in returns.columns))
    aligned = returns.loc[:, strategy_ids].sort_index(kind="stable").apply(pd.to_numeric, errors="coerce")
    if np.isinf(aligned.to_numpy(dtype=float)).any():
        raise ValueError("strategy returns cannot contain infinite values")
    aligned = aligned.dropna(axis=0, how="any")
    aligned_through = aligned.index.max().to_pydatetime() if not aligned.empty else None
    if aligned_through is not None:
        _require_utc(aligned_through, "aligned_through")
    alignment_payload = {
        "strategy_ids": strategy_ids,
        "timestamps": tuple(timestamp.isoformat() for timestamp in aligned.index),
        "values": tuple(tuple(float(value) for value in row) for row in aligned.to_numpy(dtype=float)),
    }
    alignment_hash = canonical_hash(alignment_payload)
    if len(strategy_ids) < 2 or len(aligned) < minimum_overlap:
        payload = {
            "status": "insufficient",
            "minimum_overlap": minimum_overlap,
            "observations": len(aligned),
            "alignment_hash": alignment_hash,
        }
        return CovarianceEvidence(
            status="insufficient",
            strategy_ids=strategy_ids,
            observations=len(aligned),
            matrix=(),
            shrinkage=None,
            aligned_through=aligned_through,
            alignment_hash=alignment_hash,
            evidence_hash=canonical_hash(payload),
        )

    estimator = LedoitWolf(assume_centered=False).fit(aligned.to_numpy(dtype=float))
    matrix = np.asarray(estimator.covariance_, dtype=float)
    matrix = (matrix + matrix.T) / 2.0
    if not np.isfinite(matrix).all():
        status: Literal["estimated", "insufficient", "invalid"] = "invalid"
        canonical_matrix: tuple[tuple[float, ...], ...] = ()
    else:
        eigenvalues, eigenvectors = np.linalg.eigh(matrix)
        tolerance = max(float(np.max(np.abs(eigenvalues))), 1.0) * 1e-10
        if float(eigenvalues.min()) < -tolerance:
            status = "invalid"
            canonical_matrix = ()
        else:
            clipped = np.maximum(eigenvalues, 0.0)
            matrix = (eigenvectors * clipped) @ eigenvectors.T
            matrix = (matrix + matrix.T) / 2.0
            status = "estimated"
            canonical_matrix = tuple(tuple(_canonical_float(float(value)) for value in row) for row in matrix)
    shrinkage = float(estimator.shrinkage_)
    payload = {
        "status": status,
        "strategy_ids": strategy_ids,
        "observations": len(aligned),
        "matrix": canonical_matrix,
        "shrinkage": shrinkage,
        "aligned_through": aligned_through,
        "alignment_hash": alignment_hash,
    }
    return CovarianceEvidence(
        status=status,
        strategy_ids=strategy_ids,
        observations=len(aligned),
        matrix=canonical_matrix,
        shrinkage=shrinkage,
        aligned_through=aligned_through,
        alignment_hash=alignment_hash,
        evidence_hash=canonical_hash(payload),
    )


def _effective_count(weights: np.ndarray) -> float:
    total = float(weights.sum())
    squared = float(np.square(weights).sum())
    return total**2 / squared if total > 0 and squared > 0 else 0.0


def _all_cash(
    strategy_ids: tuple[str, ...],
    estimates: Mapping[str, ConservativeEstimate],
    families: Mapping[str, StrategyFamily],
    covariance: CovarianceEvidence,
    as_of: datetime,
    *reasons: str,
) -> ContextualAllocation:
    weights = MappingProxyType({strategy_id: 0.0 for strategy_id in strategy_ids})
    weight_evidence = tuple(
        ContextualWeight(
            strategy_id=strategy_id,
            family=families[strategy_id],
            weight=0.0,
            lower_net_edge=float(estimates[strategy_id].lower_net_edge),
        )
        for strategy_id in strategy_ids
    )
    identity = {
        "status": "all_cash",
        "reasons": tuple(dict.fromkeys(reasons)),
        "weights": dict(weights),
        "covariance_hash": covariance.evidence_hash,
        "as_of": as_of,
    }
    return ContextualAllocation(
        allocation_id=canonical_hash(identity),
        status="all_cash",
        reasons=tuple(dict.fromkeys(reasons)),
        weights=weights,
        weight_evidence=weight_evidence,
        cash_weight=1.0,
        effective_strategy_count=0.0,
        covariance=covariance,
        objective_value=None,
        as_of=as_of,
    )


def _validated_weight_vector(
    values: Mapping[str, float],
    strategy_ids: tuple[str, ...],
    label: str,
) -> np.ndarray:
    unknown = set(values) - set(strategy_ids)
    if unknown:
        raise ValueError(f"{label} contains unknown strategies: {', '.join(sorted(unknown))}")
    vector = np.array([float(values.get(strategy_id, 0.0)) for strategy_id in strategy_ids], dtype=float)
    if not np.isfinite(vector).all() or (vector < 0).any() or float(vector.sum()) > 1.0 + 1e-12:
        raise ValueError(f"{label} must contain finite nonnegative mass totaling at most one")
    return vector


def allocate_contextual_weights(
    estimates: Mapping[str, ConservativeEstimate],
    synchronized_returns: pd.DataFrame,
    hierarchical_prior: Mapping[str, float],
    previous_weights: Mapping[str, float],
    families: Mapping[str, StrategyFamily | str],
    policy: AllocationPolicyConfig,
    as_of: datetime,
    *,
    applicable: Mapping[str, bool] | None = None,
) -> ContextualAllocation:
    """Solve deterministic long-only strategy influence with an explicit cash option."""

    _require_utc(as_of, "as_of")
    if not estimates:
        raise ValueError("contextual allocation requires at least one strategy estimate")
    strategy_ids = tuple(sorted(estimates))
    if set(families) != set(strategy_ids):
        raise ValueError("every strategy requires exactly one family")
    normalized_families = {key: StrategyFamily(value) for key, value in families.items()}
    missing_returns = set(strategy_ids) - set(synchronized_returns.columns)
    if missing_returns:
        raise ValueError(f"missing synchronized strategy returns: {', '.join(sorted(missing_returns))}")
    for strategy_id, estimate in estimates.items():
        if estimate.strategy_id != strategy_id:
            raise ValueError("estimate key must match strategy_id")
        if not math.isfinite(float(estimate.lower_net_edge)):
            raise ValueError("lower net edges must be finite")

    covariance = estimate_strategy_covariance(
        synchronized_returns.loc[:, strategy_ids],
        as_of,
        minimum_overlap=policy.minimum_covariance_overlap,
    )
    eligible = {
        strategy_id: float(estimates[strategy_id].lower_net_edge) > 0
        and (applicable is None or applicable.get(strategy_id, False))
        for strategy_id in strategy_ids
    }
    if applicable is not None and set(applicable) != set(strategy_ids):
        raise ValueError("applicability must cover every strategy exactly")
    if covariance.status != "estimated":
        return _all_cash(
            strategy_ids,
            estimates,
            normalized_families,
            covariance,
            as_of,
            "covariance_evidence_required",
        )
    if sum(eligible.values()) < policy.minimum_effective_strategies:
        return _all_cash(
            strategy_ids,
            estimates,
            normalized_families,
            covariance,
            as_of,
            "minimum_positive_edge_breadth",
        )
    if any(normalized_families[item] not in policy.family_weight_caps for item in strategy_ids):
        return _all_cash(
            strategy_ids,
            estimates,
            normalized_families,
            covariance,
            as_of,
            "family_cap_required",
        )

    prior = _validated_weight_vector(hierarchical_prior, strategy_ids, "hierarchical_prior")
    previous = _validated_weight_vector(previous_weights, strategy_ids, "previous_weights")
    matrix = covariance.as_array()
    deviations = np.sqrt(np.maximum(np.diag(matrix), 0.0))
    denominator = np.outer(deviations, deviations)
    correlations = np.divide(matrix, denominator, out=np.zeros_like(matrix), where=denominator > 0)
    np.fill_diagonal(correlations, 0.0)
    redundancy = 1.0 + np.clip((np.abs(correlations) - 0.80) / 0.20, 0.0, 1.0).sum(axis=1)
    mu = np.array([float(estimates[item].lower_net_edge) for item in strategy_ids], dtype=float)
    mu /= redundancy
    prior /= redundancy
    eligible_indices = [index for index, item in enumerate(strategy_ids) if eligible[item]]

    counts_by_family: dict[StrategyFamily, int] = {}
    for strategy_id in strategy_ids:
        if eligible[strategy_id]:
            family = normalized_families[strategy_id]
            counts_by_family[family] = counts_by_family.get(family, 0) + 1
    initial_unit = min(
        0.5 / len(eligible_indices),
        policy.maximum_strategy_weight,
        *(float(policy.family_weight_caps[family]) / count for family, count in counts_by_family.items()),
    )
    if initial_unit <= 0:
        return _all_cash(
            strategy_ids,
            estimates,
            normalized_families,
            covariance,
            as_of,
            "infeasible_initial_allocation",
        )
    initial = np.zeros(len(strategy_ids), dtype=float)
    initial[eligible_indices] = initial_unit * (1.0 - 1e-9)

    def objective(weights: np.ndarray) -> float:
        return float(
            -mu @ weights
            + policy.risk_penalty * (weights @ matrix @ weights)
            + policy.turnover_penalty * np.square(weights - previous).sum()
            + policy.prior_penalty * np.square(weights - prior).sum()
        )

    constraints: list[dict[str, object]] = [
        {"type": "ineq", "fun": lambda weights: 1.0 - float(weights.sum())},
        {
            "type": "ineq",
            "fun": lambda weights: (
                float(weights.sum()) ** 2 - policy.minimum_effective_strategies * float(np.square(weights).sum())
            ),
        },
    ]
    for family in sorted(set(normalized_families.values()), key=lambda item: item.value):
        indices = np.array(
            [index for index, item in enumerate(strategy_ids) if normalized_families[item] is family],
            dtype=int,
        )
        cap = float(policy.family_weight_caps[family])
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda weights, indices=indices, cap=cap: cap - float(weights[indices].sum()),
            }
        )
    bounds = [(0.0, policy.maximum_strategy_weight if eligible[strategy_id] else 0.0) for strategy_id in strategy_ids]
    result = minimize(
        objective,
        initial,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"ftol": 1e-12, "maxiter": 2_000, "disp": False},
    )
    if not result.success or not np.isfinite(result.x).all() or not math.isfinite(float(result.fun)):
        return _all_cash(
            strategy_ids,
            estimates,
            normalized_families,
            covariance,
            as_of,
            "optimization_failed",
        )

    solved = np.asarray(result.x, dtype=float)
    solved[np.abs(solved) < 1e-12] = 0.0
    tolerance = 1e-8
    violations: list[str] = []
    if (solved < -tolerance).any() or float(solved.sum()) > 1.0 + tolerance:
        violations.append("total_weight_constraint")
    if any(solved[index] > policy.maximum_strategy_weight + tolerance for index in range(len(solved))):
        violations.append("strategy_cap")
    if any(not eligible[strategy_id] and solved[index] > tolerance for index, strategy_id in enumerate(strategy_ids)):
        violations.append("inapplicable_strategy_weight")
    for family, cap in policy.family_weight_caps.items():
        family_weight = sum(
            solved[index]
            for index, strategy_id in enumerate(strategy_ids)
            if normalized_families[strategy_id] is family
        )
        if family_weight > cap + tolerance:
            violations.append("family_cap")
    effective = _effective_count(solved)
    if effective + tolerance < policy.minimum_effective_strategies:
        violations.append("effective_strategy_breadth")
    if violations:
        return _all_cash(
            strategy_ids,
            estimates,
            normalized_families,
            covariance,
            as_of,
            *violations,
        )

    canonical_weights = {
        strategy_id: _canonical_float(max(float(solved[index]), 0.0)) for index, strategy_id in enumerate(strategy_ids)
    }
    canonical_vector = np.array([canonical_weights[item] for item in strategy_ids], dtype=float)
    canonical_total = float(canonical_vector.sum())
    if canonical_total > 1.0:
        canonical_vector /= canonical_total
        canonical_weights = {
            strategy_id: _canonical_float(float(canonical_vector[index]))
            for index, strategy_id in enumerate(strategy_ids)
        }
        canonical_total = sum(canonical_weights.values())
    cash_weight = 1.0 - canonical_total
    canonical_effective = _effective_count(np.array(tuple(canonical_weights.values()), dtype=float))
    weight_evidence = tuple(
        ContextualWeight(
            strategy_id=strategy_id,
            family=normalized_families[strategy_id],
            weight=canonical_weights[strategy_id],
            lower_net_edge=float(estimates[strategy_id].lower_net_edge),
        )
        for strategy_id in strategy_ids
    )
    payload = {
        "status": "allocated",
        "weights": canonical_weights,
        "cash_weight": cash_weight,
        "effective_strategy_count": canonical_effective,
        "covariance_hash": covariance.evidence_hash,
        "policy": policy.model_dump(mode="json"),
        "as_of": as_of,
    }
    return ContextualAllocation(
        allocation_id=canonical_hash(payload),
        status="allocated",
        reasons=(),
        weights=MappingProxyType(canonical_weights),
        weight_evidence=weight_evidence,
        cash_weight=cash_weight,
        effective_strategy_count=canonical_effective,
        covariance=covariance,
        objective_value=_canonical_float(float(result.fun)),
        as_of=as_of,
    )


__all__ = [
    "AllocationPolicy",
    "ContextualAllocation",
    "ContextualWeight",
    "CovarianceEvidence",
    "allocate_contextual_weights",
    "estimate_strategy_covariance",
]
