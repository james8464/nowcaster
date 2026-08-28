from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from websockets.asyncio.client import connect

from src.live_monitor.types import (
    DepthLevel,
    MarketBar,
    MarketDepth,
    MarketEvent,
    MarketQuote,
    MarketStatusEvent,
    MarketTrade,
    MonitorHealth,
    ProviderHealthEvent,
)
from src.strategies.calendars import calendar_for
from src.strategies.types import BarInterval

MAXIMUM_MESSAGE_BYTES = 64 * 1024
MAXIMUM_REPAIR_BARS = 1_000


class ProviderDecodeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderSymbolMetadata:
    symbol: str
    tick_size: Decimal
    tradable: bool
    shortable: bool
    easy_to_borrow: bool


def load_alpaca_symbol_metadata(
    symbols: Iterable[str], *, key_id: str, secret: str, client: httpx.Client | None = None
) -> dict[str, ProviderSymbolMetadata]:
    normalized = _symbols(symbols)
    owned = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(10.0))
    try:
        result = {}
        for symbol in normalized:
            response = http.get(
                f"https://paper-api.alpaca.markets/v2/assets/{symbol}",
                headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or str(payload.get("symbol", "")).upper() != symbol:
                raise ValueError("Alpaca returned invalid symbol metadata")
            raw_increment = Decimal(str(payload.get("price_increment", "0")))
            result[symbol] = ProviderSymbolMetadata(
                symbol=symbol,
                tick_size=raw_increment if raw_increment > 0 else Decimal(0),
                tradable=payload.get("tradable") is True,
                shortable=payload.get("shortable") is True,
                easy_to_borrow=payload.get("easy_to_borrow") is True,
            )
        if not all(item.tradable for item in result.values()):
            raise ValueError("one or more Alpaca symbols are not tradable")
        return result
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise ValueError("Alpaca symbol metadata is unavailable") from error
    finally:
        if owned:
            http.close()


def load_binance_symbol_metadata(
    symbols: Iterable[str], *, client: httpx.Client | None = None
) -> dict[str, ProviderSymbolMetadata]:
    normalized = _symbols(symbols)
    owned = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(10.0))
    try:
        response = http.get("https://api.binance.com/api/v3/exchangeInfo", params={"symbols": json.dumps(normalized)})
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("symbols") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Binance returned invalid exchange metadata")
        result = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol", "")).upper()
            price_filter = next(
                (item for item in row.get("filters", []) if item.get("filterType") == "PRICE_FILTER"), None
            )
            if symbol not in normalized or not isinstance(price_filter, dict):
                continue
            result[symbol] = ProviderSymbolMetadata(
                symbol=symbol,
                tick_size=Decimal(str(price_filter["tickSize"])),
                tradable=row.get("status") == "TRADING" and "SPOT" in row.get("permissions", ["SPOT"]),
                shortable=False,
                easy_to_borrow=False,
            )
        if set(result) != set(normalized) or not all(item.tradable for item in result.values()):
            raise ValueError("one or more Binance symbols are not tradable")
        return result
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
        raise ValueError("Binance symbol metadata is unavailable") from error
    finally:
        if owned:
            http.close()


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


def _depth_levels(value: Any) -> tuple[DepthLevel, ...]:
    if not isinstance(value, list) or len(value) > 5_000:
        raise ProviderDecodeError("provider depth levels are malformed")
    result: list[DepthLevel] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise ProviderDecodeError("provider depth level is malformed")
        result.append(DepthLevel(price=Decimal(str(item[0])), size=Decimal(str(item[1]))))
    return tuple(result)


def expected_repair_starts(provider: str, feed: str, start: datetime, end: datetime) -> tuple[datetime, ...]:
    if start.tzinfo is not UTC or end.tzinfo is not UTC or end <= start:
        raise ValueError("gap repair requires a positive explicit UTC window")
    expected = calendar_for(provider, feed).expected_opens(start, end, BarInterval.ONE_MINUTE)
    if len(expected) > MAXIMUM_REPAIR_BARS:
        raise ValueError("gap exceeds the bounded repair window")
    return expected


