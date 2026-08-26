from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.strategies.types import canonical_hash

BarIntervalValue = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"


class MonitorHealth(StrEnum):
    STOPPED = "stopped"
    WARMING = "warming"
    HEALTHY = "healthy"
    RECONNECTING = "reconnecting"
    STALE = "stale"
    PAUSED = "paused"
    FAILED = "failed"


class AlertState(StrEnum):
    WATCHING = "watching"
    CANDIDATE = "candidate"
    ENTRY_ALERTED = "entry_alerted"
    TRACKED = "tracked"
    UNTRACKED = "untracked"
    TARGET_1 = "target_1"
    TARGET_2 = "target_2"
    STOPPED = "stopped"
    CLOSED = "closed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class LiveMonitorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def finite_numbers_and_utc_datetimes(self) -> LiveMonitorModel:
        def visit(value: Any, path: str) -> None:
            if isinstance(value, datetime):
                if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
                    raise ValueError(f"{path} must be an explicit UTC datetime")
            elif (isinstance(value, Decimal) and not value.is_finite()) or (
                isinstance(value, float) and not math.isfinite(value)
            ):
                raise ValueError(f"{path} must be finite")
            elif isinstance(value, dict):
                for key, item in value.items():
                    visit(item, f"{path}.{key}")
            elif isinstance(value, (list, tuple)):
                for index, item in enumerate(value):
                    visit(item, f"{path}[{index}]")

        for name in type(self).model_fields:
            visit(getattr(self, name), name)
        return self


class MarketBar(LiveMonitorModel):
    provider: str = Field(min_length=1, max_length=32)
    feed: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=1, max_length=32)
    interval: BarIntervalValue
    start: datetime
    end: datetime
    available_at: datetime
    received_at: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    finalized: Literal[True]
    revision: int = Field(ge=0)
    repair_verified: bool = False

    @field_validator("provider", "feed")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_bar(self) -> MarketBar:
        if self.end <= self.start:
            raise ValueError("bar end must follow start")
        if self.available_at < self.end:
            raise ValueError("bar cannot be available before its end")
        if self.received_at < self.end:
            raise ValueError("bar cannot be received before its end")
        if self.high < max(self.open, self.low, self.close) or self.low > min(self.open, self.high, self.close):
            raise ValueError("bar has impossible OHLC values")
        return self

    @property
    def bar_id(self) -> str:
        return canonical_hash(
            {
                "provider": self.provider,
                "feed": self.feed,
                "symbol": self.symbol,
                "interval": self.interval,
                "start": self.start,
                "end": self.end,
                "available_at": self.available_at,
                "open": str(self.open),
                "high": str(self.high),
                "low": str(self.low),
                "close": str(self.close),
                "volume": str(self.volume),
                "revision": self.revision,
                "repair_verified": self.repair_verified,
            }
        )


class MarketQuote(LiveMonitorModel):
    provider: str = Field(min_length=1, max_length=32)
    feed: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=1, max_length=32)
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    last: Decimal = Field(gt=0)
    tick_size: Decimal = Field(gt=0)
    provider_time: datetime
    received_at: datetime

    @field_validator("provider", "feed")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def quote_is_not_crossed(self) -> MarketQuote:
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")
        return self


class TradeLevelPolicy(LiveMonitorModel):
    atr_multiplier: Decimal = Field(gt=0)
    maximum_chase_bps: Decimal = Field(ge=0)
    maximum_stop_atr: Decimal = Field(gt=0)
    minimum_stop_noise_multiple: Decimal = Field(gt=0)
    minimum_target_1_r: Decimal = Field(gt=0)
    minimum_target_2_r: Decimal = Field(gt=0)
    expires_after_bars: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def targets_are_ordered(self) -> TradeLevelPolicy:
        if self.minimum_target_2_r <= self.minimum_target_1_r:
            raise ValueError("target 2 reward must exceed target 1 reward")
        return self


