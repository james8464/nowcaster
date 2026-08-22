from __future__ import annotations

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
from src.strategies.types import canonical_hash


class BinanceBarProvider:
    def __init__(
        self,
        client: httpx.Client,
        *,
        feed: str = "spot",
        base_url: str = "https://api.binance.com",
        max_attempts: int = 3,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        cache_dir: Path | None = None,
    ):
        self.client = client
        self.feed = feed.strip().lower()
        if self.feed != "spot":
            raise ValueError("BinanceBarProvider supports only the spot feed")
        self.base_url = base_url.rstrip("/")
        self.max_attempts = max_attempts
        self.clock = clock
        self.sleep = sleep
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None

    def fetch(self, request: BarRequest) -> Iterable[MarketBar]:
        feed = (request.feed or self.feed).strip().lower()
        if feed != "spot":
            raise ValueError("BinanceBarProvider supports only the spot feed")
        cursor_ms = int(request.start.timestamp() * 1_000)
        end_ms = int(request.end.timestamp() * 1_000)
        interval = INTERVAL_DURATION[request.interval]
        interval_ms = int(interval.total_seconds() * 1_000)
        available_at = require_utc(self.clock())
        bars: list[MarketBar] = []

        while cursor_ms < end_ms:
            response = request_with_retries(
                self.client,
                "GET",
                f"{self.base_url}/api/v3/klines",
                params={
                    "symbol": request.symbol,
                    "interval": request.interval.value,
                    "startTime": cursor_ms,
                    "endTime": end_ms - 1,
                    "limit": min(request.page_size, 1_000),
                },
                max_attempts=self.max_attempts,
                sleep=self.sleep,
            )
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Binance kline response must be a list")
            self._cache_payload(request, cursor_ms, response.content)
            if not payload:
                break
            last_open_ms = cursor_ms - interval_ms
            for raw in payload:
                if not isinstance(raw, list) or len(raw) != 12:
                    raise ValueError("Binance kline must contain all 12 documented fields")
                open_ms = int(raw[0])
                last_open_ms = max(last_open_ms, open_ms)
                if open_ms < int(request.start.timestamp() * 1_000) or open_ms >= end_ms:
                    continue
                open_timestamp = datetime.fromtimestamp(open_ms / 1_000, UTC)
                close_timestamp = datetime.fromtimestamp((int(raw[6]) + 1) / 1_000, UTC)
                if close_timestamp > available_at:
                    continue
                bars.append(
                    MarketBar(
                        provider="binance",
                        feed=feed,
                        symbol=request.symbol,
                        interval=request.interval,
                        open_timestamp=open_timestamp,
                        close_timestamp=close_timestamp,
                        available_at=available_at,
                        revision=1,
                        finalized=True,
                        open=float(raw[1]),
                        high=float(raw[2]),
                        low=float(raw[3]),
                        close=float(raw[4]),
                        volume=float(raw[5]),
                        vwap=None,
                        trade_count=int(raw[8]),
                        payload_hash=canonical_hash(raw),
                    )
                )
            next_cursor = last_open_ms + interval_ms
            if len(payload) < min(request.page_size, 1_000) or next_cursor <= cursor_ms:
                break
            cursor_ms = next_cursor
        return deduplicate_bars(bars)

    def _cache_payload(self, request: BarRequest, cursor_ms: int, payload: bytes) -> None:
        if self.cache_dir is None:
            return
        digest = canonical_hash([request.symbol, request.interval, cursor_ms, payload.hex()])
        atomic_write_bytes(self.cache_dir / "binance" / f"{digest}.json", payload)


__all__ = ["BinanceBarProvider"]
