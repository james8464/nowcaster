from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from websockets.asyncio.client import connect

from src.live_monitor.types import MarketBar, MarketQuote, MonitorHealth, ProviderHealthEvent

MarketEvent = MarketBar | MarketQuote | ProviderHealthEvent
MAXIMUM_MESSAGE_BYTES = 64 * 1024


class ProviderDecodeError(ValueError):
    pass


def _json_object(message: bytes | str) -> Any:
    raw = message if isinstance(message, bytes) else message.encode("utf-8")
    if len(raw) > MAXIMUM_MESSAGE_BYTES:
        raise ProviderDecodeError("provider payload exceeds the maximum size")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ProviderDecodeError("provider payload is not valid JSON") from error


def _zulu(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProviderDecodeError("provider timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ProviderDecodeError("provider timestamp must be explicit UTC")
    return parsed.astimezone(UTC)


def _epoch_milliseconds(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value) / 1_000, tz=UTC)
    except (TypeError, ValueError, OSError) as error:
        raise ProviderDecodeError("provider timestamp is invalid") from error


def _symbols(values: Iterable[str], *, maximum: int = 200) -> tuple[str, ...]:
    result = tuple(dict.fromkeys(value.strip().upper() for value in values if value.strip()))
    if not result or len(result) > maximum or any(len(value) > 32 for value in result):
        raise ValueError("watchlist must contain between 1 and the provider symbol limit")
    return result


@dataclass(slots=True)
class AlpacaMarketDataAdapter:
    feed: str
    key_id: str = field(repr=False)
    secret: str = field(repr=False)

    def __post_init__(self) -> None:
        self.feed = self.feed.strip().lower()
        if self.feed not in {"iex", "sip"}:
            raise ValueError("Alpaca feed must be iex or sip")
        if not self.key_id or not self.secret:
            raise ValueError("Alpaca market data credentials are required")

    def authentication(self) -> dict[str, str]:
        return {"action": "auth", "key": self.key_id, "secret": self.secret}

    def subscription(self, symbols: Iterable[str]) -> dict[str, Any]:
        normalized = list(_symbols(symbols))
        return {"action": "subscribe", "quotes": normalized, "bars": normalized}

    def decode(self, message: bytes | str, *, received_at: datetime) -> tuple[MarketEvent, ...]:
        payload = _json_object(message)
        if not isinstance(payload, list) or not payload:
            raise ProviderDecodeError("Alpaca payload must be a nonempty event list")
        result: list[MarketEvent] = []
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("T"), str):
                raise ProviderDecodeError("Alpaca event is malformed")
            event_type = item["T"]
            if event_type == "q":
                bid = Decimal(str(item["bp"]))
                ask = Decimal(str(item["ap"]))
                result.append(
                    MarketQuote(
                        provider="alpaca",
                        feed=self.feed,
                        symbol=str(item["S"]),
                        bid=bid,
                        ask=ask,
                        last=(bid + ask) / 2,
                        tick_size=Decimal("0.01"),
                        provider_time=_zulu(str(item["t"])),
                        received_at=received_at,
                    )
                )
            elif event_type == "b":
                start = _zulu(str(item["t"]))
                end = start + timedelta(minutes=1)
                result.append(
                    MarketBar(
                        provider="alpaca",
                        feed=self.feed,
                        symbol=str(item["S"]),
                        interval="1m",
                        start=start,
                        end=end,
                        available_at=max(end, received_at),
                        received_at=received_at,
                        open=Decimal(str(item["o"])),
                        high=Decimal(str(item["h"])),
                        low=Decimal(str(item["l"])),
                        close=Decimal(str(item["c"])),
                        volume=Decimal(str(item["v"])),
                        finalized=True,
                        revision=0,
                    )
                )
            elif event_type == "success":
                reason = str(item.get("msg", "success"))
                status = MonitorHealth.WARMING if reason == "connected" else MonitorHealth.HEALTHY
                result.append(
                    ProviderHealthEvent(
                        provider="alpaca", feed=self.feed, status=status, reason=reason, occurred_at=received_at
                    )
                )
            elif event_type == "subscription":
                result.append(
                    ProviderHealthEvent(
                        provider="alpaca",
                        feed=self.feed,
                        status=MonitorHealth.WARMING,
                        reason="subscribed",
                        occurred_at=received_at,
                    )
                )
            elif event_type == "error":
                reason = "connection_limit" if int(item.get("code", 0)) == 406 else "provider_error"
                result.append(
                    ProviderHealthEvent(
                        provider="alpaca",
                        feed=self.feed,
                        status=MonitorHealth.FAILED,
                        reason=reason,
                        occurred_at=received_at,
                    )
                )
            else:
                raise ProviderDecodeError("unsupported Alpaca event type")
        return tuple(result)

    async def stream(self, url: str, symbols: Iterable[str]) -> AsyncIterator[MarketEvent]:
        subscription = self.subscription(symbols)
        policy = ReconnectPolicy()
        attempt = 0
        while True:
            try:
                async with connect(url, ping_interval=20, ping_timeout=20, max_size=MAXIMUM_MESSAGE_BYTES) as socket:
                    await socket.send(json.dumps(self.authentication(), separators=(",", ":")))
                    await socket.send(json.dumps(subscription, separators=(",", ":")))
                    attempt = 0
                    async for message in socket:
                        for event in self.decode(message, received_at=datetime.now(UTC)):
                            yield event
            except asyncio.CancelledError:
                raise
            except Exception:
                yield ProviderHealthEvent(
                    provider="alpaca",
                    feed=self.feed,
                    status=MonitorHealth.RECONNECTING,
                    reason="stream_disconnected",
                    occurred_at=datetime.now(UTC),
                )
                await asyncio.sleep(policy.delay(attempt))
                attempt += 1


