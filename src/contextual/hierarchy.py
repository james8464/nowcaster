"""Hierarchical partial pooling for causal strategy outcome evidence."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from src.backtest.robustness import effective_sample_size, lower_mean_confidence_bound
from src.contextual.types import (
    AssetProfileName,
    ContextLevel,
    MarketRegime,
    StrategyDirection,
)
from src.strategies.types import BarInterval, StrategyMode, canonical_hash

REGIME_PROBABILITY_COLUMNS: Mapping[MarketRegime, str] = {
    MarketRegime.TREND_NORMAL: "regime_trend_normal",
    MarketRegime.TREND_ELEVATED_VOLATILITY: "regime_trend_elevated_volatility",
    MarketRegime.RANGE_LIQUID: "regime_range_liquid",
    MarketRegime.STRESSED_OR_ILLIQUID: "regime_stressed_or_illiquid",
}

_BASE_COLUMNS = (
    "dataset_hash",
    "protocol_hash",
    "provider",
    "feed",
    "venue",
    "product",
    "interval",
    "mode",
    "strategy_id",
    "direction",
)
_REQUIRED_COLUMNS = {
    "outcome_id",
    *_BASE_COLUMNS,
    "asset_class",
    "profile",
    "symbol",
    "decision_timestamp",
    "outcome_available_at",
    "net_return",
    *REGIME_PROBABILITY_COLUMNS.values(),
}
_CONFIDENCE_Z = 1.6448536269514722


@dataclass(frozen=True, slots=True)
class HierarchicalEstimate:
    estimate_id: str
    strategy_id: str
    level: ContextLevel
    dataset_hash: str
    protocol_hash: str
    provider: str
    feed: str
    venue: str
    product: str
    interval: BarInterval
    mode: StrategyMode
    direction: StrategyDirection
    asset_class: str | None
    profile: AssetProfileName | None
    symbol: str | None
    regime: MarketRegime | None
    parent_estimate_id: str | None
    alpha: float
    nominal_observations: int
    effective_observations: float
    local_mean_net_edge: float
    parent_mean_net_edge: float
    mean_net_edge: float
    local_variance: float
    uncertainty: float
    lower_net_edge: float
    evidence_through: datetime

    @property
    def context_hash(self) -> str:
        identity = {
            "dataset_hash": self.dataset_hash,
            "protocol_hash": self.protocol_hash,
            "provider": self.provider,
            "feed": self.feed,
            "venue": self.venue,
            "product": self.product,
            "interval": self.interval,
            "mode": self.mode,
            "strategy_id": self.strategy_id,
            "direction": self.direction,
            "level": self.level,
            "asset_class": self.asset_class,
            "profile": self.profile,
            "symbol": self.symbol,
            "regime": self.regime,
        }
        return canonical_hash(identity)


@dataclass(frozen=True, slots=True)
class BlendedRegimeEstimate:
    strategy_id: str
    symbol: str
    direction: StrategyDirection
    mean_net_edge: float
    lower_net_edge: float
    uncertainty: float
    parent_fallback_mass: float
    component_estimate_ids: tuple[str, ...]
    blend_hash: str


@dataclass(frozen=True, slots=True)
class HierarchyResult:
    estimates: tuple[HierarchicalEstimate, ...]
    as_of: datetime
    outcome_count: int
    evidence_hash: str

    def _matching_leafs(
        self,
        strategy_id: str,
        symbol: str,
        direction: StrategyDirection | str,
        regime: MarketRegime | str | None,
    ) -> tuple[HierarchicalEstimate, ...]:
        normalized_direction = StrategyDirection(direction)
        normalized_regime = MarketRegime(regime) if regime is not None else None
        level = ContextLevel.ASSET_REGIME if normalized_regime is not None else ContextLevel.ASSET
        return tuple(
            item
            for item in self.estimates
            if item.level is level
            and item.strategy_id == strategy_id
            and item.symbol == symbol.upper()
            and item.direction is normalized_direction
            and item.regime == normalized_regime
        )

    def leaf(
        self,
        strategy_id: str,
        symbol: str,
        direction: StrategyDirection | str = StrategyDirection.LONG,
        *,
        regime: MarketRegime | str | None = None,
    ) -> HierarchicalEstimate:
        matching = self._matching_leafs(strategy_id, symbol, direction, regime)
        if len(matching) != 1:
            raise KeyError(
                f"expected one contextual leaf for {strategy_id}/{symbol}/{direction}/{regime}; found {len(matching)}"
            )
        return matching[0]

    def parent(
        self,
        strategy_id: str,
        symbol: str,
        direction: StrategyDirection | str = StrategyDirection.LONG,
    ) -> HierarchicalEstimate:
        leaf = self.leaf(strategy_id, symbol, direction)
        matching = tuple(item for item in self.estimates if item.estimate_id == leaf.parent_estimate_id)
        if len(matching) != 1:
            raise KeyError("contextual leaf has no unique parent estimate")
        return matching[0]


def _require_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is not UTC:
        raise ValueError(f"{label} must be an explicit UTC datetime")
    return value


def _strict_utc_series(values: pd.Series, label: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="raise")
    timezone = getattr(parsed.dtype, "tz", None)
    if timezone is None or str(timezone) != "UTC":
        raise ValueError(f"{label} must contain explicit UTC timestamps")
    return parsed


def _validated_outcomes(outcomes: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    _require_utc(as_of, "as_of")
    missing = sorted(_REQUIRED_COLUMNS - set(outcomes.columns))
    if missing:
        raise ValueError(f"outcomes missing contextual columns: {', '.join(missing)}")
    if outcomes.empty:
        raise ValueError("hierarchical estimates require resolved outcomes")
    frame = outcomes.copy()
    if frame["outcome_id"].astype(str).duplicated().any():
        raise ValueError("contextual outcome IDs must be unique")
    for column in ("decision_timestamp", "outcome_available_at"):
        frame[column] = _strict_utc_series(frame[column], column)
    if (frame["outcome_available_at"] < frame["decision_timestamp"]).any():
        raise ValueError("outcomes cannot be available before their decision")
    if (frame["outcome_available_at"] > pd.Timestamp(as_of)).any():
        raise ValueError("all outcomes must be available by as_of")

    text_columns = (*_BASE_COLUMNS, "asset_class", "profile", "symbol", "outcome_id")
    if any(frame[column].astype(str).str.strip().eq("").any() for column in text_columns):
        raise ValueError("contextual outcome identity fields cannot be blank")
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    for column in ("dataset_hash", "protocol_hash", "provider", "feed", "venue", "product"):
        frame[column] = frame[column].astype(str)
    frame["interval"] = frame["interval"].map(lambda value: BarInterval(value).value)
    frame["mode"] = frame["mode"].map(lambda value: StrategyMode(value).value)
    frame["direction"] = frame["direction"].map(lambda value: StrategyDirection(value).value)
    frame["profile"] = frame["profile"].map(lambda value: AssetProfileName(value).value)

    numeric_columns = ("net_return", *REGIME_PROBABILITY_COLUMNS.values())
    numeric = frame.loc[:, numeric_columns].apply(pd.to_numeric, errors="coerce").astype(float)
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("contextual returns and regime probabilities must be finite")
    probabilities = numeric.loc[:, REGIME_PROBABILITY_COLUMNS.values()]
    if (probabilities < 0).any().any() or (probabilities > 1).any().any():
        raise ValueError("regime probabilities must be in [0, 1]")
    if not np.allclose(probabilities.sum(axis=1).to_numpy(), 1.0, atol=1e-9, rtol=0):
        raise ValueError("regime probabilities must sum to one")
    frame.loc[:, numeric_columns] = numeric
    return frame.sort_values(["outcome_available_at", "decision_timestamp", "outcome_id"], kind="stable").reset_index(
        drop=True
    )


def _normalized_strengths(prior_strengths: Mapping[ContextLevel | str, float]) -> dict[ContextLevel, float]:
    normalized = {ContextLevel(level): float(value) for level, value in prior_strengths.items()}
    missing = set(ContextLevel) - set(normalized)
    if missing:
        names = ", ".join(sorted(item.value for item in missing))
        raise ValueError(f"missing hierarchy strengths: {names}")
    if any(not math.isfinite(value) or value <= 0 for value in normalized.values()):
        raise ValueError("hierarchy strengths must be finite and positive")
    return normalized


def _local_statistics(values: np.ndarray, weights: np.ndarray | None) -> tuple[int, float, float, float, float | None]:
    if weights is None:
        nominal = len(values)
        mean = float(values.mean())
        variance = float(values.var(ddof=1)) if nominal >= 2 else 0.0
        effective = effective_sample_size(values)
        lower = lower_mean_confidence_bound(values) if nominal >= 2 else None
        return nominal, effective, mean, variance, lower

    positive = weights > 0
    values = values[positive]
    weights = weights[positive]
    nominal = len(values)
    if nominal == 0 or float(weights.sum()) <= 0:
        raise ValueError("soft-regime estimate requires positive probability mass")
    total = float(weights.sum())
    mean = float(np.dot(values, weights) / total)
    variance = float(np.dot(weights, np.square(values - mean)) / total)
    kish = total**2 / float(np.square(weights).sum())
    serial_effective = effective_sample_size(values)
    effective = float(min(kish, serial_effective, nominal))
    local_error = math.sqrt(variance / max(effective, 1.0))
    lower = mean - _CONFIDENCE_Z * local_error if nominal >= 2 else None
    return nominal, effective, mean, variance, lower


def _build_estimate(
    frame: pd.DataFrame,
    *,
    level: ContextLevel,
    identity: Mapping[str, object],
    parent: HierarchicalEstimate | None,
    prior_strength: float,
    weights: np.ndarray | None = None,
) -> HierarchicalEstimate:
    values = frame["net_return"].to_numpy(dtype=float)
    nominal, effective, local_mean, variance, local_lower = _local_statistics(values, weights)
    parent_mean = parent.mean_net_edge if parent is not None else 0.0
    parent_uncertainty = parent.uncertainty if parent is not None else 0.0
    alpha = effective / (effective + prior_strength)
    mean = alpha * local_mean + (1.0 - alpha) * parent_mean
    uncertainty = math.sqrt(alpha**2 * variance / max(effective, 1.0) + (1.0 - alpha) ** 2 * parent_uncertainty**2)
    lower = min(
        mean - _CONFIDENCE_Z * uncertainty,
        local_lower if local_lower is not None else mean,
    )
    evidence_through = pd.Timestamp(frame["outcome_available_at"].max()).to_pydatetime()
    _require_utc(evidence_through, "evidence_through")
    payload = {
        **identity,
        "level": level,
        "parent_estimate_id": parent.estimate_id if parent is not None else None,
        "alpha": alpha,
        "nominal_observations": nominal,
        "effective_observations": effective,
        "local_mean_net_edge": local_mean,
        "parent_mean_net_edge": parent_mean,
        "mean_net_edge": mean,
        "local_variance": variance,
        "uncertainty": uncertainty,
        "lower_net_edge": lower,
        "evidence_through": evidence_through,
    }
    estimate_id = canonical_hash(payload)
    return HierarchicalEstimate(
        estimate_id=estimate_id,
        strategy_id=str(identity["strategy_id"]),
        level=level,
        dataset_hash=str(identity["dataset_hash"]),
        protocol_hash=str(identity["protocol_hash"]),
        provider=str(identity["provider"]),
        feed=str(identity["feed"]),
        venue=str(identity["venue"]),
        product=str(identity["product"]),
        interval=BarInterval(str(identity["interval"])),
        mode=StrategyMode(str(identity["mode"])),
        direction=StrategyDirection(str(identity["direction"])),
        asset_class=str(identity["asset_class"]) if identity.get("asset_class") is not None else None,
        profile=(AssetProfileName(str(identity["profile"])) if identity.get("profile") is not None else None),
        symbol=str(identity["symbol"]) if identity.get("symbol") is not None else None,
        regime=MarketRegime(str(identity["regime"])) if identity.get("regime") is not None else None,
        parent_estimate_id=parent.estimate_id if parent is not None else None,
        alpha=alpha,
        nominal_observations=nominal,
        effective_observations=effective,
        local_mean_net_edge=local_mean,
        parent_mean_net_edge=parent_mean,
        mean_net_edge=mean,
        local_variance=variance,
        uncertainty=uncertainty,
        lower_net_edge=lower,
        evidence_through=evidence_through,
    )


def _identity(base_values: tuple[object, ...], **updates: object) -> dict[str, object]:
    identity = dict(zip(_BASE_COLUMNS, base_values, strict=True))
    identity.update({"asset_class": None, "profile": None, "symbol": None, "regime": None})
    identity.update(updates)
    return identity


def build_hierarchical_estimates(
    outcomes: pd.DataFrame,
    as_of: datetime,
    prior_strengths: Mapping[ContextLevel | str, float],
) -> HierarchyResult:
    """Build global → class → profile → asset → soft-regime estimates."""

    frame = _validated_outcomes(outcomes, as_of)
    strengths = _normalized_strengths(prior_strengths)
    estimates: list[HierarchicalEstimate] = []

    for base_key, base_frame in frame.groupby(list(_BASE_COLUMNS), sort=True, dropna=False):
        base_values = base_key if isinstance(base_key, tuple) else (base_key,)
        root = _build_estimate(
            base_frame,
            level=ContextLevel.GLOBAL,
            identity=_identity(base_values),
            parent=None,
            prior_strength=strengths[ContextLevel.GLOBAL],
        )
        estimates.append(root)
        for asset_class, class_frame in base_frame.groupby("asset_class", sort=True):
            class_estimate = _build_estimate(
                class_frame,
                level=ContextLevel.ASSET_CLASS,
                identity=_identity(base_values, asset_class=asset_class),
                parent=root,
                prior_strength=strengths[ContextLevel.ASSET_CLASS],
            )
            estimates.append(class_estimate)
            for profile, profile_frame in class_frame.groupby("profile", sort=True):
                profile_estimate = _build_estimate(
                    profile_frame,
                    level=ContextLevel.PROFILE,
                    identity=_identity(base_values, asset_class=asset_class, profile=profile),
                    parent=class_estimate,
                    prior_strength=strengths[ContextLevel.PROFILE],
                )
                estimates.append(profile_estimate)
                for symbol, asset_frame in profile_frame.groupby("symbol", sort=True):
                    asset_estimate = _build_estimate(
                        asset_frame,
                        level=ContextLevel.ASSET,
                        identity=_identity(
                            base_values,
                            asset_class=asset_class,
                            profile=profile,
                            symbol=symbol,
                        ),
                        parent=profile_estimate,
                        prior_strength=strengths[ContextLevel.ASSET],
                    )
                    estimates.append(asset_estimate)
                    for regime, probability_column in REGIME_PROBABILITY_COLUMNS.items():
                        probability = asset_frame[probability_column].to_numpy(dtype=float)
                        if not (probability > 0).any():
                            continue
                        estimates.append(
                            _build_estimate(
                                asset_frame,
                                level=ContextLevel.ASSET_REGIME,
                                identity=_identity(
                                    base_values,
                                    asset_class=asset_class,
                                    profile=profile,
                                    symbol=symbol,
                                    regime=regime,
                                ),
                                parent=asset_estimate,
                                prior_strength=strengths[ContextLevel.ASSET_REGIME],
                                weights=probability,
                            )
                        )

    estimates_tuple = tuple(estimates)
    evidence_hash = canonical_hash(
        {
            "as_of": as_of,
            "outcome_ids": tuple(frame["outcome_id"].astype(str)),
            "estimate_ids": tuple(item.estimate_id for item in estimates_tuple),
        }
    )
    return HierarchyResult(
        estimates=estimates_tuple,
        as_of=as_of,
        outcome_count=len(frame),
        evidence_hash=evidence_hash,
    )


def blend_current_regime(
    hierarchy: HierarchyResult,
    posterior: Mapping[MarketRegime | str, float],
    *,
    strategy_id: str,
    symbol: str,
    direction: StrategyDirection | str = StrategyDirection.LONG,
) -> BlendedRegimeEstimate:
    """Blend stored regime leaves, routing absent mass to the non-regime asset parent."""

    normalized = {MarketRegime(regime): float(value) for regime, value in posterior.items()}
    if set(normalized) != set(MarketRegime):
        raise ValueError("posterior must contain the complete fixed regime taxonomy")
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in normalized.values()):
        raise ValueError("posterior probabilities must be finite and in [0, 1]")
    if not math.isclose(sum(normalized.values()), 1.0, abs_tol=1e-9):
        raise ValueError("posterior probabilities must sum to one")

    normalized_direction = StrategyDirection(direction)
    parent = hierarchy.leaf(strategy_id, symbol, normalized_direction)
    components: list[tuple[float, HierarchicalEstimate]] = []
    missing_mass = 0.0
    for regime in MarketRegime:
        mass = normalized[regime]
        try:
            estimate = hierarchy.leaf(
                strategy_id,
                symbol,
                normalized_direction,
                regime=regime,
            )
        except KeyError:
            estimate = parent
            missing_mass += mass
        components.append((mass, estimate))

    mean = sum(mass * estimate.mean_net_edge for mass, estimate in components)
    uncertainty = math.sqrt(sum((mass * estimate.uncertainty) ** 2 for mass, estimate in components))
    uncertainty *= 1.0 + missing_mass
    component_lower = sum(mass * estimate.lower_net_edge for mass, estimate in components)
    lower = min(component_lower, mean - _CONFIDENCE_Z * uncertainty)
    component_ids = tuple(estimate.estimate_id for _, estimate in components)
    payload = {
        "hierarchy_hash": hierarchy.evidence_hash,
        "strategy_id": strategy_id,
        "symbol": symbol.upper(),
        "direction": normalized_direction,
        "posterior": {regime.value: normalized[regime] for regime in MarketRegime},
        "mean_net_edge": mean,
        "lower_net_edge": lower,
        "uncertainty": uncertainty,
        "parent_fallback_mass": missing_mass,
        "component_estimate_ids": component_ids,
    }
    return BlendedRegimeEstimate(
        strategy_id=strategy_id,
        symbol=symbol.upper(),
        direction=normalized_direction,
        mean_net_edge=mean,
        lower_net_edge=lower,
        uncertainty=uncertainty,
        parent_fallback_mass=missing_mass,
        component_estimate_ids=component_ids,
        blend_hash=canonical_hash(payload),
    )


__all__ = [
    "REGIME_PROBABILITY_COLUMNS",
    "BlendedRegimeEstimate",
    "HierarchicalEstimate",
    "HierarchyResult",
    "blend_current_regime",
    "build_hierarchical_estimates",
]
