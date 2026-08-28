from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TradeDirection = Literal["long", "short"]


@dataclass(frozen=True, slots=True)
class BarrierPolicy:
    target_r: float
    stop_r: float
    maximum_bars: int
    round_trip_cost_bps: float = 0.0
    risk_column: str = "atr"

    def __post_init__(self) -> None:
        if not math.isfinite(self.target_r) or not math.isfinite(self.stop_r) or self.target_r <= 0 or self.stop_r <= 0:
            raise ValueError("target and stop multiples must be positive and finite")
        if self.maximum_bars < 1:
            raise ValueError("maximum bars must be positive")
        if not math.isfinite(self.round_trip_cost_bps) or self.round_trip_cost_bps < 0:
            raise ValueError("round-trip cost must be non-negative and finite")
        if not self.risk_column.strip():
            raise ValueError("risk column must not be empty")


class TradeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    provider: str
    feed: str
    symbol: str
    direction: TradeDirection
    decision_timestamp: datetime
    entry_timestamp: datetime
    exit_timestamp: datetime
    outcome_available_at: datetime
    entry_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    target_price: float = Field(gt=0)
    exit_price: float = Field(gt=0)
    risk_distance: float = Field(gt=0)
    target_before_stop: bool
    exit_reason: Literal["target", "stop", "ambiguous_stop_first", "expired"]
    gross_return: float
    net_return: float
    maximum_favourable_excursion_r: float = Field(ge=0)
    maximum_adverse_excursion_r: float = Field(ge=0)
    bars_held: int = Field(ge=1)

    @field_validator("decision_timestamp", "entry_timestamp", "exit_timestamp", "outcome_available_at")
    @classmethod
    def explicit_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("outcome timestamps must be explicit UTC")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def causal_order(self) -> TradeOutcome:
        if not self.decision_timestamp <= self.entry_timestamp <= self.exit_timestamp <= self.outcome_available_at:
            raise ValueError("outcome timestamps must be causally ordered")
        return self


def _utc_series(frame: pd.DataFrame, name: str) -> pd.Series:
    values = frame[name]
    for value in values.dropna():
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            raise ValueError(f"{name} must contain explicit UTC timestamps")
    return pd.to_datetime(values, utc=True)


def _validated_bars(frame: pd.DataFrame, policy: BarrierPolicy) -> pd.DataFrame:
    required = {
        "open_timestamp",
        "close_timestamp",
        "available_at",
        "open",
        "high",
        "low",
        "close",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"trade outcome bars are missing fields: {sorted(missing)}")
    result = frame.copy()
    for name in ("open_timestamp", "close_timestamp", "available_at"):
        result[name] = _utc_series(result, name)
    result = result.sort_values("open_timestamp", kind="stable").reset_index(drop=True)
    if result["open_timestamp"].duplicated().any():
        raise ValueError("trade outcome bars must have unique open timestamps")
    if not (result["open_timestamp"] < result["close_timestamp"]).all():
        raise ValueError("bar close must follow bar open")
    if not (result["available_at"] >= result["close_timestamp"]).all():
        raise ValueError("bars cannot be available before close")
    for name in ("open", "high", "low", "close"):
        result[name] = pd.to_numeric(result[name], errors="raise")
        if not result[name].map(math.isfinite).all() or not (result[name] > 0).all():
            raise ValueError("trade outcome prices must be positive and finite")
    if not (
        (result["high"] >= result[["open", "close"]].max(axis=1))
        & (result["low"] <= result[["open", "close"]].min(axis=1))
        & (result["high"] >= result["low"])
    ).all():
        raise ValueError("trade outcome bars contain impossible OHLC values")
    if policy.risk_column in result:
        result["_risk"] = pd.to_numeric(result[policy.risk_column], errors="raise")
    else:
        result["_risk"] = result["high"] - result["low"]
    if not result["_risk"].map(math.isfinite).all() or not (result["_risk"] > 0).all():
        raise ValueError("risk distance must be positive and finite")
    return result


