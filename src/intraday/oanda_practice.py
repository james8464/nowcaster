"""OANDA practice-market-data boundary; this module exposes no order operations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from src.intraday.contracts import ConfirmedBar, InstrumentSpec, MarketQuote

PRACTICE_API = "https://api-fxpractice.oanda.com"
PRACTICE_STREAM = "https://stream-fxpractice.oanda.com"


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("request time must be explicit UTC")
    return value.isoformat().replace("+00:00", "Z")


class OandaPracticeFeed:
    def __init__(self, account_id: str, token: str, *, transport: httpx.BaseTransport | None = None):
        if not account_id.strip():
            raise ValueError("practice account ID required")
        if not token.strip():
            raise ValueError("practice token required")
        self.account_id = account_id.strip()
        self._token = token.strip()
        self._transport = transport

    def _client(self, *, stream: bool = False) -> httpx.Client:
        return httpx.Client(
            base_url=PRACTICE_STREAM if stream else PRACTICE_API,
            headers={"Authorization": f"Bearer {self._token}"},
            transport=self._transport,
            timeout=30,
        )

    def available_instruments(self) -> tuple[dict, ...]:
        """Return broker identities to verify before declaring a market in a round."""
        with self._client() as client:
            response = client.get(f"/v3/accounts/{self.account_id}/instruments")
            response.raise_for_status()
            return tuple(response.json()["instruments"])

    def verify_instrument(self, instrument: InstrumentSpec) -> dict:
        """Fail closed until the exact product exists in the practice account."""
        required_type = "CURRENCY" if instrument.product == "margin_fx" else "CFD"
        for row in self.available_instruments():
            if row.get("name") == instrument.broker_symbol and row.get("type") == required_type:
                return row
        raise ValueError(f"broker {instrument.product} instrument not available: {instrument.broker_symbol}")

    def fetch_candles(
        self, instrument: InstrumentSpec, start: datetime, end: datetime, *, received_at: datetime
    ) -> tuple[ConfirmedBar, ...]:
        if end <= start:
            raise ValueError("candle range must be positive")
        _iso(start)
        _iso(end)
        _iso(received_at)
        rows: list[ConfirmedBar] = []
        cursor = start
        # OANDA limits each candle response to 5,000; ten days is at most
        # 2,880 five-minute intervals and avoids silent provider truncation.
        with self._client() as client:
            while cursor < end:
                boundary = min(cursor + timedelta(days=10), end)
                response = client.get(
                    f"/v3/instruments/{instrument.broker_symbol}/candles",
                    params={"from": _iso(cursor), "to": _iso(boundary), "granularity": "M5", "price": "BA"},
                )
                response.raise_for_status()
                for candle in response.json()["candles"]:
                    if not candle.get("complete", False):
                        continue
                    began = _timestamp(candle["time"])
                    # The provider may include the candle containing an unaligned
                    # `from` timestamp; it predates the requested research window.
                    if began < cursor and began + timedelta(minutes=5) > cursor:
                        continue
                    if not cursor <= began < boundary:
                        raise ValueError("provider returned candle outside requested range")
                    values = {
                        f"{side}_{name}": Decimal(candle[side][letter])
                        for side in ("bid", "ask")
                        for name, letter in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))
                    }
                    rows.append(
                        ConfirmedBar(
                            instrument=instrument,
                            start=began,
                            end=began + timedelta(minutes=5),
                            available_at=received_at,
                            source_key=f"oanda:{instrument.broker_symbol}:{_iso(began)}",
                            **values,
                        )
                    )
                cursor = boundary
        return tuple(sorted(rows, key=lambda row: row.start))

    def parse_price_event(self, instrument: InstrumentSpec, line: str, *, received_at: datetime) -> MarketQuote | None:
        event = json.loads(line)
        if event.get("type") == "HEARTBEAT":
            return None
        if event.get("type") != "PRICE" or event.get("instrument") != instrument.broker_symbol:
            raise ValueError("price event instrument/type mismatch")
        if not event.get("bids") or not event.get("asks"):
            raise ValueError("account price has no executable bid and ask")
        observed = _timestamp(event["time"])
        tradeable = event.get("tradeable")
        if tradeable is None:
            tradeable = event.get("status") == "tradeable"
        return MarketQuote(
            instrument=instrument,
            account_feed_hash=hashlib.sha256(self.account_id.encode()).hexdigest(),
            observed_at=observed,
            received_at=received_at,
            bid=Decimal(event["bids"][0]["price"]),
            ask=Decimal(event["asks"][0]["price"]),
            status="tradeable" if tradeable is True else "non_tradeable",
            source_key=f"oanda-account:{instrument.broker_symbol}:{_iso(observed)}:{event['bids'][0]['price']}:{event['asks'][0]['price']}",
        )

    def price_lines(self, instruments: tuple[InstrumentSpec, ...]):
        """Yield raw account-specific events; caller owns durable capture and reconnects."""
        if not instruments:
            raise ValueError("at least one instrument required")
        with (
            self._client(stream=True) as client,
            client.stream(
                "GET",
                f"/v3/accounts/{self.account_id}/pricing/stream",
                params={"instruments": ",".join(item.broker_symbol for item in instruments)},
            ) as response,
        ):
            response.raise_for_status()
            yield from response.iter_lines()
