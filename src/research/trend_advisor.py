"""Causal, deterministic research posture; no execution or notification dependencies.

The policy is fixed and hashed separately from the sealed candidate protocol.
Its extra trend filters have not themselves established an executable edge.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from src.research.round_two_contracts import ResearchRoundProtocol, RoundObservation, RoundReport, RoundStatus, _utc
from src.research.round_two_quality import QualitySummary
from src.research.round_two_walkforward import CandidateResult, _gate, _parameter_specs, _signals
from src.strategies.registry import StrategyRegistry
from src.strategies.types import canonical_hash

D = Decimal
POLICY = {
    "version": "trend-advisor-v1",
    "fast_minutes": 10,
    "slow_minutes": 30,
    "coarse_minutes": 5,
    "coarse_fast_bars": 3,
    "coarse_slow_bars": 6,
    "minimum_trend_separation": "0.001",
    "maximum_minute_volatility": "0.01",
    "minimum_quote_volume": "10000",
    "expiry_seconds": 15,
}


class TrendAdvisorSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, alias_generator=to_camel, populate_by_name=True)

    symbol: Literal["BTCUSDT", "ETHUSDT"]
    strategy_id: str = Field(min_length=1, max_length=256)
    round_id: str = Field(min_length=1, max_length=256)
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: Literal["binance:spot"] = "binance:spot"
    posture: Literal["long_research", "stand_aside"]
    paper_only: Literal[True] = True
    qualification_status: Literal["unqualified"] = "unqualified"
    timeframe: Literal["1m / 5m"] = "1m / 5m"
    decision_at: datetime
    available_at: datetime | None = None
    expires_at: datetime
    entry_low: Decimal | None = None
    entry_high: Decimal | None = None
    invalidation: Decimal | None = None
    target: Decimal | None = None
    reasons: tuple[str, ...] = Field(max_length=16)
    close_reasons: tuple[str, ...] = (
        "invalidation_reached",
        "target_reached",
        "trend_alignment_lost",
        "evidence_expired",
    )

    @field_validator("decision_at", "available_at", "expires_at")
    @classmethod
    def utc_times(cls, value):
        return None if value is None else _utc(value, "advisor timestamp")

    @model_validator(mode="after")
    def valid_posture(self):
        if self.expires_at > self.decision_at + timedelta(seconds=15):
            raise ValueError("advisor expiry must be bounded")
        if self.available_at is not None and self.available_at > self.decision_at:
            raise ValueError("advisor evidence must be available")
        if self.available_at is not None and self.expires_at > self.available_at + timedelta(seconds=15):
            raise ValueError("advisor expiry must not extend evidence freshness")
        levels = (self.invalidation, self.entry_low, self.entry_high, self.target)
        if self.posture == "long_research":
            if self.available_at is None or any(
                value is None or not value.is_finite() or value <= 0 for value in levels
            ):
                raise ValueError("long research requires finite positive levels and evidence")
            if not self.invalidation < self.entry_low <= self.entry_high < self.target:
                raise ValueError("long research levels are inconsistent")
            if self.expires_at <= self.decision_at or self.decision_at - self.available_at >= timedelta(seconds=15):
                raise ValueError("long research evidence is stale")
            if self.reasons != ("trend_aligned", "candidate_confirmed"):
                raise ValueError("long research confirmation missing")
        elif any(value is not None for value in levels):
            raise ValueError("stand aside must not carry price levels")
        if not self.reasons or any(not reason or len(reason) > 256 for reason in self.reasons + self.close_reasons):
            raise ValueError("advisor reasons must be bounded")
        if self.close_reasons != ("invalidation_reached", "target_reached", "trend_alignment_lost", "evidence_expired"):
            raise ValueError("unsupported research close reason")
        return self


class AdvisorRoundReport(RoundReport):
    trend_advisor: tuple[TrendAdvisorSuggestion, ...] = Field(default=(), max_length=100)


def _policy_hash() -> str:
    return canonical_hash({"policy": POLICY, "implementation": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


def advise(
    protocol: ResearchRoundProtocol,
    result: CandidateResult,
    quality: QualitySummary,
    observations: Sequence[RoundObservation],
    *,
    decision_at: datetime,
    registry: StrategyRegistry,
) -> TrendAdvisorSuggestion:
    """Project a retained candidate at one explicit instant, using only then-visible evidence."""
    protocol = protocol.validated()
    decision_at = _utc(decision_at, "decision_at")
    if quality.protocol.identity_hash != protocol.identity_hash:
        raise ValueError("quality evidence protocol mismatch")
    # Compare the complete supplied evidence before filtering; a favorable subset
    # must never erase a retained gap, provider error, or conflicting revision.
    if tuple(observations) != quality.observations:
        raise ValueError("observations must match retained quality evidence")
    candidate = result.candidate
    visible = tuple(row for row in observations if row.symbol == candidate.symbol and row.available_at <= decision_at)
    latest = max(visible, key=lambda row: row.available_at, default=None)
    reasons = set(quality.reasons_for(decision_at))
    ttl = timedelta(seconds=min(protocol.maximum_observation_age_seconds, 15))
    expires_at = min(decision_at + ttl, latest.available_at + ttl) if latest else decision_at + ttl
    if expires_at <= decision_at:
        reasons.add("evidence_expired")
    try:
        registry.resolve(candidate.strategy_id)
    except KeyError:
        reasons.add("candidate_confirmation_unavailable")
    if candidate.direction == "short":
        reasons.add("spot_short_unsupported")
    if candidate.direction == "abstain":
        reasons.add("candidate_abstains")
    if candidate not in protocol.candidates:
        reasons.add("candidate_identity_mismatch")
    if result.status != RoundStatus.EXPERIMENTAL_PAPER_ONLY:
        reasons.add("candidate_not_experimental")
    if result.reasons or any(
        _gate(getattr(result, phase), protocol, phase) for phase in ("train", "validation", "sealed_test")
    ):
        reasons.add("candidate_gates_failed")
    if not result.folds or any(fold.receipt is None for fold in result.folds):
        reasons.add("candidate_evidence_missing")
    elif any(fold.fold.test_end > decision_at or fold.receipt.sealed_at > decision_at for fold in result.folds):
        reasons.add("candidate_unavailable_at_decision")
    if len({row.source_key for row in visible}) != len(visible):
        reasons.add("duplicate_source_key")
    history = visible[-protocol.maximum_feature_bars :]
    for row in history:
        try:
            RoundObservation.model_validate(row.model_dump()).validate_for(protocol)
        except ValueError:
            reasons.add("invalid_observation")
        if row.available_at - row.provider_at > timedelta(seconds=protocol.maximum_observation_age_seconds):
            reasons.add("provider_latency")
        if row.close is None or row.provider_error:
            reasons.add("unfinalized_or_unhealthy_evidence")
    if any(b.provider_at - a.provider_at != timedelta(minutes=1) for a, b in zip(history, history[1:], strict=False)):
        reasons.add("continuity_warmup")
    if latest is not None:
        if latest.bid is None or latest.ask is None:
            reasons.add("quote_unavailable")
        if latest.volume is None:
            reasons.add("liquidity_unavailable")
        elif latest.close is not None and latest.volume * latest.close < D(POLICY["minimum_quote_volume"]):
            reasons.add("liquidity_below_minimum")
    if len(history) < max(protocol.warmup_minutes + 1, 36):
        reasons.add("insufficient_trend_history")
    if not reasons:
        prices = pd.Series(
            [float(row.close) for row in history], index=pd.DatetimeIndex([row.provider_at for row in history])
        )
        fast, slow = prices.rolling(10).mean(), prices.rolling(30).mean()
        # Five-minute candles end on UTC boundaries; require all five constituent
        # finalized closes and omit the currently unfinished bucket.
        grouped = prices.resample("5min", closed="right", label="right")
        coarse = grouped.last()[grouped.count().eq(5)]
        coarse = coarse[coarse.index <= latest.provider_at]
        coarse_fast, coarse_slow = coarse.rolling(3).mean(), coarse.rolling(6).mean()
        aligned = (
            len(coarse) >= 7
            and fast.iloc[-1] > slow.iloc[-1]
            and fast.iloc[-1] > fast.iloc[-2]
            and slow.iloc[-1] > slow.iloc[-2]
            and coarse_fast.iloc[-1] > coarse_slow.iloc[-1]
            and coarse_slow.iloc[-1] > coarse_slow.iloc[-2]
        )
        if not aligned:
            reasons.add("trend_not_aligned")
        if (fast.iloc[-1] - slow.iloc[-1]) / slow.iloc[-1] < float(POLICY["minimum_trend_separation"]):
            reasons.add("trend_too_weak")
        volatility = prices.pct_change().iloc[-30:].std(ddof=1)
        if not math.isfinite(volatility) or volatility > float(POLICY["maximum_minute_volatility"]):
            reasons.add("volatility_out_of_bounds")
        try:
            item = registry.resolve(candidate.strategy_id)
            specs = _parameter_specs(candidate, item)
            spec = next(spec for spec in specs if spec.parameters == result.selected_parameters)
            if not _signals(history, protocol, item, spec)[-1]:
                reasons.add("candidate_not_confirmed")
        except (KeyError, ValueError, StopIteration, TypeError, IndexError):
            reasons.add("candidate_confirmation_unavailable")
    base = dict(
        symbol=candidate.symbol,
        strategy_id=candidate.strategy_id,
        round_id=protocol.round_id,
        protocol_hash=protocol.identity_hash,
        source_hash=canonical_hash(protocol.source.model_dump(mode="json")),
        candidate_hash=canonical_hash(candidate.model_dump(mode="json")),
        evidence_hash=canonical_hash(
            {
                "candidate_result": result.model_dump(mode="json"),
                "observations": [row.model_dump(mode="json") for row in visible],
            }
        ),
        policy_hash=_policy_hash(),
        decision_at=decision_at,
        available_at=latest.available_at if latest else None,
        expires_at=expires_at,
    )
    if reasons:
        return TrendAdvisorSuggestion(**base, posture="stand_aside", reasons=tuple(sorted(reasons))[:16])
    stop = latest.ask * (1 - candidate.stop_loss_bps / D(10000))
    target = latest.ask * (1 + candidate.target_bps / D(10000))
    if not stop < latest.bid <= latest.ask < target:
        return TrendAdvisorSuggestion(**base, posture="stand_aside", reasons=("barriers_inside_spread",))
    return TrendAdvisorSuggestion(
        **base,
        posture="long_research",
        entry_low=latest.bid,
        entry_high=latest.ask,
        invalidation=stop,
        target=target,
        reasons=("trend_aligned", "candidate_confirmed"),
    )
