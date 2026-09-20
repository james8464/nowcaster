"""Immutable, paper-only contracts for Research Round 2."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from src.strategies.types import canonical_hash

SUPPORTED_SYMBOLS = ("BTCUSDT", "ETHUSDT")


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be an explicit UTC datetime")
    return value.astimezone(UTC)


class RoundSource(BaseModel):
    """Credential-free identity of one declared market-data source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    feed: str
    revision: str = "binance-spot-public-v1"

    @field_validator("provider", "feed", "revision")
    @classmethod
    def non_empty_identifier(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("source identifiers must not be empty")
        return normalized

    @model_validator(mode="after")
    def binance_spot_only(self) -> RoundSource:
        if self.provider != "binance" or self.feed != "spot":
            raise ValueError("Research Round 2 source must be Binance spot")
        return self


def _freeze_parameter(value: Any) -> Any:
    if isinstance(value, Mapping):
        ordered = sorted(value.items(), key=lambda item: str(item[0]))
        return tuple((str(key), _freeze_parameter(item)) for key, item in ordered)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_parameter(item) for item in value)
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ValueError("candidate parameters must be JSON scalar, list, or object values")


class RoundCandidate(BaseModel):
    """A retained candidate identity; status is decided only by later evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    strategy_id: str
    direction: str = "long"
    strategy_version: str = "v1"
    parameters: tuple[tuple[str, Any], ...] = ()
    stop_loss_bps: Decimal = Field(default=Decimal("100"), gt=0, lt=10000)
    target_bps: Decimal = Field(default=Decimal("150"), gt=0)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("strategy_id", "strategy_version")
    @classmethod
    def non_empty_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("candidate identifiers must not be empty")
        return normalized

    @field_validator("direction")
    @classmethod
    def normalize_direction(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"long", "abstain", "short"}:
            raise ValueError("candidate direction must be long, abstain, or short")
        return normalized

    @field_validator("parameters", mode="before")
    @classmethod
    def freeze_parameters(cls, value: Any) -> tuple[tuple[str, Any], ...]:
        if value is None:
            return ()
        if isinstance(value, Mapping):
            return _freeze_parameter(value)
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(pair, (list, tuple)) or len(pair) != 2 for pair in value
        ):
            raise ValueError("candidate parameters must be an object")
        items = [(str(pair[0]), pair[1]) for pair in value]
        keys = [key for key, _ in items]
        if len(keys) != len(set(keys)):
            raise ValueError("candidate parameter names must be unique")
        return tuple((key, _freeze_parameter(item)) for key, item in sorted(items))


class WalkForwardSchedule(BaseModel):
    """Fixed UTC-day windows. The sealed window is part of round identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    starts_at: datetime
    train_days: int = Field(default=90, gt=0)
    validation_days: int = Field(default=30, gt=0)
    sealed_test_days: int = Field(default=30, gt=0)
    step_days: int = Field(default=30, gt=0)

    @field_validator("starts_at")
    @classmethod
    def explicit_utc(cls, value: datetime) -> datetime:
        return _utc(value, "starts_at")


class RoundStatus(StrEnum):
    INSUFFICIENT_DATA = "insufficient_data"
    REJECTED = "rejected"
    EXPERIMENTAL_PAPER_ONLY = "experimental_paper_only"


