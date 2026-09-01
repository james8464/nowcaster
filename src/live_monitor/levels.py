from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import numpy as np
from pydantic import Field, model_validator

from src.live_monitor.types import Direction, LiveMonitorModel, MarketQuote, TradeLevelPolicy, TradePlan
from src.models.trade_outcomes import TradeOutcome
from src.strategies.types import canonical_hash

_INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1_440}

DEFAULT_TRADE_LEVEL_POLICY = TradeLevelPolicy(
    atr_multiplier=Decimal("1"),
    maximum_chase_bps=Decimal("10"),
    maximum_stop_atr=Decimal("4"),
    minimum_stop_noise_multiple=Decimal("2"),
    minimum_target_1_r=Decimal("1"),
    minimum_target_2_r=Decimal("1.5"),
    expires_after_bars=3,
)


class EmpiricalLevelEvidence(LiveMonitorModel):
    sample_size: int = Field(ge=1)
    effective_sample_size: Decimal = Field(gt=0)
    stop_atr: Decimal = Field(gt=0)
    target_1_atr: Decimal = Field(gt=0)
    target_2_atr: Decimal = Field(gt=0)
    success_probability: Decimal = Field(ge=0, le=1)
    expected_net_edge: Decimal
    lower_net_edge: Decimal

    @model_validator(mode="after")
    def coherent_evidence(self) -> EmpiricalLevelEvidence:
        if self.effective_sample_size > self.sample_size:
            raise ValueError("effective sample cannot exceed nominal sample")
        if self.target_2_atr <= self.target_1_atr:
            raise ValueError("empirical target 2 must exceed target 1")
        return self


def _effective_sample(values: Sequence[float]) -> float:
    if len(values) < 3:
        return float(len(values))
    mean = statistics.fmean(values)
    centered = [value - mean for value in values]
    denominator = sum(value * value for value in centered)
    if denominator <= 0:
        return 1.0
    rho = sum(left * right for left, right in zip(centered[:-1], centered[1:], strict=True)) / denominator
    rho = min(max(rho, -0.99), 0.99)
    estimate = len(values) * (1 - rho) / (1 + rho)
    return min(max(estimate, 1.0), float(len(values)))


def select_empirical_levels(
    outcomes: Sequence[TradeOutcome],
    *,
    direction: Direction,
    minimum_effective_sample: int = 100,
    confidence_z: float = 1.96,
) -> EmpiricalLevelEvidence | None:
    selected = [item for item in outcomes if item.direction == direction.value]
    if len(selected) < minimum_effective_sample or minimum_effective_sample < 2:
        return None
    returns = [item.net_return for item in selected]
    if any(not math.isfinite(value) for value in returns):
        return None
    effective = _effective_sample(returns)
    if effective < minimum_effective_sample:
        return None
    mean = statistics.fmean(returns)
    deviation = statistics.stdev(returns) if len(returns) > 1 else 0.0
    lower = mean - confidence_z * deviation / math.sqrt(effective)
    if lower <= 0:
        return None
    adverse = np.asarray([item.maximum_adverse_excursion_r for item in selected], dtype=float)
    favourable = np.asarray([item.maximum_favourable_excursion_r for item in selected], dtype=float)
    stop_atr = max(float(np.quantile(adverse, 0.8)), 0.25)
    target_1_atr = max(float(np.quantile(favourable, 0.5)), 0.25)
    target_2_atr = max(float(np.quantile(favourable, 0.8)), target_1_atr + 0.25)
    return EmpiricalLevelEvidence(
        sample_size=len(selected),
        effective_sample_size=Decimal(str(effective)),
        stop_atr=Decimal(str(stop_atr)),
        target_1_atr=Decimal(str(target_1_atr)),
        target_2_atr=Decimal(str(target_2_atr)),
        success_probability=Decimal(str(statistics.fmean(item.target_before_stop for item in selected))),
        expected_net_edge=Decimal(str(mean)),
        lower_net_edge=Decimal(str(lower)),
    )


