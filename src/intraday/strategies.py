"""Fixed, causal intraday paper strategy families."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.intraday.contracts import ConfirmedBar, MarketQuote
from src.strategies.types import canonical_hash

D = Decimal


class SetupDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    status: Literal["ready", "no_trade"]
    strategy_id: str
    direction: Literal["long", "short"] | None = None
    reason: str
    decision_at: datetime
    entry_at: datetime | None = None
    entry: Decimal | None = Field(default=None, gt=0)
    stop: Decimal | None = Field(default=None, gt=0)
    target: Decimal | None = Field(default=None, gt=0)
    exit_by: datetime | None = None
    estimated_roundtrip_cost: Decimal | None = Field(default=None, ge=0)
    evidence_hash: str | None = None

    @model_validator(mode="after")
    def valid_levels(self):
        if self.status == "ready":
            required = (
                "direction",
                "entry_at",
                "entry",
                "stop",
                "target",
                "exit_by",
                "estimated_roundtrip_cost",
                "evidence_hash",
            )
            if any(getattr(self, key) is None for key in required):
                raise ValueError("ready plan needs complete levels and provenance")
            if self.entry_at <= self.decision_at or self.exit_by <= self.entry_at:
                raise ValueError("invalid plan timing")
            if self.direction == "long" and not self.stop < self.entry < self.target:
                raise ValueError("invalid long plan levels")
            if self.direction == "short" and not self.target < self.entry < self.stop:
                raise ValueError("invalid short plan levels")
        return self


def _no_trade(strategy_id: str, reason: str, decision_at: datetime) -> SetupDecision:
    return SetupDecision(status="no_trade", strategy_id=strategy_id, reason=reason, decision_at=decision_at)


def _ema(values: list[Decimal], length: int) -> Decimal:
    result = values[0]
    alpha = D(2) / D(length + 1)
    for value in values[1:]:
        result += alpha * (value - result)
    return result


def _atr(bars: list[ConfirmedBar], length: int = 14) -> Decimal:
    pairs = list(zip(bars[-length - 1 : -1], bars[-length:], strict=False))
    if len(pairs) != length:
        return D(0)
    return (
        sum(
            max(
                current.ask_high - current.bid_low,
                abs(current.ask_high - previous.bid_close),
                abs(current.bid_low - previous.ask_close),
            )
            for previous, current in pairs
        )
        / length
    )


def evaluate_setup(
    strategy_id: str,
    bars: list[ConfirmedBar] | tuple[ConfirmedBar, ...],
    quote: MarketQuote,
    *,
    session_open: datetime,
    session_close: datetime,
) -> SetupDecision:
    """Evaluate only data confirmed before the first actionable account quote."""
    if not bars:
        return _no_trade(strategy_id, "missing_bars", quote.observed_at)
    latest = bars[-1]
    decision_at = max(latest.end, latest.available_at)
    if quote.instrument != latest.instrument or any(bar.instrument != latest.instrument for bar in bars):
        return _no_trade(strategy_id, "instrument_mismatch", decision_at)
    if any(bar.end > decision_at or bar.available_at > decision_at for bar in bars):
        return _no_trade(strategy_id, "bar_unavailable", decision_at)
    if any(a.end != b.start for a, b in zip(bars, bars[1:], strict=False)):
        return _no_trade(strategy_id, "bar_gap", decision_at)
    if (
        quote.status != "tradeable"
        or quote.observed_at <= decision_at
        or quote.received_at - decision_at > timedelta(seconds=15)
        or not session_open < decision_at < session_close
    ):
        return _no_trade(strategy_id, "quote_or_session_unavailable", decision_at)
    if strategy_id not in {"opening_range_15", "opening_range_30", "trend_pullback", "range_reversion"}:
        return _no_trade(strategy_id, "unknown_strategy", decision_at)
    support: list[ConfirmedBar]
    if strategy_id.startswith("opening_range_"):
        length = 15 if strategy_id.endswith("15") else 30
        range_end = session_open + timedelta(minutes=length)
        opening = [bar for bar in bars if session_open <= bar.start and bar.end <= range_end]
        if len(opening) != length // 5 or latest.start != range_end:
            return _no_trade(strategy_id, "opening_range_unavailable", decision_at)
        high = max(bar.bid_high for bar in opening)
        low = min(bar.ask_low for bar in opening)
        support = opening + [latest]
        if latest.bid_close > high:
            direction = "long"
            entry = quote.ask
            stop = min(bar.bid_low for bar in opening)
            risk = entry - stop
            target = entry + risk * 2
        elif latest.ask_close < low:
            direction = "short"
            entry = quote.bid
            stop = max(bar.ask_high for bar in opening)
            risk = stop - entry
            target = entry - risk * 2
        else:
            return _no_trade(strategy_id, "no_confirmed_breakout", decision_at)
        reason = f"Confirmed {length}-minute opening-range {direction} breakout; paper only."
    elif strategy_id == "trend_pullback":
        if len(bars) < 62:
            return _no_trade(strategy_id, "trend_history_unavailable", decision_at)
        support = list(bars[-62:])
        closes = [(bar.bid_close + bar.ask_close) / 2 for bar in bars[:-1]]
        ema20, ema60 = _ema(closes, 20), _ema(closes, 60)
        atr = _atr(list(bars[:-1]))
        previous = bars[-2]
        if atr <= 0:
            return _no_trade(strategy_id, "atr_unavailable", decision_at)
        if ema20 > ema60 and previous.bid_low <= ema20 + atr / 2 and latest.bid_close > previous.bid_high:
            direction, entry = "long", quote.ask
            stop = min(previous.bid_low, entry - D("1.5") * atr)
            risk = entry - stop
            target = entry + 2 * risk
        elif ema20 < ema60 and previous.ask_high >= ema20 - atr / 2 and latest.ask_close < previous.ask_low:
            direction, entry = "short", quote.bid
            stop = max(previous.ask_high, entry + D("1.5") * atr)
            risk = stop - entry
            target = entry - 2 * risk
        else:
            return _no_trade(strategy_id, "trend_reclaim_absent", decision_at)
        reason = f"Confirmed volatility-scaled {direction} trend reclaim; paper only."
    else:
        if len(bars) < 21:
            return _no_trade(strategy_id, "range_history_unavailable", decision_at)
        support = list(bars[-21:])
        closes = [(bar.bid_close + bar.ask_close) / 2 for bar in bars[-21:-1]]
        mean = sum(closes) / len(closes)
        variance = sum((value - mean) ** 2 for value in closes) / len(closes)
        volatility = variance.sqrt()
        travel = sum(abs(b - a) for a, b in zip(closes, closes[1:], strict=False))
        efficiency = abs(closes[-1] - closes[0]) / travel if travel else D(1)
        if volatility <= 0 or efficiency > D("0.35"):
            return _no_trade(strategy_id, "not_range_regime", decision_at)
        atr = _atr(list(bars[:-1]))
        if (latest.bid_close + latest.ask_close) / 2 < mean - 2 * volatility:
            direction, entry = "long", quote.ask
            stop = min(latest.bid_low, entry - D("1.5") * atr)
            risk = entry - stop
            target = mean
        elif (latest.bid_close + latest.ask_close) / 2 > mean + 2 * volatility:
            direction, entry = "short", quote.bid
            stop = max(latest.ask_high, entry + D("1.5") * atr)
            risk = stop - entry
            target = mean
        else:
            return _no_trade(strategy_id, "range_extreme_absent", decision_at)
        reason = f"Confirmed range-day {direction} mean reversion; paper only."
    if (
        min(entry, stop, target, risk) <= 0
        or (direction == "long" and not stop < entry < target)
        or (direction == "short" and not target < entry < stop)
    ):
        return _no_trade(strategy_id, "invalid_levels", decision_at)
    exit_by = min(session_close, quote.received_at + timedelta(hours=1))
    if exit_by <= quote.received_at:
        return _no_trade(strategy_id, "session_closing", decision_at)
    return SetupDecision(
        status="ready",
        strategy_id=strategy_id,
        direction=direction,
        reason=reason,
        decision_at=decision_at,
        entry_at=quote.received_at,
        entry=entry,
        stop=stop,
        target=target,
        exit_by=exit_by,
        estimated_roundtrip_cost=quote.spread,
        evidence_hash=canonical_hash(
            {"bars": [bar.model_dump(mode="json") for bar in support], "quote": quote.model_dump(mode="json")}
        ),
    )
