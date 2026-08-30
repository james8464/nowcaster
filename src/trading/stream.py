from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from src.strategies.types import canonical_hash
from src.trading.types import TradeUpdate
from src.utils.tls import verified_client_context

PAPER_STREAM_URL = "wss://paper-api.alpaca.markets/stream"
MAX_FRAME_BYTES = 1_000_000
_KNOWN_EVENTS = {
    "new",
    "partial_fill",
    "fill",
    "canceled",
    "expired",
    "rejected",
    "replaced",
    "suspended",
    "calculated",
    "cancel_rejected",
    "order_replace_rejected",
    "pending_new",
    "pending_cancel",
    "pending_replace",
    "done_for_day",
    "stopped",
}


class StreamProtocolError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StreamControl:
    kind: str
    status: str


def _decode_frame(message: bytes | str) -> Mapping[str, Any]:
    if isinstance(message, bytes):
        if len(message) > MAX_FRAME_BYTES:
            raise StreamProtocolError("oversized broker stream frame")
        try:
            message = message.decode("utf-8")
        except UnicodeDecodeError:
            raise StreamProtocolError("malformed broker stream frame") from None
    if len(message.encode("utf-8")) > MAX_FRAME_BYTES:
        raise StreamProtocolError("oversized broker stream frame")
    try:
        payload = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        raise StreamProtocolError("malformed broker stream JSON") from None
    if not isinstance(payload, Mapping):
        raise StreamProtocolError("malformed broker stream envelope")
    return payload


def parse_trade_update(
    message: bytes | str,
    *,
    received_at: datetime | None = None,
) -> TradeUpdate | StreamControl:
    payload = _decode_frame(message)
    stream = payload.get("stream")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise StreamProtocolError("malformed broker stream data")
    if stream == "authorization":
        return StreamControl(kind="authorization", status=str(data.get("status", "unknown")))
    if stream == "listening":
        streams = data.get("streams", [])
        status = ",".join(str(value) for value in streams) if isinstance(streams, list) else "unknown"
        return StreamControl(kind="listening", status=status)
    if stream != "trade_updates":
        raise StreamProtocolError("unsupported broker stream channel")
    order = data.get("order")
    if not isinstance(order, Mapping):
        raise StreamProtocolError("malformed trade update order")
    event = str(data.get("event", ""))
    timestamp = data.get("timestamp") or order.get("updated_at")
    cumulative = order.get("filled_qty", "0")
    event_quantity = data.get("qty", cumulative)
    fill_price = data.get("price") or order.get("filled_avg_price")
    try:
        return TradeUpdate(
            event_id=data.get("execution_id"),
            event=event,
            known_event=event in _KNOWN_EVENTS,
            broker_order_id=order["id"],
            client_order_id=order["client_order_id"],
            status=order["status"],
            symbol=order["symbol"],
            side=order["side"],
            quantity=event_quantity,
            fill_price=fill_price,
            cumulative_filled_quantity=cumulative,
            broker_timestamp=timestamp,
            received_at=received_at or datetime.now(UTC),
            raw_payload_hash=canonical_hash(payload),
        )
    except (KeyError, TypeError, ValueError, ValidationError):
        raise StreamProtocolError("malformed trade update payload") from None


async def _default_connector(url: str):
    from websockets.asyncio.client import connect

    return await connect(
        url, ssl=verified_client_context(), max_size=MAX_FRAME_BYTES, open_timeout=10, ping_interval=20, ping_timeout=20
    )


class AlpacaTradeUpdateStream:
    """Authenticated paper stream with bounded reconnect and secret-free errors."""

    def __init__(
        self,
        *,
        key_id: str,
        secret_key: str,
        connector: Callable[[str], Awaitable[Any]] = _default_connector,
        clock: Callable[[], datetime] | None = None,
        backoff: Callable[[int], float] | None = None,
    ):
        if not key_id or not secret_key:
            raise ValueError("both paper stream credentials are required")
        self._key_id = key_id
        self._secret_key = secret_key
        self._connector = connector
        self._clock = clock or (lambda: datetime.now(UTC))
        self._backoff = backoff or (lambda attempt: min(0.25 * 2**attempt, 5.0))
        self.last_received_at: datetime | None = None

    async def iter_updates(self, stop: Callable[[], bool], *, max_reconnects: int = 5) -> AsyncIterator[TradeUpdate]:
        attempt = 0
        while not stop():
            socket = None
            try:
                socket = await self._connector(PAPER_STREAM_URL)
                await socket.send(json.dumps({"action": "auth", "key": self._key_id, "secret": self._secret_key}))
                authorized = False
                listening = False
                await socket.send(json.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}}))
                async for frame in socket:
                    if stop():
                        return
                    self.last_received_at = self._clock()
                    parsed = parse_trade_update(frame, received_at=self.last_received_at)
                    if isinstance(parsed, StreamControl):
                        if parsed.kind == "authorization":
                            if parsed.status != "authorized":
                                raise StreamProtocolError("paper stream authorization rejected")
                            authorized = True
                        elif parsed.kind == "listening":
                            listening = "trade_updates" in parsed.status
                        continue
                    if not authorized or not listening:
                        raise StreamProtocolError("trade update arrived before stream acknowledgement")
                    yield parsed
                return
            except (TimeoutError, OSError, StreamProtocolError):
                if stop() or attempt >= max_reconnects:
                    raise StreamProtocolError("paper trade stream unavailable after bounded reconnects") from None
                await asyncio.sleep(self._backoff(attempt))
                attempt += 1
            finally:
                if socket is not None:
                    close = getattr(socket, "close", None)
                    if close is not None:
                        result = close()
                        if inspect.isawaitable(result):
                            await result


__all__ = [
    "AlpacaTradeUpdateStream",
    "MAX_FRAME_BYTES",
    "PAPER_STREAM_URL",
    "StreamControl",
    "StreamProtocolError",
    "parse_trade_update",
]
