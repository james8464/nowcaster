from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.ingestion.bars import (
    INTERVAL_DURATION,
    BarRequest,
    MarketBar,
    atomic_write_bytes,
    deduplicate_bars,
    request_with_retries,
    require_utc,
)
from src.strategies.types import BarInterval, canonical_hash

ALPACA_TIMEFRAMES = {
    BarInterval.ONE_MINUTE: "1Min",
    BarInterval.FIVE_MINUTES: "5Min",
    BarInterval.FIFTEEN_MINUTES: "15Min",
    BarInterval.THIRTY_MINUTES: "30Min",
    BarInterval.ONE_HOUR: "1Hour",
    BarInterval.FOUR_HOURS: "4Hour",
    BarInterval.ONE_DAY: "1Day",
}


class AlpacaBarProvider:
    def __init__(
        self,
        client: httpx.Client,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        feed: str = "iex",
        base_url: str = "https://data.alpaca.markets",
        max_attempts: int = 3,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        cache_dir: Path | None = None,
    ):
        self.client = client
        self.api_key = api_key or os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
        self.api_secret = api_secret or os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_API_SECRET")
        if not self.api_key or not self.api_secret:
            raise ValueError("Alpaca credentials are required")
        self.feed = feed.strip().lower()
        self.base_url = base_url.rstrip("/")
        self.max_attempts = max_attempts
        self.clock = clock
        self.sleep = sleep
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None

    def fetch(self, request: BarRequest) -> Iterable[MarketBar]:
        feed = (request.feed or self.feed).strip().lower()
        retrieved_at = require_utc(self.clock())
        page_token: str | None = None
        seen_tokens: set[str] = set()
        bars: list[MarketBar] = []

        while True:
            params: dict[str, object] = {
                "timeframe": ALPACA_TIMEFRAMES[request.interval],
                "start": request.start.isoformat().replace("+00:00", "Z"),
                "end": request.end.isoformat().replace("+00:00", "Z"),
                "limit": min(request.page_size, 10_000),
                "adjustment": "raw",
                "feed": feed,
                "sort": "asc",
            }
            if page_token is not None:
                params["page_token"] = page_token
            response = request_with_retries(
                self.client,
                "GET",
                f"{self.base_url}/v2/stocks/{request.symbol}/bars",
                params=params,
                headers={
                    "APCA-API-KEY-ID": self.api_key,
                    "APCA-API-SECRET-KEY": self.api_secret,
                },
                max_attempts=self.max_attempts,
                sleep=self.sleep,
            )
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("bars"), list):
                raise ValueError("Alpaca bars response must contain a bars list")
            self._cache_payload(request, page_token, response.content)
            for raw in payload["bars"]:
                if not isinstance(raw, dict) or not {"c", "h", "l", "n", "o", "t", "v", "vw"} <= raw.keys():
                    raise ValueError("Alpaca bar is missing documented fields")
                open_timestamp = datetime.fromisoformat(str(raw["t"]).replace("Z", "+00:00")).astimezone(UTC)
                if open_timestamp < request.start or open_timestamp >= request.end:
                    continue
                close_timestamp = open_timestamp + INTERVAL_DURATION[request.interval]
                if close_timestamp > retrieved_at:
                    continue
                bars.append(
                    MarketBar(
                        provider="alpaca",
                        feed=feed,
                        symbol=request.symbol,
                        interval=request.interval,
                        open_timestamp=open_timestamp,
                        close_timestamp=close_timestamp,
                        available_at=close_timestamp,
                        retrieved_at=retrieved_at,
                        revision=1,
                        finalized=True,
                        open=float(raw["o"]),
                        high=float(raw["h"]),
                        low=float(raw["l"]),
                        close=float(raw["c"]),
                        volume=float(raw["v"]),
                        vwap=float(raw["vw"]) if raw["vw"] is not None else None,
                        trade_count=int(raw["n"]) if raw["n"] is not None else None,
                        payload_hash=canonical_hash(raw),
                    )
                )
            next_token = payload.get("next_page_token")
            if next_token is None:
                break
            next_token = str(next_token)
            if next_token in seen_tokens:
                raise ValueError("Alpaca returned a repeated page cursor")
            seen_tokens.add(next_token)
            page_token = next_token
        return deduplicate_bars(bars)

    def _cache_payload(self, request: BarRequest, page_token: str | None, payload: bytes) -> None:
        if self.cache_dir is None:
            return
        digest = canonical_hash([request.symbol, request.interval, page_token, payload.hex()])
        atomic_write_bytes(self.cache_dir / "alpaca" / f"{digest}.json", payload)


__all__ = ["AlpacaBarProvider"]
