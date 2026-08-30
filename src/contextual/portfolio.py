"""Conservative portfolio compatibility and research-size selection."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from src.config.settings import PortfolioSelectionPolicyConfig
from src.contextual.allocation import CovarianceEvidence, estimate_strategy_covariance
from src.contextual.types import StrategyDirection
from src.strategies.types import StrategyFamily, canonical_hash


@dataclass(frozen=True, slots=True)
class ResearchOpportunity:
    decision_hash: str
    context_hash: str
    symbol: str
    direction: StrategyDirection
    asset_class: str
    sector: str
    family: StrategyFamily
    decision_time: datetime
    horizon_minutes: int
    eligible: bool
    lower_net_edge: float
    liquidity_quality: float
    probability_lower: float
    payoff_lower: float
    realized_volatility: float
    liquidity_capacity_weight: float
    remaining_risk_weight: float

    def __post_init__(self) -> None:
        if self.decision_time.tzinfo is not UTC:
            raise ValueError("opportunity decision_time must be explicit UTC")
        if self.horizon_minutes <= 0:
            raise ValueError("opportunity horizon must be positive")
        if any(
            not str(value).strip()
            for value in (
                self.decision_hash,
                self.context_hash,
                self.symbol,
                self.asset_class,
                self.sector,
            )
        ):
            raise ValueError("opportunity identity fields cannot be blank")
        numeric = (
            self.lower_net_edge,
            self.liquidity_quality,
            self.probability_lower,
            self.payoff_lower,
            self.realized_volatility,
            self.liquidity_capacity_weight,
            self.remaining_risk_weight,
        )
        if any(not math.isfinite(value) for value in numeric):
            raise ValueError("opportunity evidence must be finite")
        if not 0 <= self.liquidity_quality <= 1 or not 0 <= self.probability_lower <= 1:
            raise ValueError("opportunity quality and probability must be in [0, 1]")
        if any(
            value < 0
            for value in (
                self.payoff_lower,
                self.realized_volatility,
                self.liquidity_capacity_weight,
                self.remaining_risk_weight,
            )
        ):
            raise ValueError("opportunity size evidence cannot be negative")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())


@dataclass(frozen=True, slots=True)
class ResearchSizeEvidence:
    decision_hash: str
    raw_kelly_fraction: float
    fractional_kelly_ceiling: float
    volatility_ceiling: float
    liquidity_ceiling: float
    remaining_risk_ceiling: float
    hard_asset_ceiling: float
    ceiling: float
    reasons: tuple[str, ...]
    evidence_hash: str


@dataclass(frozen=True, slots=True)
class SelectedOpportunity:
    opportunity: ResearchOpportunity
    weight: float
    size_evidence: ResearchSizeEvidence


@dataclass(frozen=True, slots=True)
class PortfolioSelection:
    selection_id: str
    status: Literal["selected", "all_cash"]
    selected: tuple[SelectedOpportunity, ...]
    exclusions: Mapping[str, tuple[str, ...]]
    cash_weight: float
    gross_weight: float
    net_weight: float
    covariance_hash: str | None
    as_of: datetime

    @property
    def selected_symbols(self) -> tuple[str, ...]:
        return tuple(item.opportunity.symbol for item in self.selected)


PortfolioSelectionPolicy = PortfolioSelectionPolicyConfig


def research_size_ceiling(
    opportunity: ResearchOpportunity,
    policy: PortfolioSelectionPolicyConfig,
) -> ResearchSizeEvidence:
    """Return a conservative ceiling; invalid probability/payoff evidence receives zero."""

    reasons: list[str] = []
    if not 0 < opportunity.probability_lower < 1:
        reasons.append("invalid_probability_evidence")
    if opportunity.payoff_lower <= 0:
        reasons.append("invalid_payoff_evidence")
    if opportunity.realized_volatility <= 0:
        reasons.append("invalid_volatility_evidence")
    if reasons:
        payload = {"decision_hash": opportunity.decision_hash, "reasons": reasons, "ceiling": 0.0}
        return ResearchSizeEvidence(
            decision_hash=opportunity.decision_hash,
            raw_kelly_fraction=0.0,
            fractional_kelly_ceiling=0.0,
            volatility_ceiling=0.0,
            liquidity_ceiling=opportunity.liquidity_capacity_weight,
            remaining_risk_ceiling=opportunity.remaining_risk_weight,
            hard_asset_ceiling=policy.maximum_asset_weight,
            ceiling=0.0,
            reasons=tuple(reasons),
            evidence_hash=canonical_hash(payload),
        )

    raw_kelly = max(
        (opportunity.probability_lower * opportunity.payoff_lower - (1.0 - opportunity.probability_lower))
        / opportunity.payoff_lower,
        0.0,
    )
    fractional_kelly = policy.kelly_fraction * raw_kelly
    volatility_ceiling = policy.volatility_target / opportunity.realized_volatility
    ceiling = min(
        volatility_ceiling,
        opportunity.liquidity_capacity_weight,
        opportunity.remaining_risk_weight,
        fractional_kelly,
        policy.maximum_asset_weight,
    )
    if ceiling < policy.minimum_research_weight:
        reasons.append("below_minimum_research_size")
        ceiling = 0.0
    payload = {
        "decision_hash": opportunity.decision_hash,
        "raw_kelly_fraction": raw_kelly,
        "fractional_kelly_ceiling": fractional_kelly,
        "volatility_ceiling": volatility_ceiling,
        "liquidity_ceiling": opportunity.liquidity_capacity_weight,
        "remaining_risk_ceiling": opportunity.remaining_risk_weight,
        "hard_asset_ceiling": policy.maximum_asset_weight,
        "ceiling": ceiling,
        "reasons": reasons,
    }
    return ResearchSizeEvidence(
        decision_hash=opportunity.decision_hash,
        raw_kelly_fraction=raw_kelly,
        fractional_kelly_ceiling=fractional_kelly,
        volatility_ceiling=volatility_ceiling,
        liquidity_ceiling=opportunity.liquidity_capacity_weight,
        remaining_risk_ceiling=opportunity.remaining_risk_weight,
        hard_asset_ceiling=policy.maximum_asset_weight,
        ceiling=ceiling,
        reasons=tuple(reasons),
        evidence_hash=canonical_hash(payload),
    )


def _exclusion_key(opportunity: ResearchOpportunity, existing: Mapping[str, tuple[str, ...]]) -> str:
    if opportunity.symbol not in existing:
        return opportunity.symbol
    return f"{opportunity.symbol}:{opportunity.direction.value}:{opportunity.decision_hash[:12]}"


def _all_cash(
    opportunities: Sequence[ResearchOpportunity],
    exclusions: Mapping[str, tuple[str, ...]],
    as_of: datetime,
    *,
    covariance_hash: str | None = None,
) -> PortfolioSelection:
    payload = {
        "status": "all_cash",
        "opportunities": tuple(item.decision_hash for item in opportunities),
        "exclusions": dict(exclusions),
        "covariance_hash": covariance_hash,
        "as_of": as_of,
    }
    return PortfolioSelection(
        selection_id=canonical_hash(payload),
        status="all_cash",
        selected=(),
        exclusions=MappingProxyType(dict(exclusions)),
        cash_weight=1.0,
        gross_weight=0.0,
        net_weight=0.0,
        covariance_hash=covariance_hash,
        as_of=as_of,
    )


def _candidate_covariance(
    returns: pd.DataFrame,
    symbols: tuple[str, ...],
    as_of: datetime,
) -> CovarianceEvidence:
    missing = set(symbols) - set(returns.columns)
    if missing:
        raise ValueError(f"asset returns missing symbols: {', '.join(sorted(missing))}")
    return estimate_strategy_covariance(
        returns.loc[:, symbols],
        as_of,
        minimum_overlap=20,
    )


def select_portfolio_opportunities(
    opportunities: Sequence[ResearchOpportunity],
    synchronized_asset_returns: pd.DataFrame,
    policy: PortfolioSelectionPolicyConfig,
    as_of: datetime,
    *,
    current_exposures: Mapping[str, float] | None = None,
) -> PortfolioSelection:
    """Rank, de-duplicate, decorrelate, and size contemporaneous research opportunities."""

    if as_of.tzinfo is not UTC:
        raise ValueError("portfolio as_of must be explicit UTC")
    if len({item.decision_hash for item in opportunities}) != len(opportunities):
        raise ValueError("portfolio decision hashes must be unique")
    exposure = {str(symbol).upper(): float(value) for symbol, value in (current_exposures or {}).items()}
    if any(not math.isfinite(value) for value in exposure.values()):
        raise ValueError("current exposures must be finite")
    current_gross = sum(abs(value) for value in exposure.values())
    current_net = sum(exposure.values())
    if current_gross > policy.maximum_gross_exposure + 1e-12 or abs(current_net) > policy.maximum_net_exposure + 1e-12:
        exclusions = {item.symbol: ("current_risk_limit",) for item in opportunities}
        return _all_cash(opportunities, exclusions, as_of)

    ranked = sorted(
        opportunities,
        key=lambda item: (
            -item.lower_net_edge,
            -item.liquidity_quality,
            -item.probability_lower,
            item.decision_time,
            item.decision_hash,
        ),
    )
    exclusions: dict[str, tuple[str, ...]] = {}
    size_by_hash: dict[str, ResearchSizeEvidence] = {}
    preselected: list[ResearchOpportunity] = []
    seen_symbol: dict[str, ResearchOpportunity] = {}
    for opportunity in ranked:
        reasons: list[str] = []
        if not opportunity.eligible:
            reasons.append("context_not_eligible")
        if opportunity.lower_net_edge <= 0:
            reasons.append("nonpositive_lower_net_edge")
        if opportunity.decision_time > as_of:
            reasons.append("decision_after_as_of")
        elif (as_of - opportunity.decision_time).total_seconds() > opportunity.horizon_minutes * 60:
            reasons.append("decision_stale")
        if opportunity.symbol not in synchronized_asset_returns.columns:
            reasons.append("return_history_required")
        size = research_size_ceiling(opportunity, policy)
        size_by_hash[opportunity.decision_hash] = size
        reasons.extend(size.reasons)
        existing = seen_symbol.get(opportunity.symbol)
        if existing is not None:
            reasons.append(
                "conflicting_direction" if existing.direction is not opportunity.direction else "duplicate_risk_window"
            )
        if reasons:
            exclusions[_exclusion_key(opportunity, exclusions)] = tuple(dict.fromkeys(reasons))
            continue
        if len(preselected) >= policy.maximum_candidates:
            exclusions[_exclusion_key(opportunity, exclusions)] = ("candidate_limit",)
            continue
        preselected.append(opportunity)
        seen_symbol[opportunity.symbol] = opportunity

    if not preselected:
        return _all_cash(opportunities, exclusions, as_of)

    symbols = tuple(sorted({item.symbol for item in preselected}))
    covariance = _candidate_covariance(synchronized_asset_returns, symbols, as_of)
    if covariance.status != "estimated":
        for opportunity in preselected:
            exclusions[_exclusion_key(opportunity, exclusions)] = ("covariance_evidence_required",)
        return _all_cash(
            opportunities,
            exclusions,
            as_of,
            covariance_hash=covariance.evidence_hash,
        )

    decorrelated: list[ResearchOpportunity] = []
    for opportunity in preselected:
        conflict = any(
            abs(covariance.correlation(opportunity.symbol, selected.symbol)) > policy.maximum_correlation
            for selected in decorrelated
        )
        if conflict:
            exclusions[_exclusion_key(opportunity, exclusions)] = ("correlation_cluster_limit",)
            continue
        if len(decorrelated) >= policy.maximum_opportunities:
            exclusions[_exclusion_key(opportunity, exclusions)] = ("opportunity_limit",)
            continue
        decorrelated.append(opportunity)
    if not decorrelated:
        return _all_cash(
            opportunities,
            exclusions,
            as_of,
            covariance_hash=covariance.evidence_hash,
        )

    covariance_indices = [covariance.strategy_ids.index(item.symbol) for item in decorrelated]
    matrix = covariance.as_array()[np.ix_(covariance_indices, covariance_indices)]
    edges = np.array([item.lower_net_edge for item in decorrelated], dtype=float)
    signs = np.array(
        [1.0 if item.direction is StrategyDirection.LONG else -1.0 for item in decorrelated],
        dtype=float,
    )
    ceilings = np.array([size_by_hash[item.decision_hash].ceiling for item in decorrelated], dtype=float)

    def objective(weights: np.ndarray) -> float:
        return float(-edges @ weights + 10.0 * (weights @ matrix @ weights))

    constraints: list[dict[str, object]] = [
        {
            "type": "ineq",
            "fun": lambda weights: policy.maximum_gross_exposure - current_gross - float(weights.sum()),
        },
        {
            "type": "ineq",
            "fun": lambda weights: policy.maximum_net_exposure - current_net - float(signs @ weights),
        },
        {
            "type": "ineq",
            "fun": lambda weights: policy.maximum_net_exposure + current_net + float(signs @ weights),
        },
    ]
    for attribute, cap in (
        ("family", policy.maximum_family_weight),
        ("asset_class", policy.maximum_asset_class_weight),
        ("sector", policy.maximum_sector_weight),
    ):
        values = sorted({getattr(item, attribute) for item in decorrelated}, key=str)
        for value in values:
            indices = np.array(
                [index for index, item in enumerate(decorrelated) if getattr(item, attribute) == value],
                dtype=int,
            )
            constraints.append(
                {
                    "type": "ineq",
                    "fun": lambda weights, indices=indices, cap=cap: cap - float(weights[indices].sum()),
                }
            )

    result = minimize(
        objective,
        np.minimum(ceilings, policy.minimum_research_weight),
        method="SLSQP",
        bounds=[(0.0, float(ceiling)) for ceiling in ceilings],
        constraints=constraints,
        options={"ftol": 1e-12, "maxiter": 2_000, "disp": False},
    )
    if not result.success or not np.isfinite(result.x).all():
        for opportunity in decorrelated:
            exclusions[_exclusion_key(opportunity, exclusions)] = ("portfolio_optimization_failed",)
        return _all_cash(
            opportunities,
            exclusions,
            as_of,
            covariance_hash=covariance.evidence_hash,
        )

    solved = np.asarray(result.x, dtype=float)
    solved[np.abs(solved) < 1e-12] = 0.0
    selected: list[SelectedOpportunity] = []
    for index, opportunity in enumerate(decorrelated):
        weight = float(f"{max(solved[index], 0.0):.15g}")
        if weight < policy.minimum_research_weight - 1e-10:
            exclusions[_exclusion_key(opportunity, exclusions)] = ("below_minimum_research_size",)
            continue
        selected.append(
            SelectedOpportunity(
                opportunity=opportunity,
                weight=weight,
                size_evidence=size_by_hash[opportunity.decision_hash],
            )
        )
    if not selected:
        return _all_cash(
            opportunities,
            exclusions,
            as_of,
            covariance_hash=covariance.evidence_hash,
        )

    gross = sum(item.weight for item in selected)
    net = sum(
        item.weight if item.opportunity.direction is StrategyDirection.LONG else -item.weight for item in selected
    )
    tolerance = 1e-8
    if (
        current_gross + gross > policy.maximum_gross_exposure + tolerance
        or abs(current_net + net) > policy.maximum_net_exposure + tolerance
    ):
        for item in selected:
            exclusions[_exclusion_key(item.opportunity, exclusions)] = ("portfolio_validation_failed",)
        return _all_cash(
            opportunities,
            exclusions,
            as_of,
            covariance_hash=covariance.evidence_hash,
        )
    cash = 1.0 - gross
    payload = {
        "status": "selected",
        "selected": tuple((item.opportunity.decision_hash, item.weight) for item in selected),
        "exclusions": exclusions,
        "gross_weight": gross,
        "net_weight": net,
        "covariance_hash": covariance.evidence_hash,
        "as_of": as_of,
    }
    return PortfolioSelection(
        selection_id=canonical_hash(payload),
        status="selected",
        selected=tuple(selected),
        exclusions=MappingProxyType(exclusions),
        cash_weight=cash,
        gross_weight=gross,
        net_weight=net,
        covariance_hash=covariance.evidence_hash,
        as_of=as_of,
    )


__all__ = [
    "PortfolioSelection",
    "PortfolioSelectionPolicy",
    "ResearchOpportunity",
    "ResearchSizeEvidence",
    "SelectedOpportunity",
    "research_size_ceiling",
    "select_portfolio_opportunities",
]
