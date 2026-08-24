from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.trading.stream import (
    AlpacaTradeUpdateStream,
    StreamControl,
    StreamProtocolError,
    parse_trade_update,
)
from src.trading.types import BrokerOrderStatus, TradeUpdate

FIXTURE = Path(__file__).parents[1] / "fixtures" / "trading" / "alpaca_trade_updates.json"
NOW = datetime(2026, 8, 24, 14, 31, tzinfo=UTC)


def _message(event: str) -> str:
    payload = json.loads(FIXTURE.read_text())
    payload["data"]["event"] = event
    payload["data"]["order"]["status"] = {
        "partial_fill": "partially_filled",
        "fill": "filled",
        "cancel_rejected": "new",
    }.get(event, event)
    return json.dumps(payload)


@pytest.mark.parametrize(
    "event",
    [
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
    ],
)
def test_documented_trade_updates_round_trip(event: str) -> None:
    update = parse_trade_update(_message(event), received_at=NOW)
    assert isinstance(update, TradeUpdate)
    assert update.event == event and update.known_event
    assert update.received_at == NOW


def test_control_unknown_binary_and_protocol_failures() -> None:
    assert parse_trade_update('{"stream":"authorization","data":{"status":"authorized"}}') == StreamControl(
        kind="authorization", status="authorized"
    )
    assert parse_trade_update(b'{"stream":"listening","data":{"streams":["trade_updates"]}}') == StreamControl(
        kind="listening", status="trade_updates"
    )
    unknown_payload = json.loads(_message("future_event"))
    unknown_payload["data"]["order"]["status"] = "new"
    unknown = parse_trade_update(json.dumps(unknown_payload), received_at=NOW)
    assert isinstance(unknown, TradeUpdate) and not unknown.known_event
    with pytest.raises(StreamProtocolError, match="malformed"):
        parse_trade_update("not-json")
    with pytest.raises(StreamProtocolError, match="oversized"):
        parse_trade_update("x" * 1_000_001)


class _Socket:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.frames)
        except StopIteration:
            raise StopAsyncIteration from None


def test_stream_authenticates_listens_and_yields_only_updates() -> None:
    socket = _Socket(
        [
            '{"stream":"authorization","data":{"status":"authorized"}}',
            '{"stream":"listening","data":{"streams":["trade_updates"]}}',
            _message("fill"),
        ]
    )

    async def connector(_url: str):
        return socket

    stream = AlpacaTradeUpdateStream(
        key_id="paper-key",
        secret_key="secret",
        connector=connector,
        clock=lambda: NOW,
    )

    async def collect():
        return [item async for item in stream.iter_updates(stop=lambda: False)]

    updates = asyncio.run(collect())
    assert updates[0].status is BrokerOrderStatus.FILLED
    assert socket.sent == [
        {"action": "auth", "key": "paper-key", "secret": "secret"},
        {"action": "listen", "data": {"streams": ["trade_updates"]}},
    ]