@dataclass(slots=True)
class BinanceSpotAdapter:
    feed: str = "spot"

    def subscription(self, symbols: Iterable[str]) -> dict[str, Any]:
        params: list[str] = []
        for symbol in _symbols(symbols):
            lowered = symbol.lower()
            params.extend((f"{lowered}@bookTicker", f"{lowered}@kline_1m"))
        return {"method": "SUBSCRIBE", "params": params, "id": 1}

    def decode(self, message: bytes | str, *, received_at: datetime) -> tuple[MarketEvent, ...]:
        item = _json_object(message)
        if not isinstance(item, dict):
            raise ProviderDecodeError("Binance payload must be an event object")
        if item.get("result") is None and item.get("id") is not None:
            return ()
        if "code" in item:
            return (
                ProviderHealthEvent(
                    provider="binance",
                    feed=self.feed,
                    status=MonitorHealth.FAILED,
                    reason="provider_error",
                    occurred_at=received_at,
                ),
            )
        event_type = item.get("e")
        if event_type == "bookTicker":
            bid = Decimal(str(item["b"]))
            ask = Decimal(str(item["a"]))
            return (
                MarketQuote(
                    provider="binance",
                    feed=self.feed,
                    symbol=str(item["s"]),
                    bid=bid,
                    ask=ask,
                    last=(bid + ask) / 2,
                    tick_size=Decimal("0.01"),
                    provider_time=_epoch_milliseconds(item["E"]),
                    received_at=received_at,
                ),
            )
        if event_type == "kline":
            kline = item.get("k")
            if not isinstance(kline, dict):
                raise ProviderDecodeError("Binance kline is malformed")
            if kline.get("x") is not True:
                return ()
            start = _epoch_milliseconds(kline["t"])
            end = _epoch_milliseconds(kline["T"] + 1)
            return (
                MarketBar(
                    provider="binance",
                    feed=self.feed,
                    symbol=str(kline["s"]),
                    interval="1m",
                    start=start,
                    end=end,
                    available_at=_epoch_milliseconds(item["E"]),
                    received_at=received_at,
                    open=Decimal(str(kline["o"])),
                    high=Decimal(str(kline["h"])),
                    low=Decimal(str(kline["l"])),
                    close=Decimal(str(kline["c"])),
                    volume=Decimal(str(kline["v"])),
                    finalized=True,
                    revision=0,
                ),
            )
        raise ProviderDecodeError("unsupported Binance event type")

    async def stream(self, url: str, symbols: Iterable[str]) -> AsyncIterator[MarketEvent]:
        subscription = self.subscription(symbols)
        policy = ReconnectPolicy()
        attempt = 0
        while True:
            try:
                connected_at = datetime.now(UTC)
                async with connect(url, ping_interval=20, ping_timeout=20, max_size=MAXIMUM_MESSAGE_BYTES) as socket:
                    await socket.send(json.dumps(subscription, separators=(",", ":")))
                    attempt = 0
                    while not policy.rotation_due(connected_at=connected_at, now=datetime.now(UTC)):
                        message = await asyncio.wait_for(socket.recv(), timeout=30)
                        for event in self.decode(message, received_at=datetime.now(UTC)):
                            yield event
            except asyncio.CancelledError:
                raise
            except Exception:
                yield ProviderHealthEvent(
                    provider="binance",
                    feed=self.feed,
                    status=MonitorHealth.RECONNECTING,
                    reason="stream_disconnected",
                    occurred_at=datetime.now(UTC),
                )
                await asyncio.sleep(policy.delay(attempt))
                attempt += 1


@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    initial_seconds: int = 1
    maximum_seconds: int = 30
    multiplier: int = 2
    rotate_after: timedelta = timedelta(hours=23, minutes=55)

    def __post_init__(self) -> None:
        if self.initial_seconds < 1 or self.maximum_seconds < self.initial_seconds or self.multiplier < 1:
            raise ValueError("reconnect policy values are invalid")

    def delay(self, attempt: int) -> int:
        return min(self.initial_seconds * self.multiplier ** max(attempt, 0), self.maximum_seconds)

    def rotation_due(self, *, connected_at: datetime, now: datetime) -> bool:
        return now - connected_at >= self.rotate_after


class ProviderHealthTracker:
    def __init__(self, *, stale_after: timedelta):
        if stale_after <= timedelta(0):
            raise ValueError("stale_after must be positive")
        self.stale_after = stale_after
        self._status = MonitorHealth.WARMING
        self._last_observed: datetime | None = None

    def connected(self, *, at: datetime) -> None:
        self._status = MonitorHealth.WARMING
        self._last_observed = at

    def observed(self, *, at: datetime, continuity_ok: bool) -> None:
        self._last_observed = at
        self._status = MonitorHealth.HEALTHY if continuity_ok else MonitorHealth.RECONNECTING

    def disconnected(self) -> None:
        self._status = MonitorHealth.RECONNECTING

    def status(self, *, now: datetime) -> MonitorHealth:
        if self._last_observed is not None and now - self._last_observed > self.stale_after:
            return MonitorHealth.STALE
        return self._status


__all__ = [
    "AlpacaMarketDataAdapter",
    "BinanceSpotAdapter",
    "ProviderDecodeError",
    "ProviderHealthTracker",
    "ReconnectPolicy",
]
