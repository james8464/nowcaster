from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from src.ingestion.bars import INTERVAL_DURATION, BarRequest, MarketBar, deduplicate_bars, require_utc
from src.strategies.types import canonical_hash

REQUIRED_COLUMNS = {
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "finalized",
    "available_at",
    "revision",
}


class CSVBarProvider:
    def __init__(self, path: str | Path, *, provider: str = "csv", feed: str = "local"):
        self.path = Path(path)
        self.provider = provider
        self.feed = feed

    def fetch(self, request: BarRequest) -> Iterable[MarketBar]:
        bars: list[MarketBar] = []
        with self.path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"Bar CSV is missing columns: {sorted(missing)}")
            for raw in reader:
                finalized = str(raw["finalized"]).strip().lower() in {"1", "true", "yes"}
                if not finalized:
                    continue
                open_timestamp = require_utc(datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00")))
                if open_timestamp < request.start or open_timestamp >= request.end:
                    continue
                bars.append(
                    MarketBar(
                        provider=self.provider,
                        feed=request.feed or self.feed,
                        symbol=request.symbol,
                        interval=request.interval,
                        open_timestamp=open_timestamp,
                        close_timestamp=open_timestamp + INTERVAL_DURATION[request.interval],
                        available_at=require_utc(
                            datetime.fromisoformat(raw["available_at"].replace("Z", "+00:00"))
                        ),
                        revision=int(raw["revision"]),
                        finalized=True,
                        open=float(raw["open"]),
                        high=float(raw["high"]),
                        low=float(raw["low"]),
                        close=float(raw["close"]),
                        volume=float(raw["volume"]),
                        vwap=float(raw["vwap"]) if raw.get("vwap") else None,
                        trade_count=int(raw["trade_count"]) if raw.get("trade_count") else None,
                        payload_hash=canonical_hash(raw),
                    )
                )
        return deduplicate_bars(bars)


__all__ = ["CSVBarProvider"]