class ResearchRoundProtocol(BaseModel):
    """The complete immutable identity for one paper-only research round."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    round_id: str
    source: RoundSource
    symbols: tuple[str, ...]
    candidates: tuple[RoundCandidate, ...]
    schedule: WalkForwardSchedule
    interval: Literal["1m"] = "1m"
    maximum_spread_bps: Decimal = Field(default=Decimal("25"), gt=0)
    minimum_coverage: Decimal = Field(default=Decimal("0.995"), gt=0, le=1)
    maximum_observation_age_seconds: int = Field(default=15, gt=0)
    warmup_minutes: int = Field(default=60, gt=0)
    fee_bps: Decimal = Field(default=Decimal("10"), ge=0)
    slippage_bps: Decimal = Field(default=Decimal("5"), ge=0)
    latency_ms: int = Field(default=250, ge=0)
    minimum_closed_trades: int = Field(default=100, gt=0)
    maximum_drawdown: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)
    minimum_stressed_lower_edge: Decimal = Field(default=Decimal("0"), ge=0)
    maximum_volume_participation: Decimal = Field(default=Decimal("0.01"), gt=0, le=1)
    maximum_initial_cash_exposure: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)
    maximum_feature_bars: int = Field(default=1000, gt=0)

    @field_validator("round_id")
    @classmethod
    def non_empty_round_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("round_id must not be empty")
        return normalized

    @field_validator("symbols")
    @classmethod
    def supported_unique_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(symbol.strip().upper() for symbol in value)
        if not normalized or any(symbol not in SUPPORTED_SYMBOLS for symbol in normalized):
            raise ValueError("supported symbols are BTCUSDT and ETHUSDT only")
        if len(normalized) != len(set(normalized)):
            raise ValueError("symbols must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_protocol(self) -> ResearchRoundProtocol:
        candidate_symbols = [candidate.symbol for candidate in self.candidates]
        if not self.candidates:
            raise ValueError("at least one candidate is required")
        if any(symbol not in self.symbols for symbol in candidate_symbols):
            raise ValueError("candidate symbols must be declared protocol symbols")
        candidate_ids = {
            (candidate.symbol, candidate.strategy_id, candidate.direction) for candidate in self.candidates
        }
        if len(candidate_ids) != len(self.candidates):
            raise ValueError("candidate identities must be unique")
        if any(candidate.direction == "short" for candidate in self.candidates):
            raise ValueError("spot direction must be long or abstain")
        return self

    @classmethod
    def default(cls, *, round_id: str, starts_at: datetime) -> ResearchRoundProtocol:
        return cls(
            round_id=round_id,
            source=RoundSource(provider="binance", feed="spot"),
            symbols=SUPPORTED_SYMBOLS,
            candidates=tuple(
                RoundCandidate(symbol=symbol, strategy_id="ema", direction="long") for symbol in SUPPORTED_SYMBOLS
            ),
            schedule=WalkForwardSchedule(starts_at=starts_at),
        )

    def validated(self) -> ResearchRoundProtocol:
        """Revalidate after ``model_copy`` so a changed protocol cannot bypass invariants."""
        return type(self).model_validate(self.model_dump(mode="python"))

    @property
    def identity_hash(self) -> str:
        return canonical_hash(self.validated().model_dump(mode="json"))


class RoundObservation(BaseModel):
    """A single immutable public-feed bar or quote observation for later ingestion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    feed: str
    symbol: str
    provider_at: datetime
    received_at: datetime
    available_at: datetime
    source_key: str
    interval: Literal["1m"] = "1m"
    open: Decimal | None = Field(default=None, gt=0)
    high: Decimal | None = Field(default=None, gt=0)
    low: Decimal | None = Field(default=None, gt=0)
    close: Decimal | None = Field(default=None, gt=0)
    bid: Decimal | None = Field(default=None, gt=0)
    ask: Decimal | None = Field(default=None, gt=0)
    volume: Decimal | None = Field(default=None, ge=0)
    provider_error: str | None = None

    @field_validator("provider", "feed", "source_key")
    @classmethod
    def non_empty_source_field(cls, value: str, info: ValidationInfo) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source fields must not be empty")
        return normalized if info.field_name == "source_key" else normalized.lower()

    @field_validator("symbol")
    @classmethod
    def normalize_observation_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("provider_at", "received_at", "available_at")
    @classmethod
    def explicit_observation_utc(cls, value: datetime, info: Any) -> datetime:
        return _utc(value, info.field_name)

    @model_validator(mode="after")
    def coherent_observation(self) -> RoundObservation:
        if self.received_at < self.provider_at or self.available_at < self.received_at:
            raise ValueError("observation chronology must be provider, receipt, availability")
        if self.high is not None and self.low is not None and self.high < self.low:
            raise ValueError("high must not be below low")
        for name in ("open", "close"):
            value = getattr(self, name)
            if value is not None and (
                (self.low is not None and value < self.low)
                or (self.high is not None and value > self.high)
            ):
                raise ValueError(f"{name} must be within the observed low/high range")
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("ask must not be below bid")
        if self.close is None and (self.bid is None or self.ask is None) and self.provider_error is None:
            raise ValueError("observation requires a finalized bar, quote, or provider error")
        return self

    def validate_for(self, protocol: ResearchRoundProtocol) -> RoundObservation:
        type(self).model_validate(self.model_dump(mode="python"))
        protocol = protocol.validated()
        if self.provider.lower() != protocol.source.provider or self.feed.lower() != protocol.source.feed:
            raise ValueError("observation provider/feed does not match protocol")
        if self.symbol not in protocol.symbols:
            raise ValueError("observation symbol does not match protocol")
        if self.interval != protocol.interval:
            raise ValueError("observation interval does not match protocol")
        return self


class RoundProviderHealth(BaseModel):
    """Bounded provider status derived from retained, causally available evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["binance"]
    feed: Literal["spot"]
    revision: str = Field(min_length=1, max_length=256)
    reported_at: datetime
    last_successful_observation_at: datetime | None
    maximum_age_seconds: int = Field(gt=0, le=86400)
    state: Literal["healthy", "degraded", "stale", "error", "unavailable"]
    exclusions: tuple[str, ...] = Field(max_length=16)

    @field_validator("reported_at", "last_successful_observation_at")
    @classmethod
    def utc_health_time(cls, value: datetime | None, info: Any) -> datetime | None:
        return _utc(value, info.field_name) if value is not None else None

    @field_validator("revision", "exclusions")
    @classmethod
    def bounded_health_strings(cls, value: Any) -> Any:
        strings = (value,) if isinstance(value, str) else value
        if any(not item or len(item.encode("utf-8")) > 256 for item in strings):
            raise ValueError("provider health strings must contain 1 to 256 bytes")
        return value

    @model_validator(mode="after")
    def coherent_health(self) -> RoundProviderHealth:
        last = self.last_successful_observation_at
        if last is not None and last > self.reported_at:
            raise ValueError("provider health success cannot follow report time")
        if self.state == "healthy" and (
            last is None or self.exclusions or (self.reported_at - last).total_seconds() > self.maximum_age_seconds
        ):
            raise ValueError("healthy provider requires fresh success without exclusions")
        if self.state == "unavailable" and last is not None:
            raise ValueError("unavailable provider cannot claim successful observations")
        return self


class RoundReport(BaseModel):
    """A deliberately unqualified, paper-only report shell used by the runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    round_id: str
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: RoundStatus
    provider_health: RoundProviderHealth
    paper_only: Literal[True] = True
    qualification_status: Literal["unqualified"] = "unqualified"
    reasons: tuple[str, ...] = ()
    candidates: tuple[dict[str, Any], ...] = ()
