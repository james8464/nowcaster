from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.strategies.types import BarInterval, canonical_hash

INTERVAL_DURATION: dict[BarInterval, timedelta] = {
    BarInterval.ONE_MINUTE: timedelta(minutes=1),
    BarInterval.FIVE_MINUTES: timedelta(minutes=5),
    BarInterval.FIFTEEN_MINUTES: timedelta(minutes=15),
    BarInterval.THIRTY_MINUTES: timedelta(minutes=30),
    BarInterval.ONE_HOUR: timedelta(hours=1),
    BarInterval.FOUR_HOURS: timedelta(hours=4),
    BarInterval.ONE_DAY: timedelta(days=1),
}


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is not UTC:
        raise ValueError("timestamps must be explicit UTC datetimes")
    return value


class BarRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    interval: BarInterval
    start: datetime
    end: datetime
    feed: str | None = None
    page_size: int = Field(default=1_000, ge=1, le=10_000)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("symbol must not be empty")
        return value

    @field_validator("start", "end")
    @classmethod
    def utc_boundaries(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def ordered_boundaries(self) -> BarRequest:
        if self.end <= self.start:
            raise ValueError("bar request end must be after start")
        return self


class BarQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    feed: str
    symbol: str
    interval: BarInterval
    start: datetime
    end: datetime

    @field_validator("provider", "feed")
    @classmethod
    def non_empty_identity(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("provider and feed must not be empty")
        return value

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("symbol must not be empty")
        return value

    @field_validator("start", "end")
    @classmethod
    def utc_boundaries(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def ordered_boundaries(self) -> BarQuery:
        if self.end <= self.start:
            raise ValueError("bar query end must be after start")
        return self


class MarketBar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    feed: str
    symbol: str
    interval: BarInterval
    open_timestamp: datetime
    close_timestamp: datetime
    available_at: datetime
    retrieved_at: datetime | None = None
    source_available_at: datetime | None = None
    observed_at: datetime | None = None
    vintage_fidelity: Literal[
        "authenticated_immutable",
        "backfilled_rest_no_revision_history",
        "unknown_legacy",
    ] = "authenticated_immutable"
    revision: int = Field(default=1, ge=1)
    finalized: bool = True
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(ge=0)
    vwap: float | None = None
    trade_count: int | None = Field(default=None, ge=0)
    quote_volume: float | None = Field(default=None, ge=0)
    taker_buy_base_volume: float | None = Field(default=None, ge=0)
    taker_buy_quote_volume: float | None = Field(default=None, ge=0)
    payload_hash: str = Field(min_length=64, max_length=64)

    @field_validator("provider", "feed")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("provider and feed must not be empty")
        return value

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("symbol must not be empty")
        return value

    @field_validator(
        "open_timestamp",
        "close_timestamp",
        "available_at",
        "retrieved_at",
        "source_available_at",
        "observed_at",
    )
    @classmethod
    def utc_timestamps(cls, value: datetime | None) -> datetime | None:
        return require_utc(value) if value is not None else None

    @model_validator(mode="after")
    def valid_bar(self) -> MarketBar:
        if self.close_timestamp <= self.open_timestamp:
            raise ValueError("bar close timestamp must follow open timestamp")
        if self.finalized and self.available_at < self.close_timestamp:
            raise ValueError("a finalized bar cannot be available before its close timestamp")
        if self.retrieved_at is not None and self.retrieved_at < self.available_at:
            raise ValueError("bar retrieval cannot precede source availability")
        source_available_at = self.source_available_at or self.close_timestamp
        observed_at = self.observed_at or self.retrieved_at or self.available_at
        object.__setattr__(self, "source_available_at", source_available_at)
        object.__setattr__(self, "observed_at", observed_at)
        if source_available_at < self.close_timestamp or observed_at < source_available_at:
            raise ValueError("bar observation provenance is chronologically malformed")
        if self.vintage_fidelity == "backfilled_rest_no_revision_history" and self.available_at < observed_at:
            raise ValueError("REST backfills cannot be labeled available before retrieval")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close) or self.high < self.low:
            raise ValueError("bar OHLC values are inconsistent")
        return self

    @property
    def bar_id(self) -> str:
        return canonical_hash(
            [
                self.provider,
                self.feed,
                self.symbol,
                self.interval,
                self.open_timestamp,
                self.revision,
                self.available_at,
            ]
        )


class BarProvider(Protocol):
    def fetch(self, request: BarRequest) -> Iterable[MarketBar]: ...


def deduplicate_bars(bars: Iterable[MarketBar]) -> list[MarketBar]:
    unique: dict[tuple[str, str, str, BarInterval, datetime, int, str], MarketBar] = {}
    for bar in bars:
        key = (
            bar.provider,
            bar.feed,
            bar.symbol,
            bar.interval,
            bar.open_timestamp,
            bar.revision,
            bar.payload_hash,
        )
        unique.setdefault(key, bar)
    return sorted(unique.values(), key=lambda bar: (bar.open_timestamp, bar.revision, bar.available_at))


def atomic_write_bytes(path: Path, payload: bytes) -> Path:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return path


def request_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_attempts: int,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs: object,
) -> httpx.Response:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    response: httpx.Response | None = None
    for attempt in range(max_attempts):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError:
            if attempt + 1 == max_attempts:
                raise
            sleep(min(2**attempt, 8))
            continue
        if response.status_code not in {418, 429, 500, 502, 503, 504} or attempt + 1 == max_attempts:
            response.raise_for_status()
            return response
        retry_after = response.headers.get("Retry-After")
        sleep(float(retry_after) if retry_after is not None else min(2**attempt, 8))
    if response is None:  # pragma: no cover - loop always executes
        raise RuntimeError("request did not execute")
    response.raise_for_status()
    return response


__all__ = [
    "INTERVAL_DURATION",
    "BarProvider",
    "BarQuery",
    "BarRequest",
    "MarketBar",
    "atomic_write_bytes",
    "deduplicate_bars",
    "request_with_retries",
    "require_utc",
]