def _floor_tick(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _ceil_tick(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def plan_trade_levels(
    quote: MarketQuote,
    direction: Direction,
    *,
    atr: Decimal,
    structural_invalidation: Decimal,
    expected_targets: tuple[Decimal, ...],
    empirical_evidence: EmpiricalLevelEvidence | None = None,
    decision_interval: str,
    decision_time,
    policy: TradeLevelPolicy,
    identity_context: dict[str, object] | None = None,
) -> TradePlan | None:
    if atr <= 0 or structural_invalidation <= 0 or decision_interval not in _INTERVAL_MINUTES:
        return None
    if any(not target.is_finite() or target <= 0 for target in expected_targets):
        return None
    if empirical_evidence is not None and empirical_evidence.lower_net_edge <= 0:
        return None

    tick = quote.tick_size
    spread = quote.ask - quote.bid
    chase = policy.maximum_chase_bps / Decimal(10_000)
    if direction is Direction.LONG:
        entry_low = _ceil_tick(quote.ask, tick)
        entry_high = _ceil_tick(quote.ask * (Decimal(1) + chase), tick)
        if empirical_evidence is None:
            stop = _floor_tick(min(structural_invalidation, entry_low - policy.atr_multiplier * atr), tick)
        else:
            stop = _floor_tick(entry_low - empirical_evidence.stop_atr * atr, tick)
            expected_targets = (
                entry_low + empirical_evidence.target_1_atr * atr,
                entry_low + empirical_evidence.target_2_atr * atr,
            )
        risk = entry_low - stop
        first_threshold = entry_low + risk * policy.minimum_target_1_r
        second_threshold = entry_low + risk * policy.minimum_target_2_r
        candidates = sorted({_ceil_tick(item, tick) for item in expected_targets if item > entry_high})
        target_1 = next((item for item in candidates if item >= first_threshold), None)
        target_2 = next((item for item in candidates if item >= second_threshold and item > (target_1 or 0)), None)
    else:
        entry_high = _floor_tick(quote.bid, tick)
        entry_low = _floor_tick(quote.bid * (Decimal(1) - chase), tick)
        if empirical_evidence is None:
            stop = _ceil_tick(max(structural_invalidation, entry_high + policy.atr_multiplier * atr), tick)
        else:
            stop = _ceil_tick(entry_high + empirical_evidence.stop_atr * atr, tick)
            expected_targets = (
                entry_high - empirical_evidence.target_1_atr * atr,
                entry_high - empirical_evidence.target_2_atr * atr,
            )
        risk = stop - entry_high
        first_threshold = entry_high - risk * policy.minimum_target_1_r
        second_threshold = entry_high - risk * policy.minimum_target_2_r
        candidates = sorted({_floor_tick(item, tick) for item in expected_targets if item < entry_low}, reverse=True)
        target_1 = next((item for item in candidates if item <= first_threshold), None)
        target_2 = next(
            (item for item in candidates if item <= second_threshold and item < (target_1 or Decimal("Infinity"))), None
        )

    if risk <= 0 or risk > atr * policy.maximum_stop_atr:
        return None
    if spread > 0 and risk < spread * policy.minimum_stop_noise_multiple:
        return None
    if target_1 is None or target_2 is None:
        return None

    reward_1 = target_1 - entry_low if direction is Direction.LONG else entry_high - target_1
    reward_2 = target_2 - entry_low if direction is Direction.LONG else entry_high - target_2
    reward_to_risk_1 = reward_1 / risk
    reward_to_risk_2 = reward_2 / risk
    expires_at = decision_time + timedelta(minutes=_INTERVAL_MINUTES[decision_interval] * policy.expires_after_bars)
    identity = {
        "provider": quote.provider,
        "feed": quote.feed,
        "symbol": quote.symbol,
        "decision_interval": decision_interval,
        "direction": direction,
        "decision_time": decision_time,
        "entry_low": str(entry_low),
        "entry_high": str(entry_high),
        "stop": str(stop),
        "target_1": str(target_1),
        "target_2": str(target_2),
        "evidence": identity_context or {},
        "empirical_levels": empirical_evidence.model_dump(mode="json") if empirical_evidence is not None else None,
    }
    return TradePlan(
        plan_id=canonical_hash(identity),
        provider=quote.provider,
        feed=quote.feed,
        symbol=quote.symbol,
        decision_interval=decision_interval,
        direction=direction,
        decision_time=decision_time,
        expires_at=expires_at,
        entry_low=entry_low,
        entry_high=entry_high,
        stop=stop,
        target_1=target_1,
        target_2=target_2,
        risk_per_unit=risk,
        reward_to_risk_1=reward_to_risk_1,
        reward_to_risk_2=reward_to_risk_2,
        venue_note=(
            "Short execution availability is venue-dependent."
            if quote.provider == "binance" and direction is Direction.SHORT
            else None
        ),
        cohort_id=str((identity_context or {}).get("cohort_id", "0" * 64)),
        dataset_hash=str((identity_context or {}).get("dataset_hash", "0" * 64)),
        evidence_hash=str((identity_context or {}).get("evidence_hash", "0" * 64)),
        policy_hash=str((identity_context or {}).get("policy_hash", "0" * 64)),
        config_hash=str((identity_context or {}).get("config_hash", "0" * 64)),
        strategy_versions=tuple((identity_context or {}).get("strategy_versions", ())),
    )


__all__ = [
    "DEFAULT_TRADE_LEVEL_POLICY",
    "EmpiricalLevelEvidence",
    "plan_trade_levels",
    "select_empirical_levels",
]
