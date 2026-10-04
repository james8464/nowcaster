"""Fixed diagnostic hypotheses; pure, causal and separate from qualification."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.research.day_trader_context import (
    CalendarSnapshot,
    ContextObservation,
    DayTraderContextProtocol,
    extract_context,
)
from src.research.round_two_contracts import ResearchRoundProtocol, _utc
from src.research.trend_advisor import long_trade_economics
from src.strategies.types import canonical_hash

D = Decimal
HASH = r"^[0-9a-f]{64}$"


class WorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def finite_utc(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return _utc(value, "workflow time")
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError("workflow values must be finite")
        return value


class WorkflowPolicy(WorkflowModel):
    """A manifest identity, never tuned from the account's outcomes."""

    version: Literal["diagnostic-trader-v1"] = "diagnostic-trader-v1"
    round_protocol: ResearchRoundProtocol
    initial_cash: Literal[D("10000")] = D("10000")
    exposure_fraction: Literal[D(".25")] = D(".25")
    risk_fraction: Literal[D(".005")] = D(".005")
    volume_participation: Literal[D(".01")] = D(".01")
    daily_loss_fraction: Literal[D(".02")] = D(".02")
    maximum_daily_entries: Literal[6] = 6
    losing_close_limit: Literal[3] = 3
    cooldown_seconds: Literal[3600] = 3600
    intent_seconds: Literal[60] = 60
    maximum_holding_seconds: Literal[3600] = 3600
    breakout_bars: Literal[20] = 20
    stop_bars: Literal[10] = 10
    atr_bars: Literal[14] = 14
    atr_multiplier: Literal[D("1.5")] = D("1.5")
    reward_multiple: Literal[D("2")] = D("2")
    minimum_trailing_quote_volume: Literal[D("10000")] = D("10000")
    btc_lot_step: Literal[D(".00001")] = D(".00001")
    eth_lot_step: Literal[D(".0001")] = D(".0001")
    history_limit: int = Field(default=200, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_round(self):
        DayTraderContextProtocol(round_protocol=self.round_protocol.validated())
        for name in ("fee_bps", "slippage_bps", "maximum_spread_bps"):
            value = getattr(self.round_protocol, name)
            if not value.is_finite() or value >= 10000:
                raise ValueError("invalid workflow cost/spread assumptions")
        return self

    @property
    def identity_hash(self) -> str:
        return canonical_hash(type(self).model_validate(self.model_dump()).model_dump(mode="json"))

    @property
    def source_identity_hash(self) -> str:
        return canonical_hash(self.round_protocol.source.model_dump(mode="json"))

    @property
    def context_protocol(self) -> DayTraderContextProtocol:
        return DayTraderContextProtocol(round_protocol=self.round_protocol)

    def lot_step(self, symbol: str) -> Decimal:
        if symbol not in self.round_protocol.symbols:
            raise ValueError("unsupported workflow symbol")
        return self.btc_lot_step if symbol == "BTCUSDT" else self.eth_lot_step


class WorkflowDecision(WorkflowModel):
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    status: Literal["ready", "watching", "blocked"]
    reasons: tuple[str, ...]
    decision_at: datetime
    expires_at: datetime
    protocol_hash: str = Field(pattern=HASH)
    policy_hash: str = Field(pattern=HASH)
    source_identity_hash: str = Field(pattern=HASH)
    source_key: str | None = None
    observation_at: datetime | None = None
    observation_hash: str | None = Field(default=None, pattern=HASH)
    context_hash: str | None = Field(default=None, pattern=HASH)
    session: str | None = None
    calendar_available: bool = False
    rank_score: Decimal | None = Field(default=None, ge=0)
    setup: Literal["breakout", "pullback_reclaim"] | None = None
    trigger_level: Decimal | None = Field(default=None, gt=0)
    entry: Decimal | None = Field(default=None, gt=0)
    stop: Decimal | None = Field(default=None, gt=0)
    target: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def ready_contract(self):
        if self.status == "ready" and (
            self.reasons
            or any(
                getattr(self, name) is None
                for name in (
                    "setup",
                    "trigger_level",
                    "entry",
                    "stop",
                    "target",
                    "source_key",
                    "observation_at",
                    "observation_hash",
                    "context_hash",
                )
            )
        ):
            raise ValueError("ready decision requires complete setup provenance")
        if (
            self.entry is not None
            and self.stop is not None
            and self.target is not None
            and not self.stop < self.entry < self.target
        ):
            raise ValueError("invalid setup levels")
        if self.expires_at < self.decision_at:
            raise ValueError("invalid decision expiry")
        return self

    @property
    def decision_id(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


def parse_observations(observations, policy: WorkflowPolicy) -> tuple[ContextObservation, ...]:
    rows = tuple(
        ContextObservation.model_validate(item.model_dump() if isinstance(item, BaseModel) else item)
        for item in observations
    )
    for row in rows:
        row.validate_for(policy.round_protocol)
        for name in ("open", "high", "low", "close", "bid", "ask", "volume", "bid_size", "ask_size"):
            value = getattr(row, name)
            if value is not None and not value.is_finite():
                raise ValueError("observation requires finite values")
    return rows


def select_setups(
    round_protocol: ResearchRoundProtocol,
    observations: Sequence[ContextObservation | Mapping[str, Any]],
    calendar: CalendarSnapshot | Mapping[str, Any] | None,
    now: datetime,
    policy: WorkflowPolicy,
) -> tuple[WorkflowDecision, ...]:
    """Return every declared asset, ranked by trailing volatility/spread.

    Trigger and stop windows exclude the current candle. Neither the score nor
    the cost screen claims confidence, profitability or qualification.
    """
    now = _utc(now, "now")
    policy = WorkflowPolicy.model_validate(policy.model_dump())
    if round_protocol.identity_hash != policy.round_protocol.identity_hash:
        raise ValueError("workflow protocol identity mismatch")
    parsed = parse_observations(observations, policy)
    decisions = []
    for symbol in round_protocol.symbols:
        rows = sorted(
            (r for r in parsed if r.symbol == symbol and r.available_at <= now),
            key=lambda r: (r.provider_at, r.source_key),
        )
        common = dict(
            symbol=symbol,
            decision_at=now,
            expires_at=now + timedelta(seconds=policy.intent_seconds),
            protocol_hash=round_protocol.identity_hash,
            policy_hash=policy.identity_hash,
            source_identity_hash=policy.source_identity_hash,
        )
        if not rows:
            decisions.append(WorkflowDecision(**common, status="blocked", reasons=("observations_unavailable",)))
            continue
        context = extract_context(policy.context_protocol, rows, now, calendar)
        # Context has already bound all conflicts. De-duplicate the feature window.
        unique = {r.provider_at: r for r in rows}
        window = list(unique.values())[-round_protocol.maximum_feature_bars :]
        latest, prior = window[-1], window[:-1]
        reasons = list(context.exclusions)
        if (
            latest.bid_size is not None
            and latest.ask_size is not None
            and min(latest.bid_size, latest.ask_size) < policy.lot_step(symbol)
        ):
            reasons.append("quote_size_below_lot")
        directions = [t.direction for t in context.trends if t.timeframe_minutes in (5, 15)]
        if directions != ["up", "up"]:
            reasons.append("trend_not_aligned_up")
        elif any(t.strength is None or t.strength < D(".5") for t in context.trends if t.timeframe_minutes in (5, 15)):
            reasons.append("range_regime")
        if len(prior) < 20:
            reasons.append("setup_history_unavailable")
        trailing = prior[-20:]
        if len(trailing) == 20 and all(r.volume is not None and r.close is not None for r in trailing):
            if sum(r.volume * r.close for r in trailing) / 20 < policy.minimum_trailing_quote_volume:
                reasons.append("trailing_liquidity_insufficient")
        else:
            reasons.append("trailing_liquidity_unavailable")
        score = None
        if context.realized_volatility_bps is not None and context.spread_bps is not None:
            score = context.realized_volatility_bps / max(context.spread_bps, D(".01"))
        common.update(
            source_key=latest.source_key,
            observation_at=latest.provider_at,
            observation_hash=canonical_hash(latest.model_dump(mode="json")),
            context_hash=context.feature_hash,
            session=context.session,
            calendar_available=context.calendar_blackout is not None,
            rank_score=score,
        )
        levels = {}
        if not reasons:
            breakout = max(r.high for r in trailing)
            mean = sum(r.close for r in trailing) / 20
            setup, trigger = None, None
            if latest.close > breakout:
                setup, trigger = "breakout", breakout
            elif latest.low <= mean < latest.close:
                setup, trigger = "pullback_reclaim", mean
            if setup:
                atr_rows = prior[-policy.atr_bars - 1 :]
                ranges = [
                    max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
                    for a, b in zip(atr_rows, atr_rows[1:], strict=False)
                ]
                atr = sum(ranges) / len(ranges)
                entry = latest.ask
                stop = min(min(r.low for r in prior[-policy.stop_bars :]), entry - policy.atr_multiplier * atr)
                target = entry + policy.reward_multiple * (entry - stop)
                if stop <= 0 or not stop < latest.bid <= entry < target:
                    reasons.append("invalid_setup_levels")
                else:
                    economics = long_trade_economics(
                        bid=latest.bid,
                        ask=entry,
                        stop=stop,
                        target=target,
                        fee_bps=round_protocol.fee_bps,
                        slippage_bps=round_protocol.slippage_bps,
                    )
                    reasons.extend(economics.reasons)
                    levels = dict(setup=setup, trigger_level=trigger, entry=entry, stop=stop, target=target)
            else:
                reasons.append("setup_not_triggered")
        status = "watching" if reasons == ["setup_not_triggered"] else "blocked" if reasons else "ready"
        decisions.append(WorkflowDecision(**common, **levels, status=status, reasons=tuple(dict.fromkeys(reasons))))
    return tuple(sorted(decisions, key=lambda d: (d.status == "blocked", -(d.rank_score or D(0)), d.symbol)))