def load_alpaca_repair_bars(
    symbol: str,
    *,
    start: datetime,
    end: datetime,
    feed: str,
    key_id: str,
    secret: str,
    client: httpx.Client | None = None,
) -> tuple[MarketBar, ...]:
    expected = expected_repair_starts("alpaca", feed, start, end)
    if not expected:
        return ()
    owned = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(10.0))
    received_at = datetime.now(UTC)
    try:
        response = http.get(
            "https://data.alpaca.markets/v2/stocks/bars",
            params={
                "symbols": symbol,
                "timeframe": "1Min",
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
                "limit": MAXIMUM_REPAIR_BARS,
                "adjustment": "raw",
                "feed": feed,
            },
            headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret},
        )
        response.raise_for_status()
        payload = response.json()
        container = payload.get("bars") if isinstance(payload, dict) else None
        rows = container.get(symbol) if isinstance(container, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Alpaca repair response is malformed")
        bars = tuple(
            MarketBar(
                provider="alpaca",
                feed=feed,
                symbol=symbol,
                interval="1m",
                start=_zulu(str(item["t"])),
                end=_zulu(str(item["t"])) + timedelta(minutes=1),
                available_at=received_at,
                received_at=received_at,
                open=Decimal(str(item["o"])),
                high=Decimal(str(item["h"])),
                low=Decimal(str(item["l"])),
                close=Decimal(str(item["c"])),
                volume=Decimal(str(item["v"])),
                finalized=True,
                revision=0,
                repair_verified=True,
            )
            for item in rows
            if isinstance(item, dict) and start <= _zulu(str(item.get("t", ""))) < end
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        raise ValueError("Alpaca bounded gap repair failed") from error
    finally:
        if owned:
            http.close()
    if tuple(sorted(item.start for item in bars)) != expected:
        raise ValueError("Alpaca bounded gap repair is incomplete")
    return tuple(sorted(bars, key=lambda item: item.start))


def load_binance_repair_bars(
    symbol: str,
    *,
    start: datetime,
    end: datetime,
    client: httpx.Client | None = None,
) -> tuple[MarketBar, ...]:
    expected = expected_repair_starts("binance", "spot", start, end)
    owned = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(10.0))
    received_at = datetime.now(UTC)
    try:
        response = http.get(
            "https://api.binance.com/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": "1m",
                "startTime": int(start.timestamp() * 1_000),
                "endTime": int(end.timestamp() * 1_000),
                "limit": MAXIMUM_REPAIR_BARS,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Binance repair response is malformed")
        bars = tuple(
            MarketBar(
                provider="binance",
                feed="spot",
                symbol=symbol,
                interval="1m",
                start=_epoch_milliseconds(item[0]),
                end=_epoch_milliseconds(item[0]) + timedelta(minutes=1),
                available_at=received_at,
                received_at=received_at,
                open=Decimal(str(item[1])),
                high=Decimal(str(item[2])),
                low=Decimal(str(item[3])),
                close=Decimal(str(item[4])),
                volume=Decimal(str(item[5])),
                finalized=True,
                revision=0,
                repair_verified=True,
            )
            for item in payload
            if isinstance(item, list) and len(item) >= 6 and start <= _epoch_milliseconds(item[0]) < end
        )
    except (httpx.HTTPError, IndexError, TypeError, ValueError) as error:
        raise ValueError("Binance bounded gap repair failed") from error
    finally:
        if owned:
            http.close()
    if tuple(sorted(item.start for item in bars)) != expected:
        raise ValueError("Binance bounded gap repair is incomplete")
    return tuple(sorted(bars, key=lambda item: item.start))


def _alpaca_tick_size(metadata: ProviderSymbolMetadata | None, last: Decimal) -> Decimal:
    if metadata is not None and metadata.tick_size > 0:
        return metadata.tick_size
    return Decimal("0.0001") if last < 1 else Decimal("0.01")


@dataclass(slots=True)
class AlpacaMarketDataAdapter:
    feed: str
    key_id: str = field(repr=False)
    secret: str = field(repr=False)
    metadata: dict[str, ProviderSymbolMetadata] = field(default_factory=dict, repr=False)

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
        return {
            "action": "subscribe",
            "trades": normalized,
            "quotes": normalized,
            "bars": normalized,
            "statuses": normalized,
            "lulds": normalized,
            "corrections": normalized,
            "cancelErrors": normalized,
        }

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
                last = (bid + ask) / 2
                result.append(
                    MarketQuote(
                        provider="alpaca",
                        feed=self.feed,
                        symbol=str(item["S"]),
                        bid=bid,
                        ask=ask,
                        bid_size=Decimal(str(item["bs"])) if item.get("bs") is not None else None,
                        ask_size=Decimal(str(item["as"])) if item.get("as") is not None else None,
                        last=last,
                        tick_size=_alpaca_tick_size(self.metadata.get(str(item["S"]).upper()), last),
                        sequence=int(item["i"]) if item.get("i") is not None else None,
                        provider_time=_zulu(str(item["t"])),
                        received_at=received_at,
                    )
                )
            elif event_type == "t":
                result.append(
                    MarketTrade(
                        provider="alpaca",
                        feed=self.feed,
                        symbol=str(item["S"]),
                        trade_id=str(item["i"]) if item.get("i") is not None else None,
                        price=Decimal(str(item["p"])),
                        size=Decimal(str(item["s"])),
                        aggressor="unknown",
                        sequence=int(item["i"]) if item.get("i") is not None else None,
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
            elif event_type in {"s", "l", "c", "x"}:
                kind = {"s": "status", "l": "luld", "c": "correction", "x": "cancel_error"}[event_type]
                status = {
                    "s": str(item.get("sm") or item.get("sc") or "trading_status"),
                    "l": "limit_state",
                    "c": "trade_correction",
                    "x": "trade_cancel_error",
                }[event_type]
                reference = item.get("ci") or item.get("oi") or item.get("i")
                result.append(
                    MarketStatusEvent(
                        provider="alpaca",
                        feed=self.feed,
                        symbol=str(item["S"]),
                        kind=kind,
                        status=status,
                        reference_id=str(reference) if reference is not None else None,
                        sequence=int(reference) if isinstance(reference, int) and reference >= 0 else None,
                        provider_time=_zulu(str(item.get("t", received_at.isoformat()))),
                        received_at=received_at,
                        details={str(key): value for key, value in item.items() if key not in {"T", "S", "t"}},
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
    metadata: dict[str, ProviderSymbolMetadata] = field(default_factory=dict, repr=False)

    def subscription(self, symbols: Iterable[str]) -> dict[str, Any]:
        params: list[str] = []
        for symbol in _symbols(symbols):
            lowered = symbol.lower()
            params.extend(
                (
                    f"{lowered}@aggTrade",
                    f"{lowered}@bookTicker",
                    f"{lowered}@depth@100ms",
                    f"{lowered}@kline_1m",
                )
            )
        return {"method": "SUBSCRIBE", "params": params, "id": 1}

    def decode(self, message: bytes | str, *, received_at: datetime) -> tuple[MarketEvent, ...]:
        item = _json_object(message)
        if not isinstance(item, dict):
            raise ProviderDecodeError("Binance payload must be an event object")
        if item.get("result") is None and item.get("id") is not None:
            return (
                ProviderHealthEvent(
                    provider="binance",
                    feed=self.feed,
                    status=MonitorHealth.HEALTHY,
                    reason="subscribed",
                    occurred_at=received_at,
                ),
            )
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
                    bid_size=Decimal(str(item["B"])),
                    ask_size=Decimal(str(item["A"])),
                    last=(bid + ask) / 2,
                    tick_size=self.metadata.get(
                        str(item["s"]).upper(), ProviderSymbolMetadata("", Decimal("0.01"), True, False, False)
                    ).tick_size,
                    sequence=int(item["u"]) if item.get("u") is not None else None,
                    provider_time=_epoch_milliseconds(item["E"]),
                    received_at=received_at,
                ),
            )
        if event_type == "aggTrade":
            sequence = int(item["a"])
            return (
                MarketTrade(
                    provider="binance",
                    feed=self.feed,
                    symbol=str(item["s"]),
                    trade_id=str(sequence),
                    price=Decimal(str(item["p"])),
                    size=Decimal(str(item["q"])),
                    aggressor="sell" if item.get("m") is True else "buy",
                    sequence=sequence,
                    provider_time=_epoch_milliseconds(item.get("T", item["E"])),
                    received_at=received_at,
                ),
            )
        if event_type == "depthUpdate":
            return (
                MarketDepth(
                    provider="binance",
                    feed=self.feed,
                    symbol=str(item["s"]),
                    first_update_id=int(item["U"]),
                    final_update_id=int(item["u"]),
                    bids=_depth_levels(item["b"]),
                    asks=_depth_levels(item["a"]),
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
    "ProviderSymbolMetadata",
    "expected_repair_starts",
    "ReconnectPolicy",
    "load_alpaca_symbol_metadata",
    "load_alpaca_repair_bars",
    "load_binance_repair_bars",
    "load_binance_symbol_metadata",
]
