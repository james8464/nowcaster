"""Immutable causal context for a single symbol's paper research decision.

Bar timestamps denote the right (closed) minute boundary, as in Round 2.
Higher timeframes are UTC-aligned complete buckets, never rolling partial bars.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.research.round_two_contracts import ResearchRoundProtocol, RoundObservation, _utc
from src.strategies.types import canonical_hash


class Regime(StrEnum):
    TREND = "trend"
    RANGE = "range"
    VOLATILE = "volatile"
    ILLIQUID = "illiquid"
    UNKNOWN = "unknown"


class _Immutable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def utc_times(cls, value: Any) -> Any:
        return _utc(value, "timestamp") if isinstance(value, datetime) else value


class ContextObservation(RoundObservation):
    """Round 2 bar plus independently retained contemporaneous quote provenance.

    Missing optional fields remain missing; ordinary RoundObservation inputs are
    supported but cannot establish order-book quality without this evidence.
    """

    finalized: bool = True
    bid_size: Decimal | None = Field(default=None, gt=0, le=Decimal("1e30"))
    ask_size: Decimal | None = Field(default=None, gt=0, le=Decimal("1e30"))
    quote_provider_at: datetime | None = None
    quote_received_at: datetime | None = None
    quote_available_at: datetime | None = None
    quote_source_key: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("quote_provider_at", "quote_received_at", "quote_available_at")
    @classmethod
    def quote_utc(cls, value: datetime | None) -> datetime | None:
        return _utc(value, "quote timestamp") if value is not None else None

    @model_validator(mode="after")
    def quote_chronology(self) -> ContextObservation:
        times = (self.quote_provider_at, self.quote_received_at, self.quote_available_at)
        if all(item is not None for item in times) and not times[0] <= times[1] <= times[2]:
            raise ValueError("quote chronology must be provider, receipt, availability")
        if self.quote_available_at is not None and self.quote_available_at > self.available_at:
            raise ValueError("quote availability cannot follow the containing observation availability")
        return self


class CalendarEvent(_Immutable):
    event_id: str = Field(min_length=1, max_length=256)
    scheduled_at: datetime
    available_at: datetime
    symbols: tuple[Literal["BTCUSDT", "ETHUSDT"], ...] = Field(min_length=1, max_length=2)
    impact: Literal["high", "medium", "low"]


class CalendarSnapshot(_Immutable):
    """Versioned calendar with explicit coverage, receipt and expiration.

    Empty events means no events in declared coverage, not a missing calendar.
    Callers must retain revisions rather than replacing historical snapshots.
    """

    source: str = Field(min_length=1, max_length=256)
    revision: str = Field(min_length=1, max_length=256)
    published_at: datetime
    available_at: datetime
    valid_until: datetime
    coverage_starts_at: datetime
    coverage_ends_at: datetime
    events: tuple[CalendarEvent, ...] = Field(max_length=1000)

    @model_validator(mode="after")
    def chronology(self) -> CalendarSnapshot:
        if self.available_at < self.published_at or self.valid_until <= self.published_at:
            raise ValueError("invalid calendar chronology")
        if self.coverage_ends_at <= self.coverage_starts_at:
            raise ValueError("invalid calendar coverage")
        if len({event.event_id for event in self.events}) != len(self.events):
            raise ValueError("duplicate calendar event")
        for event in self.events:
            if event.available_at > self.published_at:
                raise ValueError("calendar event was unavailable at publication")
        return self

    @property
    def identity_hash(self) -> str:
        return canonical_hash(type(self).model_validate(self.model_dump()).model_dump(mode="json"))


class DayTraderContextProtocol(_Immutable):
    """Versioned fixed context settings bound to a retained Round 2 protocol."""

    version: Literal["day-trader-context-v1"] = "day-trader-context-v1"
    round_protocol: ResearchRoundProtocol
    trend_bars: int = Field(default=4, ge=2, le=32)
    volatility_bars: int = Field(default=20, ge=2, le=480)
    atr_bars: int = Field(default=14, ge=2, le=480)
    maximum_realized_volatility_bps: Decimal = Field(default=Decimal("100"), gt=0, le=10000)
    maximum_atr_normalized_range: Decimal = Field(default=Decimal("4"), gt=0, le=100)
    maximum_quote_age_seconds: int = Field(default=15, gt=0, le=60)
    maximum_calendar_age_seconds: int = Field(default=86400, gt=0, le=604800)
    blackout_before_minutes: int = Field(default=15, ge=0, le=1440)
    blackout_after_minutes: int = Field(default=15, ge=0, le=1440)

    @model_validator(mode="after")
    def bounded_history(self) -> DayTraderContextProtocol:
        self.round_protocol.validated()
        required = max(self.trend_bars * 15, self.volatility_bars + 1, self.atr_bars + 1)
        if not required <= self.round_protocol.maximum_feature_bars <= 1000:
            raise ValueError("context requires sufficient bounded feature history (maximum 1000)")
        return self

    @property
    def identity_hash(self) -> str:
        return canonical_hash(type(self).model_validate(self.model_dump()).model_dump(mode="json"))


class TimeframeTrend(_Immutable):
    timeframe_minutes: Literal[1, 5, 15]
    direction: Literal["up", "down", "flat", "unavailable"]
    strength: Decimal | None = Field(default=None, ge=0, le=1)
    change_bps: Decimal | None = Field(default=None, ge=Decimal("-10000"), le=Decimal("1e30"))
    last_bar_at: datetime | None = None


class MarketContextSnapshot(_Immutable):
    schema_version: Literal[1] = 1
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_hashes: tuple[str, ...] = Field(max_length=1000)
    calendar_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    decision_at: datetime
    available_at: datetime
    expires_at: datetime
    trends: tuple[TimeframeTrend, ...] = Field(min_length=3, max_length=3)
    realized_volatility_bps: Decimal | None = Field(default=None, ge=0, le=Decimal("1e30"))
    atr_normalized_range: Decimal | None = Field(default=None, ge=0, le=Decimal("1e30"))
    spread_bps: Decimal | None = Field(default=None, ge=0, le=20000)
    quote_imbalance: Decimal | None = Field(default=None, ge=-1, le=1)
    session: Literal["asia", "europe", "europe_americas_overlap", "americas", "overnight"]
    calendar_blackout: bool | None = None
    exclusions: tuple[str, ...] = Field(max_length=32)
    feature_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_snapshot(self) -> MarketContextSnapshot:
        if self.available_at > self.decision_at:
            raise ValueError("context availability cannot follow decision")
        if tuple(item.timeframe_minutes for item in self.trends) != (1, 5, 15):
            raise ValueError("context requires 1m/5m/15m trends in order")
        if any(item.last_bar_at and item.last_bar_at > self.decision_at for item in self.trends):
            raise ValueError("future trend bar")
        if any(len(item) != 64 or any(char not in "0123456789abcdef" for char in item) for item in self.source_hashes):
            raise ValueError("invalid source hash")
        if any(not reason or len(reason) > 128 for reason in self.exclusions):
            raise ValueError("invalid context exclusion")
        if self.feature_hash != canonical_hash(self.model_dump(mode="json", exclude={"feature_hash"})):
            raise ValueError("context feature hash mismatch")
        return self


def _trend(rows: Sequence[ContextObservation], minutes: int, count: int) -> TimeframeTrend:
    buckets: dict[int, list[ContextObservation]] = {}
    for row in rows:
        # A closed boundary 13:00 belongs to [12:45,13:00], not the next bucket.
        minute = int(row.provider_at.timestamp()) // 60
        end = ((minute - 1) // minutes + 1) * minutes
        buckets.setdefault(end, []).append(row)
    completed = []
    for end, members in sorted(buckets.items()):
        actual = [int(item.provider_at.timestamp()) // 60 for item in members]
        if actual == list(range(end - minutes + 1, end + 1)):
            completed.append(members[-1])
    selected = completed[-count:]
    if len(selected) < count or any(
        b.provider_at - a.provider_at != timedelta(minutes=minutes)
        for a, b in zip(selected, selected[1:], strict=False)
    ):
        return TimeframeTrend(
            timeframe_minutes=minutes,
            direction="unavailable",
            last_bar_at=selected[-1].provider_at if selected else None,
        )
    closes = [item.close for item in selected]
    delta = closes[-1] - closes[0]
    distance = sum(abs(b - a) for a, b in zip(closes, closes[1:], strict=False))
    return TimeframeTrend(
        timeframe_minutes=minutes,
        direction="up" if delta > 0 else "down" if delta < 0 else "flat",
        strength=abs(delta) / distance if distance else Decimal(0),
        change_bps=delta / closes[0] * 10000,
        last_bar_at=selected[-1].provider_at,
    )


def extract_context(
    protocol: DayTraderContextProtocol | ResearchRoundProtocol,
    observations: Sequence[ContextObservation | RoundObservation | Mapping[str, Any]],
    decision_at: datetime,
    calendar: CalendarSnapshot | Mapping[str, Any] | None,
) -> MarketContextSnapshot:
    """Extract one symbol as-of the decision; later receipts cannot repaint it.

    Invalid provenance/schema raises; missing, stale or unusable market evidence
    produces explicit exclusions with unavailable features, never default values.
    """
    decision_at = _utc(decision_at, "decision_at")
    settings = (
        DayTraderContextProtocol(round_protocol=protocol)
        if isinstance(protocol, ResearchRoundProtocol)
        else DayTraderContextProtocol.model_validate(protocol.model_dump())
    )
    research = settings.round_protocol.validated()
    parsed = [
        ContextObservation.model_validate(row.model_dump() if isinstance(row, BaseModel) else row)
        for row in observations
    ]
    for row in parsed:
        row.validate_for(research)
    symbols = {row.symbol for row in parsed}
    if len(symbols) != 1:
        raise ValueError("context observations must identify exactly one symbol")
    symbol = next(iter(symbols))
    # Ignore later receipt/revision before deduplication: it was unknowable then.
    causal = sorted(
        (row for row in parsed if row.available_at <= decision_at), key=lambda row: (row.provider_at, row.source_key)
    )
    reasons: list[str] = []
    unique: dict[datetime, ContextObservation] = {}
    keys: dict[str, ContextObservation] = {}
    for row in causal:
        if (row.provider_at in unique and unique[row.provider_at] != row) or (
            row.source_key in keys and keys[row.source_key] != row
        ):
            reasons.append("conflicting_observations")
        unique.setdefault(row.provider_at, row)
        keys.setdefault(row.source_key, row)
    rows = list(unique.values())[-research.maximum_feature_bars :]
    latest = rows[-1] if rows else None
    available_at = max((row.available_at for row in rows), default=decision_at)
    expires_at = (
        latest.provider_at + timedelta(seconds=research.maximum_observation_age_seconds) if latest else decision_at
    )
    valid = []
    for row in rows:
        if row.provider_error:
            reasons.append("provider_error")
        elif not row.finalized or any(getattr(row, name) is None for name in ("open", "high", "low", "close")):
            reasons.append("unfinalized_input")
        elif row.provider_at.second or row.provider_at.microsecond:
            reasons.append("unaligned_bar")
        else:
            valid.append(row)
    if not latest:
        reasons.append("observations_unavailable")
    elif decision_at >= expires_at:
        reasons.append("observation_stale")
    if any(b.provider_at - a.provider_at != timedelta(minutes=1) for a, b in zip(valid, valid[1:], strict=False)):
        reasons.append("history_gap")
    trends = tuple(_trend(valid, minutes, settings.trend_bars) for minutes in (1, 5, 15))
    reasons.extend(
        f"insufficient_{item.timeframe_minutes}m_history" for item in trends if item.direction == "unavailable"
    )
    volatility = normalized = spread = imbalance = None
    if len(valid) >= settings.volatility_bars + 1:
        selected = valid[-settings.volatility_bars - 1 :]
        returns = [math.log(float(b.close / a.close)) for a, b in zip(selected, selected[1:], strict=False)]
        volatility = Decimal(str(math.sqrt(sum(value * value for value in returns) / len(returns)) * 10000))
        if volatility > settings.maximum_realized_volatility_bps:
            reasons.append("volatility_exceeds_maximum")
    else:
        reasons.append("volatility_unavailable")
    if len(valid) >= settings.atr_bars + 1:
        selected = valid[-settings.atr_bars - 1 :]
        ranges = [
            max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
            for a, b in zip(selected, selected[1:], strict=False)
        ]
        atr = sum(ranges) / len(ranges)
        if atr:
            normalized = ranges[-1] / atr
            if normalized > settings.maximum_atr_normalized_range:
                reasons.append("abnormal_range")
        else:
            reasons.append("atr_unavailable")
    else:
        reasons.append("atr_unavailable")
    if latest and latest.bid is not None and latest.ask is not None:
        spread = (latest.ask - latest.bid) / ((latest.ask + latest.bid) / 2) * 10000
        if spread > research.maximum_spread_bps:
            reasons.append("spread_exceeds_maximum")
    else:
        reasons.append("spread_unavailable")
    if latest is None or latest.volume is None or latest.volume <= 0:
        reasons.append("liquidity_unavailable")
    quote_fields = (
        "bid",
        "ask",
        "bid_size",
        "ask_size",
        "quote_provider_at",
        "quote_received_at",
        "quote_available_at",
        "quote_source_key",
    )
    if latest is None or any(getattr(latest, name) is None for name in quote_fields):
        reasons.append("order_book_unavailable")
    elif decision_at - latest.quote_provider_at >= timedelta(seconds=settings.maximum_quote_age_seconds):
        reasons.append("order_book_stale")
    else:
        imbalance = (latest.bid_size - latest.ask_size) / (latest.bid_size + latest.ask_size)
        available_at = max(available_at, latest.quote_available_at)
        expires_at = min(expires_at, latest.quote_provider_at + timedelta(seconds=settings.maximum_quote_age_seconds))
    calendar_hash, blackout = None, None
    if calendar is None:
        reasons.append("calendar_missing")
    else:
        snapshot = CalendarSnapshot.model_validate(
            calendar.model_dump() if isinstance(calendar, BaseModel) else calendar
        )
        if snapshot.available_at > decision_at:
            reasons.append("calendar_unavailable")
        else:
            calendar_hash = snapshot.identity_hash
            available_at = max(available_at, snapshot.available_at)
            calendar_expiry = min(
                snapshot.valid_until, snapshot.published_at + timedelta(seconds=settings.maximum_calendar_age_seconds)
            )
            expires_at = min(expires_at, calendar_expiry)
            if decision_at >= calendar_expiry:
                reasons.append("calendar_stale")
            before = decision_at - timedelta(minutes=settings.blackout_after_minutes)
            after = decision_at + timedelta(minutes=settings.blackout_before_minutes)
            if snapshot.coverage_starts_at > before or snapshot.coverage_ends_at < after:
                reasons.append("calendar_coverage_missing")
            if "calendar_stale" not in reasons and "calendar_coverage_missing" not in reasons:
                blackout = any(
                    event.impact == "high" and symbol in event.symbols and before <= event.scheduled_at <= after
                    for event in snapshot.events
                )
                if blackout:
                    reasons.append("calendar_blackout")
    hour = decision_at.hour
    session = (
        "asia"
        if hour < 7
        else "europe"
        if hour < 13
        else "europe_americas_overlap"
        if hour < 16
        else "americas"
        if hour < 21
        else "overnight"
    )
    payload = dict(
        symbol=symbol,
        protocol_hash=research.identity_hash,
        context_protocol_hash=settings.identity_hash,
        source_identity_hash=canonical_hash(research.source.model_dump(mode="json")),
        source_hashes=tuple(canonical_hash(row.model_dump(mode="json")) for row in rows),
        calendar_hash=calendar_hash,
        decision_at=decision_at,
        available_at=available_at,
        expires_at=expires_at,
        trends=trends,
        realized_volatility_bps=volatility,
        atr_normalized_range=normalized,
        spread_bps=spread,
        quote_imbalance=imbalance,
        session=session,
        calendar_blackout=blackout,
        exclusions=tuple(dict.fromkeys(reasons)),
        schema_version=1,
    )
    # JSON serialization matches the immutable wire contract before signing it.
    unsigned = MarketContextSnapshot.model_construct(**payload, feature_hash="0" * 64)
    payload["feature_hash"] = canonical_hash(unsigned.model_dump(mode="json", exclude={"feature_hash"}))
    return MarketContextSnapshot.model_validate(payload)
