from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.database.engine import Database
from src.ingestion.bars import INTERVAL_DURATION, BarQuery, MarketBar, require_utc
from src.strategies.calendars import ExpectedBarCalendar, calendar_for
from src.strategies.types import BarInterval, canonical_hash


class DatasetGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: datetime
    end: datetime
    missing_bars: int = Field(gt=0)

    @field_validator("start", "end")
    @classmethod
    def utc_timestamps(cls, value: datetime) -> datetime:
        return require_utc(value)


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_hash: str = Field(min_length=64, max_length=64)
    provider: str
    feed: str
    symbol: str
    interval: BarInterval
    requested_start: datetime
    requested_end: datetime
    coverage_start: datetime | None
    coverage_end: datetime | None
    row_count: int = Field(ge=0)
    gaps: tuple[DatasetGap, ...]
    payload_hashes: tuple[str, ...]
    calendar_id: str
    calendar_version: str

    @field_validator("requested_start", "requested_end", "coverage_start", "coverage_end")
    @classmethod
    def utc_timestamps(cls, value: datetime | None) -> datetime | None:
        return require_utc(value) if value is not None else None

    @property
    def checksum(self) -> str:
        return self.dataset_hash


class BarRepository:
    def __init__(self, database: Database):
        self.database = database

    def append(self, bars: Iterable[MarketBar]) -> int:
        incoming = list(bars)
        if any(not bar.finalized for bar in incoming):
            raise ValueError("only finalized bars may be appended")

        rows: list[dict[str, Any]] = []
        known: dict[tuple[str, str, str, str, datetime], list[tuple[int, str]]] = {}
        for bar in sorted(incoming, key=lambda item: (item.open_timestamp, item.available_at, item.revision)):
            key = (bar.provider, bar.feed, bar.symbol, bar.interval.value, bar.open_timestamp)
            versions = known.get(key)
            if versions is None:
                existing = self.database.frame(
                    "SELECT revision, payload_hash FROM market_bars "
                    "WHERE provider = :provider AND feed = :feed AND symbol = :symbol "
                    "AND interval = :interval AND open_timestamp = :open_timestamp",
                    {
                        "provider": bar.provider,
                        "feed": bar.feed,
                        "symbol": bar.symbol,
                        "interval": bar.interval.value,
                        "open_timestamp": bar.open_timestamp,
                    },
                )
                versions = [(int(row.revision), str(row.payload_hash)) for row in existing.itertuples(index=False)]
                known[key] = versions
            if any(payload_hash == bar.payload_hash for _, payload_hash in versions):
                continue
            max_revision = max((revision for revision, _ in versions), default=0)
            revision = bar.revision if bar.revision > max_revision else max_revision + 1
            available_at = (
                max(bar.available_at, bar.retrieved_at)
                if versions and bar.retrieved_at is not None
                else bar.available_at
            )
            persisted = bar.model_copy(update={"revision": revision, "available_at": available_at})
            rows.append(self._row(persisted))
            versions.append((revision, persisted.payload_hash))
        return self.database.insert("market_bars", rows)

    def bars_as_of(self, request: BarQuery, decision_timestamp: datetime) -> pd.DataFrame:
        decision_timestamp = require_utc(decision_timestamp)
        frame = self._matching_frame(request)
        if frame.empty:
            return frame
        eligible = frame[
            frame["finalized"]
            & (frame["available_at"] <= decision_timestamp)
            & (frame["close_timestamp"] <= decision_timestamp)
        ]
        return self._latest(eligible)

    def revision_ledger_as_of(self, request: BarQuery, decision_timestamp: datetime) -> pd.DataFrame:
        """Return every finalized revision eligible at the point-in-time boundary."""

        decision_timestamp = require_utc(decision_timestamp)
        frame = self._matching_frame(request)
        if frame.empty:
            return frame
        eligible = frame[
            frame["finalized"]
            & (frame["available_at"] <= decision_timestamp)
            & (frame["close_timestamp"] <= decision_timestamp)
        ]
        return eligible.sort_values(["available_at", "open_timestamp", "revision"], kind="stable").reset_index(
            drop=True
        )

    def causal_bars_as_of(self, request: BarQuery, decision_timestamp: datetime) -> pd.DataFrame:
        """Resolve the first observable version of each execution bar without repainting history."""

        ledger = self.revision_ledger_as_of(request, decision_timestamp)
        if ledger.empty:
            return ledger
        return (
            ledger.sort_values(["open_timestamp", "available_at", "revision"], kind="stable")
            .drop_duplicates(["provider", "feed", "symbol", "interval", "open_timestamp"], keep="first")
            .sort_values("open_timestamp", kind="stable")
            .reset_index(drop=True)
        )

    def coverage(self, request: BarQuery) -> tuple[datetime | None, datetime | None]:
        frame = self._latest(self._matching_frame(request))
        if frame.empty:
            return None, None
        return frame.iloc[0].open_timestamp.to_pydatetime(), frame.iloc[-1].close_timestamp.to_pydatetime()

    def gaps(self, request: BarQuery) -> tuple[DatasetGap, ...]:
        frame = self._latest(self._matching_frame(request))
        present = set(frame["open_timestamp"].tolist()) if not frame.empty else set()
        duration = INTERVAL_DURATION[request.interval]
        expected = self.calendar(request).expected_opens(request.start, request.end, request.interval)
        if not expected and len(present) >= 2:
            observed = sorted(timestamp.to_pydatetime() for timestamp in present)
            expected = tuple(
                cursor.to_pydatetime()
                for cursor in pd.date_range(observed[0], observed[-1], freq=duration, tz="UTC")
            )
        gaps: list[DatasetGap] = []
        gap_start: datetime | None = None
        missing = 0
        previous: datetime | None = None
        for cursor in expected:
            if previous is not None and cursor != previous + duration and gap_start is not None:
                gaps.append(DatasetGap(start=gap_start, end=previous + duration, missing_bars=missing))
                gap_start = None
                missing = 0
            if pd.Timestamp(cursor) not in present:
                if gap_start is None:
                    gap_start = cursor
                missing += 1
            elif gap_start is not None:
                gaps.append(DatasetGap(start=gap_start, end=cursor, missing_bars=missing))
                gap_start = None
                missing = 0
            previous = cursor
        if gap_start is not None:
            assert previous is not None
            gaps.append(DatasetGap(start=gap_start, end=previous + duration, missing_bars=missing))
        return tuple(gaps)

    @staticmethod
    def calendar(request: BarQuery) -> ExpectedBarCalendar:
        return calendar_for(request.provider, request.feed)

    def manifest(self, request: BarQuery) -> DatasetManifest:
        frame = self._latest(self._matching_frame(request))
        calendar = self.calendar(request)
        records = [
            {
                "provider": row.provider,
                "feed": row.feed,
                "symbol": row.symbol,
                "interval": row.interval,
                "open_timestamp": self._iso(row.open_timestamp),
                "close_timestamp": self._iso(row.close_timestamp),
                "available_at": self._iso(row.available_at),
                "payload_hash": row.payload_hash,
            }
            for row in frame.itertuples(index=False)
        ]
        dataset_hash = canonical_hash(
            {
                "query": {
                    "provider": request.provider,
                    "feed": request.feed,
                    "symbol": request.symbol,
                    "interval": request.interval,
                    "start": request.start,
                    "end": request.end,
                    "calendar_id": calendar.calendar_id,
                    "calendar_version": calendar.version,
                },
                "bars": records,
            }
        )
        coverage_start, coverage_end = self.coverage(request)
        return DatasetManifest(
            dataset_hash=dataset_hash,
            provider=request.provider,
            feed=request.feed,
            symbol=request.symbol,
            interval=request.interval,
            requested_start=request.start,
            requested_end=request.end,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            row_count=len(frame),
            gaps=self.gaps(request),
            payload_hashes=tuple(str(value) for value in frame.get("payload_hash", [])),
            calendar_id=calendar.calendar_id,
            calendar_version=calendar.version,
        )

    def _matching_frame(self, request: BarQuery) -> pd.DataFrame:
        frame = self.database.frame(
            "SELECT * FROM market_bars WHERE provider = :provider AND feed = :feed "
            "AND symbol = :symbol AND interval = :interval "
            "AND open_timestamp >= :start AND open_timestamp < :end",
            {
                "provider": request.provider,
                "feed": request.feed,
                "symbol": request.symbol,
                "interval": request.interval.value,
                "start": request.start,
                "end": request.end,
            },
        )
        for column in ("open_timestamp", "close_timestamp", "available_at", "created_at"):
            if column in frame:
                frame[column] = pd.to_datetime(frame[column], utc=True)
        return frame

    @staticmethod
    def _latest(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame.reset_index(drop=True)
        return (
            frame.sort_values(["open_timestamp", "available_at", "revision"])
            .drop_duplicates(["provider", "feed", "symbol", "interval", "open_timestamp"], keep="last")
            .sort_values("open_timestamp")
            .reset_index(drop=True)
        )

    @staticmethod
    def _row(bar: MarketBar) -> dict[str, Any]:
        return {
            "bar_id": bar.bar_id,
            "provider": bar.provider,
            "feed": bar.feed,
            "symbol": bar.symbol,
            "interval": bar.interval.value,
            "open_timestamp": bar.open_timestamp,
            "close_timestamp": bar.close_timestamp,
            "available_at": bar.available_at,
            "revision": bar.revision,
            "finalized": bar.finalized,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "vwap": bar.vwap,
            "trade_count": bar.trade_count,
            "payload_hash": bar.payload_hash,
            "source": bar.provider,
            "source_version": bar.payload_hash,
            "created_at": datetime.now(UTC),
        }

    @staticmethod
    def _iso(value: Any) -> str:
        return pd.Timestamp(value).to_pydatetime().astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["BarRepository", "DatasetGap", "DatasetManifest"]