def _directional_levels(
    entry: float, risk: float, policy: BarrierPolicy, direction: TradeDirection
) -> tuple[float, float]:
    if direction == "long":
        return entry - policy.stop_r * risk, entry + policy.target_r * risk
    return entry + policy.stop_r * risk, entry - policy.target_r * risk


def _label_one(
    bars: pd.DataFrame,
    decision_index: int,
    policy: BarrierPolicy,
    direction: TradeDirection,
) -> TradeOutcome:
    decision = bars.iloc[decision_index]
    window = bars.iloc[decision_index + 1 : decision_index + 1 + policy.maximum_bars]
    entry = float(window.iloc[0]["open"])
    risk = float(decision["_risk"])
    stop, target = _directional_levels(entry, risk, policy, direction)
    if stop <= 0 or target <= 0:
        raise ValueError("barrier prices must remain positive")

    maximum_favourable = 0.0
    maximum_adverse = 0.0
    exit_price = float(window.iloc[-1]["close"])
    exit_reason: Literal["target", "stop", "ambiguous_stop_first", "expired"] = "expired"
    exit_row = window.iloc[-1]
    bars_held = len(window)
    for ordinal, (_, row) in enumerate(window.iterrows(), start=1):
        opening = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        if direction == "long":
            maximum_favourable = max(maximum_favourable, (high - entry) / risk)
            maximum_adverse = max(maximum_adverse, (entry - low) / risk)
            stop_touched = low <= stop
            target_touched = high >= target
            adverse_exit = min(stop, opening) if opening <= stop else stop
        else:
            maximum_favourable = max(maximum_favourable, (entry - low) / risk)
            maximum_adverse = max(maximum_adverse, (high - entry) / risk)
            stop_touched = high >= stop
            target_touched = low <= target
            adverse_exit = max(stop, opening) if opening >= stop else stop
        if stop_touched and target_touched:
            exit_price = adverse_exit
            exit_reason = "ambiguous_stop_first"
        elif stop_touched:
            exit_price = adverse_exit
            exit_reason = "stop"
        elif target_touched:
            exit_price = target
            exit_reason = "target"
        else:
            continue
        exit_row = row
        bars_held = ordinal
        break

    sign = 1.0 if direction == "long" else -1.0
    gross_return = sign * (exit_price / entry - 1.0)
    net_return = gross_return - policy.round_trip_cost_bps / 10_000
    return TradeOutcome(
        provider=str(decision.get("provider", "unknown")),
        feed=str(decision.get("feed", "unknown")),
        symbol=str(decision.get("symbol", "unknown")).upper(),
        direction=direction,
        decision_timestamp=pd.Timestamp(decision["close_timestamp"]).to_pydatetime(),
        entry_timestamp=pd.Timestamp(window.iloc[0]["open_timestamp"]).to_pydatetime(),
        exit_timestamp=pd.Timestamp(exit_row["close_timestamp"]).to_pydatetime(),
        outcome_available_at=pd.Timestamp(exit_row["available_at"]).to_pydatetime(),
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        exit_price=exit_price,
        risk_distance=risk,
        target_before_stop=exit_reason == "target",
        exit_reason=exit_reason,
        gross_return=gross_return,
        net_return=net_return,
        maximum_favourable_excursion_r=maximum_favourable,
        maximum_adverse_excursion_r=maximum_adverse,
        bars_held=bars_held,
    )


def label_trade_outcomes(
    bars: pd.DataFrame,
    policy: BarrierPolicy,
    *,
    directions: tuple[TradeDirection, ...] = ("long", "short"),
) -> tuple[TradeOutcome, ...]:
    if not directions or any(direction not in {"long", "short"} for direction in directions):
        raise ValueError("directions must contain long or short")
    frame = _validated_bars(bars, policy)
    outcomes: list[TradeOutcome] = []
    for decision_index in range(max(len(frame) - 1, 0)):
        if frame.iloc[decision_index + 1 : decision_index + 1 + policy.maximum_bars].empty:
            continue
        for direction in directions:
            outcomes.append(_label_one(frame, decision_index, policy, direction))
    return tuple(outcomes)


__all__ = ["BarrierPolicy", "TradeDirection", "TradeOutcome", "label_trade_outcomes"]
