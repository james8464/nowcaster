"""Chronologically separated research for day-trading opportunity signals."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.backtest.opportunities import audit_strategy_opportunities, summarize_opportunities
from src.models.trade_outcomes import BarrierPolicy
from src.strategies.indicators import atr
from src.strategies.library import StrategyContext
from src.strategies.registry import RegisteredStrategy, StrategyRegistry
from src.strategies.types import BarInterval, canonical_hash


@dataclass(frozen=True, slots=True)
class OpportunityResearchProtocol:
    development_fraction: float = 0.6
    validation_fraction: float = 0.2
    minimum_development_opportunities: int = 300
    minimum_validation_opportunities: int = 100
    minimum_bootstrap_probability: float = 0.99
    bootstrap_samples: int = 2_000
    consensus_minimum_breadth: int = 2
    consensus_minimum_families: int = 2
    consensus_vote_threshold: float = 0.8

    def __post_init__(self) -> None:
        if (
            not 0 < self.development_fraction < 1
            or not 0 < self.validation_fraction < 1
            or self.development_fraction + self.validation_fraction >= 1
        ):
            raise ValueError("research chronology fractions are invalid")
        counts = (
            self.minimum_development_opportunities,
            self.minimum_validation_opportunities,
            self.bootstrap_samples,
            self.consensus_minimum_breadth,
            self.consensus_minimum_families,
        )
        if any(value < 1 for value in counts):
            raise ValueError("research evidence counts must be positive")
        if not 0.5 < self.minimum_bootstrap_probability <= 1:
            raise ValueError("bootstrap gate must be in (0.5, 1]")
        if not 0 < self.consensus_vote_threshold <= 1:
            raise ValueError("consensus vote threshold must be in (0, 1]")


def _interval(frame: pd.DataFrame) -> BarInterval:
    values = frame.get("interval", pd.Series(dtype=str)).astype(str).unique()
    if len(values) != 1:
        raise ValueError("opportunity scope requires exactly one interval")
    return BarInterval(values[0])


def _contiguous_segment_ids(bars: pd.DataFrame) -> pd.Series:
    required = {"open_timestamp", "close_timestamp"}
    missing = required - set(bars)
    if missing:
        raise ValueError(f"gap-safe research bars are missing fields: {sorted(missing)}")
    opened = pd.to_datetime(bars["open_timestamp"], utc=True)
    previous_close = pd.to_datetime(bars["close_timestamp"], utc=True).shift(1)
    return opened.ne(previous_close).cumsum()


def gap_safe_atr(bars: pd.DataFrame, *, period: int = 14) -> pd.Series:
    """Calculate ATR independently inside each continuous market-data segment."""

    if period < 1:
        raise ValueError("ATR period must be positive")
    required = {"high", "low", "close"}
    missing = required - set(bars)
    if missing:
        raise ValueError(f"gap-safe ATR bars are missing fields: {sorted(missing)}")
    result = pd.Series(np.nan, index=bars.index, dtype=float)
    for _, segment in bars.groupby(_contiguous_segment_ids(bars), sort=False):
        values = atr(
            segment["high"].reset_index(drop=True),
            segment["low"].reset_index(drop=True),
            segment["close"].reset_index(drop=True),
            period,
        )
        result.loc[segment.index] = values.to_numpy(dtype=float)
    return result


def _gap_safe_signals(
    registered: RegisteredStrategy,
    bars: pd.DataFrame,
    context: StrategyContext,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for _, segment in bars.groupby(_contiguous_segment_ids(bars), sort=False):
        isolated = segment.reset_index(drop=True).copy()
        signals = registered.generator(registered.spec, isolated, context).reset_index(drop=True)
        if len(signals) != len(isolated):
            raise ValueError("a gap-isolated strategy must emit one decision row per finalized bar")
        frames.append(signals)
    return pd.concat(frames, ignore_index=True)


def _segment(outcomes: pd.DataFrame, *, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if outcomes.empty:
        return outcomes.copy()
    decisions = pd.to_datetime(outcomes["decision_timestamp"], utc=True)
    resolved = pd.to_datetime(outcomes["outcome_available_at"], utc=True)
    return outcomes.loc[(decisions >= start) & (decisions < end) & (resolved <= end)].copy()


def _segment_summaries(
    outcomes: pd.DataFrame,
    boundaries: dict[str, pd.Timestamp],
    protocol: OpportunityResearchProtocol,
) -> dict[str, dict[str, Any]]:
    ranges = {
        "development": (boundaries["scope_start"], boundaries["development_end"]),
        "validation": (boundaries["development_end"], boundaries["validation_end"]),
        "holdout": (boundaries["validation_end"], boundaries["scope_end"]),
    }
    return {
        name: summarize_opportunities(
            _segment(outcomes, start=start, end=end),
            bootstrap_samples=protocol.bootstrap_samples,
        )
        for name, (start, end) in ranges.items()
    }


def _doubled_cost_mean(outcomes: pd.DataFrame) -> float | None:
    if outcomes.empty:
        return None
    gross = pd.to_numeric(outcomes["gross_return"], errors="coerce")
    costs = pd.to_numeric(outcomes["round_trip_cost_bps"], errors="coerce") / 10_000
    values = gross - 2 * costs
    if values.isna().any() or not np.isfinite(values).all():
        raise ValueError("cost-stressed opportunity returns are invalid")
    return float(values.mean())


def _component_gate(
    outcomes: pd.DataFrame,
    summaries: dict[str, dict[str, Any]],
    boundaries: dict[str, pd.Timestamp],
    protocol: OpportunityResearchProtocol,
    *,
    minimum_bootstrap_probability: float,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    development, validation = summaries["development"], summaries["validation"]
    if int(development.get("opportunities", 0)) < protocol.minimum_development_opportunities:
        reasons.append("insufficient development opportunities")
    if int(validation.get("opportunities", 0)) < protocol.minimum_validation_opportunities:
        reasons.append("insufficient validation opportunities")
    for name, summary in (("development", development), ("validation", validation)):
        lower = summary.get("lower_mean_net_return")
        if lower is None or not math.isfinite(float(lower)) or float(lower) <= 0:
            reasons.append(f"{name} lower net edge is not positive")
        probability = summary.get("bootstrap_probability_positive")
        if (
            probability is None
            or not math.isfinite(float(probability))
            or float(probability) < minimum_bootstrap_probability
        ):
            reasons.append(f"{name} bootstrap probability failed")
    ranges = {
        "development": (boundaries["scope_start"], boundaries["development_end"]),
        "validation": (boundaries["development_end"], boundaries["validation_end"]),
    }
    for name, (start, end) in ranges.items():
        stressed = _doubled_cost_mean(_segment(outcomes, start=start, end=end))
        if stressed is None or stressed <= 0:
            reasons.append(f"{name} doubled-cost edge is not positive")
    return not reasons, reasons


def _consensus_signals(
    components: list[tuple[RegisteredStrategy, str, int, pd.DataFrame]],
    protocol: OpportunityResearchProtocol,
) -> pd.DataFrame | None:
    if not components:
        return None
    first = components[0][3].reset_index(drop=True)
    signed = np.zeros(len(first), dtype=float)
    denominator = np.zeros(len(first), dtype=float)
    breadth = np.zeros(len(first), dtype=int)
    family_mask = np.zeros(len(first), dtype=np.uint64)
    family_names = sorted({item.spec.family.value for item, _, _, _ in components})
    families = {family: index for index, family in enumerate(family_names)}
    for item, _, direction_value, frame in components:
        candidate = frame.reset_index(drop=True)
        if len(candidate) != len(first) or not pd.to_datetime(candidate["decision_timestamp"], utc=True).equals(
            pd.to_datetime(first["decision_timestamp"], utc=True)
        ):
            raise ValueError("consensus components must share one decision chronology")
        raw_signal = pd.to_numeric(candidate["signal"], errors="raise").to_numpy(dtype=int)
        raw_strength = pd.to_numeric(candidate["strength"], errors="raise").to_numpy(dtype=float)
        active = raw_signal == direction_value
        signal = np.where(active, direction_value, 0)
        strength = np.where(active, raw_strength, 0.0)
        signed += signal * strength
        denominator += np.where(active, strength, 0.0)
        breadth += active
        family_mask[active] |= np.uint64(1 << families[item.spec.family.value])
    vote = np.divide(signed, denominator, out=np.zeros_like(signed), where=denominator > 0)
    family_count = np.fromiter((int(value).bit_count() for value in family_mask), dtype=int, count=len(first))
    eligible = (
        (breadth >= protocol.consensus_minimum_breadth)
        & (family_count >= protocol.consensus_minimum_families)
        & (np.abs(vote) >= protocol.consensus_vote_threshold)
    )
    return pd.DataFrame(
        {
            "decision_timestamp": first["decision_timestamp"].copy(),
            "data_through": first["data_through"].copy(),
            "signal": np.where(eligible, np.sign(vote), 0).astype(int),
            "strength": np.where(eligible, np.abs(vote), 0.0),
            "reason": np.where(eligible, "multi-family retrospective consensus", "consensus abstention"),
        }
    )


def _audit_consensus(
    bars: pd.DataFrame,
    components: list[tuple[RegisteredStrategy, str, int, pd.DataFrame]],
    policy: BarrierPolicy,
    protocol: OpportunityResearchProtocol,
    boundaries: dict[str, pd.Timestamp],
) -> tuple[dict[str, Any], pd.DataFrame] | None:
    signals = _consensus_signals(components, protocol)
    if signals is None:
        return None
    audit = audit_strategy_opportunities(
        bars,
        signals,
        policy,
        strategy_id="retrospective_consensus_v1",
        family="multi_family",
    )
    return (
        {
            **_segment_summaries(audit.outcomes, boundaries, protocol),
            "diagnostics": audit.diagnostics,
            "component_ids": [component_id for _, component_id, _, _ in components],
        },
        audit.outcomes,
    )


def _holdout_passes(
    outcomes: pd.DataFrame,
    summary: dict[str, Any],
    boundaries: dict[str, pd.Timestamp],
    protocol: OpportunityResearchProtocol,
) -> bool:
    lower = summary.get("lower_mean_net_return")
    probability = summary.get("bootstrap_probability_positive")
    selected = _segment(outcomes, start=boundaries["validation_end"], end=boundaries["scope_end"])
    stressed = _doubled_cost_mean(selected)
    return bool(
        int(summary.get("opportunities", 0)) >= protocol.minimum_validation_opportunities
        and lower is not None
        and float(lower) > 0
        and probability is not None
        and float(probability) >= protocol.minimum_bootstrap_probability
        and stressed is not None
        and stressed > 0
    )


def audit_opportunity_scope(
    bars: pd.DataFrame,
    registry: StrategyRegistry,
    policy: BarrierPolicy,
    *,
    protocol: OpportunityResearchProtocol | None = None,
) -> dict[str, Any]:
    """Evaluate fixed rules with development/validation/holdout separation.

    Archive results are useful for rejecting hypotheses, but this function always
    marks them ineligible for promotion because archive corrections and unseen
    forward execution cannot be reconstructed.
    """

    protocol = protocol or OpportunityResearchProtocol()
    ordered = bars.sort_values("open_timestamp", kind="stable").reset_index(drop=True).copy()
    if len(ordered) < 10:
        raise ValueError("opportunity research requires at least ten bars")
    interval = _interval(ordered)
    development_index = int(len(ordered) * protocol.development_fraction)
    validation_index = int(len(ordered) * (protocol.development_fraction + protocol.validation_fraction))
    if not 1 <= development_index < validation_index < len(ordered):
        raise ValueError("opportunity chronology cannot form three segments")
    boundaries = {
        "scope_start": pd.Timestamp(ordered.iloc[0]["open_timestamp"]),
        "development_end": pd.Timestamp(ordered.iloc[development_index]["open_timestamp"]),
        "validation_end": pd.Timestamp(ordered.iloc[validation_index]["open_timestamp"]),
        "scope_end": pd.Timestamp(ordered.iloc[-1]["close_timestamp"]),
    }
    context = StrategyContext.for_market(
        str(ordered.iloc[0].get("provider", "unknown")),
        str(ordered.iloc[0].get("feed", "unknown")),
    )
    eligible_strategies = [item for item in registry.enabled() if interval in item.spec.intervals]
    directional_hypotheses = len(eligible_strategies) * 2
    familywise_tests = max(1, directional_hypotheses * 2)
    familywise_probability = 1 - (1 - protocol.minimum_bootstrap_probability) / familywise_tests
    multiplicity = {
        "method": "Bonferroni family-wise bootstrap probability across direction and selection split",
        "directional_hypotheses": directional_hypotheses,
        "development_and_validation_tests": familywise_tests,
        "familywise_bootstrap_probability_threshold": familywise_probability,
    }
    strategy_rows: list[dict[str, Any]] = []
    signals_by_component: list[tuple[RegisteredStrategy, str, int, pd.DataFrame]] = []
    selected: list[tuple[RegisteredStrategy, str, int, pd.DataFrame]] = []
    selection_evidence: list[dict[str, Any]] = []
    for registered in eligible_strategies:
        combined_signals = _gap_safe_signals(registered, ordered, context)
        for direction_value, direction_name in ((1, "long"), (-1, "short")):
            component_id = f"{registered.spec.strategy_id}:{direction_name}"
            signals = combined_signals.copy()
            active = pd.to_numeric(signals["signal"], errors="raise").eq(direction_value)
            signals.loc[~active, "signal"] = 0
            signals.loc[~active, "strength"] = 0.0
            audit = audit_strategy_opportunities(
                ordered,
                signals,
                policy,
                strategy_id=component_id,
                family=registered.spec.family.value,
            )
            summaries = _segment_summaries(audit.outcomes, boundaries, protocol)
            passed, reasons = _component_gate(
                audit.outcomes,
                summaries,
                boundaries,
                protocol,
                minimum_bootstrap_probability=familywise_probability,
            )
            row = {
                "strategy_id": component_id,
                "base_strategy_id": registered.spec.strategy_id,
                "direction": direction_name,
                "strategy_version": registered.spec.deterministic_version,
                "family": registered.spec.family.value,
                "passed_retrospective_gate": passed,
                "gate_reasons": reasons,
                "diagnostics": audit.diagnostics,
                **summaries,
            }
            strategy_rows.append(row)
            signals_by_component.append((registered, component_id, direction_value, combined_signals))
            if passed:
                selected.append((registered, component_id, direction_value, combined_signals))
                selection_evidence.append(
                    {
                        "strategy_id": component_id,
                        "base_strategy_id": registered.spec.strategy_id,
                        "direction": direction_name,
                        "strategy_version": registered.spec.deterministic_version,
                        "development": summaries["development"],
                        "validation": summaries["validation"],
                    }
                )

    boundary_payload = {name: value.isoformat() for name, value in boundaries.items()}
    selection_hash = canonical_hash(
        {
            "protocol": asdict(protocol),
            "multiplicity": multiplicity,
            "policy": asdict(policy),
            "boundaries": boundary_payload,
            "selected": selection_evidence,
        }
    )
    diagnostic_consensus = _audit_consensus(ordered, signals_by_component, policy, protocol, boundaries)
    candidate = _audit_consensus(ordered, selected, policy, protocol, boundaries)
    candidate_payload = candidate[0] if candidate is not None else None
    if candidate is None:
        status = "no_reliable_strategy_found"
        reason = "no component passed the predeclared development and validation gates"
    elif _holdout_passes(candidate[1], candidate[0]["holdout"], boundaries, protocol):
        status = "retrospective_candidate_found_forward_test_required"
        reason = "the frozen retrospective ensemble passed its untouched holdout; forward shadow evidence is required"
    else:
        status = "no_reliable_strategy_found"
        reason = "the development-selected ensemble failed the chronological holdout"
    return {
        "schema_version": 1,
        "evidence_tier": "retrospective_archive_only",
        "eligible_for_live_promotion": False,
        "symbol": str(ordered.iloc[0].get("symbol", "unknown")),
        "interval": interval.value,
        "bar_count": len(ordered),
        "boundaries": boundary_payload,
        "protocol": asdict(protocol),
        "multiplicity": multiplicity,
        "barrier_policy": asdict(policy),
        "strategies": strategy_rows,
        "selected_components": [component_id for _, component_id, _, _ in selected],
        "selection_hash": selection_hash,
        "diagnostic_all_component_consensus": diagnostic_consensus[0] if diagnostic_consensus is not None else None,
        "candidate_ensemble": candidate_payload,
        "decision": {"status": status, "reason": reason},
        "note": (
            "This archive replay may reject a hypothesis but cannot authorize alerts, paper orders, or live money. "
            "A short hypothesis is not executable on the configured Binance Spot product."
        ),
    }


__all__ = ["OpportunityResearchProtocol", "audit_opportunity_scope", "gap_safe_atr"]