class TradePlan(LiveMonitorModel):
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(min_length=1, max_length=32)
    feed: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=1, max_length=32)
    decision_interval: BarIntervalValue
    direction: Direction
    decision_time: datetime
    expires_at: datetime
    entry_low: Decimal = Field(gt=0)
    entry_high: Decimal = Field(gt=0)
    stop: Decimal = Field(gt=0)
    target_1: Decimal = Field(gt=0)
    target_2: Decimal = Field(gt=0)
    risk_per_unit: Decimal = Field(gt=0)
    reward_to_risk_1: Decimal = Field(gt=0)
    reward_to_risk_2: Decimal = Field(gt=0)
    venue_note: str | None = Field(default=None, max_length=256)
    cohort_id: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    dataset_hash: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    evidence_hash: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    strategy_versions: tuple[tuple[str, str], ...] = ()

    @model_validator(mode="after")
    def validate_geometry(self) -> TradePlan:
        if self.expires_at <= self.decision_time:
            raise ValueError("plan expiry must follow its decision")
        if self.entry_high < self.entry_low:
            raise ValueError("entry zone must be ordered")
        if (
            self.direction is Direction.LONG
            and not self.stop < self.entry_low <= self.entry_high < self.target_1 < self.target_2
        ):
            raise ValueError("long plan prices must be stop < entry < target 1 < target 2")
        if (
            self.direction is Direction.SHORT
            and not self.target_2 < self.target_1 < self.entry_low <= self.entry_high < self.stop
        ):
            raise ValueError("short plan prices must be target 2 < target 1 < entry < stop")
        if self.reward_to_risk_2 <= self.reward_to_risk_1:
            raise ValueError("target 2 reward must exceed target 1 reward")
        return self


class LifecycleEvent(LiveMonitorModel):
    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    setup_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_state: AlertState
    occurred_at: datetime
    reason: str = Field(min_length=1, max_length=256)
    actual_fill: Decimal | None = Field(default=None, gt=0)


class LifecycleTransition(LiveMonitorModel):
    transition_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    setup_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    from_state: AlertState
    to_state: AlertState
    occurred_at: datetime
    reason: str = Field(min_length=1, max_length=256)
    actual_fill: Decimal | None = Field(default=None, gt=0)


class ProviderHealthEvent(LiveMonitorModel):
    provider: str = Field(min_length=1, max_length=32)
    feed: str = Field(min_length=1, max_length=32)
    status: MonitorHealth
    reason: str = Field(min_length=1, max_length=128)
    occurred_at: datetime


MarketEvent = MarketBar | MarketQuote | ProviderHealthEvent


def _bounded_payload(value: Any) -> Any:
    nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 50_000:
            raise ValueError("payload exceeds maximum node count")
        if depth > 16:
            raise ValueError("payload exceeds maximum depth")
        if isinstance(item, str) and len(item.encode()) > 16 * 1024:
            raise ValueError("payload string exceeds maximum size")
        if isinstance(item, dict):
            if len(item) > 2_000:
                raise ValueError("payload collection exceeds maximum size")
            for key, nested in item.items():
                visit(str(key), depth + 1)
                visit(nested, depth + 1)
        elif isinstance(item, (list, tuple)):
            if len(item) > 2_000:
                raise ValueError("payload collection exceeds maximum size")
            for nested in item:
                visit(nested, depth + 1)

    visit(value, 0)
    return value


class MonitorWireEvent(LiveMonitorModel):
    schema_version: Literal[1]
    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    sequence: int = Field(ge=0)
    event_type: Literal[
        "ready",
        "heartbeat",
        "quote",
        "bar_finalized",
        "decision",
        "setup_snapshot",
        "lifecycle_transition",
        "notification_request",
        "provider_health",
        "control_ack",
        "configuration_rejected",
        "fatal_error",
    ]
    emitted_at: datetime
    payload: dict[str, Any]

    @field_validator("payload", mode="before")
    @classmethod
    def bounded_payload(cls, value: Any) -> Any:
        return _bounded_payload(value)


__all__ = [
    "AlertState",
    "Direction",
    "LifecycleEvent",
    "LifecycleTransition",
    "MarketBar",
    "MarketQuote",
    "MarketEvent",
    "MonitorHealth",
    "MonitorWireEvent",
    "ProviderHealthEvent",
    "TradeLevelPolicy",
    "TradePlan",
]
