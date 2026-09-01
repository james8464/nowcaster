from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.live_monitor.types import BarIntervalValue, MarketBar

_INTERVAL_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1_440,
}


@dataclass(frozen=True, slots=True)
class BarAcceptance:
    status: str
    bar_id: str
    previous_bar_id: str | None = None


@dataclass(frozen=True, slots=True)
class BarRange:
    start: datetime
    end: datetime


def _natural_key(bar: MarketBar) -> tuple[str, str, str, str, datetime, datetime]:
    return (bar.provider, bar.feed, bar.symbol, bar.interval, bar.start, bar.end)


class FinalizedBarLedger:
    def __init__(self, *, maximum_bars: int = 20_000) -> None:
        if maximum_bars < 100:
            raise ValueError("finalized-bar retention must be at least 100")
        self._maximum_bars = maximum_bars
        self._bars: list[MarketBar] = []
        self._ids: set[str] = set()
        self._latest: dict[tuple[str, str, str, str, datetime, datetime], MarketBar] = {}

    @property
    def bars(self) -> tuple[MarketBar, ...]:
        return tuple(self._bars)

    def accept(self, bar: MarketBar) -> BarAcceptance:
        if bar.bar_id in self._ids:
            return BarAcceptance("duplicate", bar.bar_id)
        key = _natural_key(bar)
        previous = self._latest.get(key)
        if previous is not None and bar.revision <= previous.revision:
            raise ValueError("changed bar content requires a strictly increasing revision")
        self._bars.append(bar)
        self._ids.add(bar.bar_id)
        self._latest[key] = bar
        while len(self._bars) > self._maximum_bars:
            removed = self._bars.pop(0)
            self._ids.discard(removed.bar_id)
            removed_key = _natural_key(removed)
            if self._latest.get(removed_key) is removed:
                self._latest.pop(removed_key, None)
        if previous is None:
            return BarAcceptance("accepted", bar.bar_id)
        return BarAcceptance("revised", bar.bar_id, previous.bar_id)


def _bucket_start(value: datetime, minutes: int) -> datetime:
    seconds = minutes * 60
    bucket = int(value.timestamp()) // seconds * seconds
    return datetime.fromtimestamp(bucket, tz=value.tzinfo)


def aggregate_finalized(
    bars: Sequence[MarketBar],
    interval: BarIntervalValue,
) -> tuple[MarketBar, ...]:
    minutes = _INTERVAL_MINUTES[interval]
    if minutes == 1:
        return tuple(sorted(bars, key=lambda item: (item.start, item.revision)))
    latest: dict[tuple[str, str, str, datetime], MarketBar] = {}
    for bar in bars:
        if bar.interval != "1m":
            raise ValueError("aggregation input must contain only finalized one-minute bars")
        key = (bar.provider, bar.feed, bar.symbol, bar.start)
        current = latest.get(key)
        if current is None or bar.revision > current.revision:
            latest[key] = bar

    groups: dict[tuple[str, str, str, datetime], list[MarketBar]] = {}
    for bar in latest.values():
        bucket = _bucket_start(bar.start, minutes)
        groups.setdefault((bar.provider, bar.feed, bar.symbol, bucket), []).append(bar)

    result: list[MarketBar] = []
    duration = timedelta(minutes=minutes)
    for (provider, feed, symbol, bucket), members in sorted(groups.items(), key=lambda item: item[0]):
        ordered = sorted(members, key=lambda item: item.start)
        expected_starts = tuple(bucket + timedelta(minutes=index) for index in range(minutes))
        if tuple(item.start for item in ordered) != expected_starts:
            continue
        result.append(
            MarketBar(
                provider=provider,
                feed=feed,
                symbol=symbol,
                interval=interval,
                start=bucket,
                end=bucket + duration,
                available_at=max(item.available_at for item in ordered),
                received_at=max(item.received_at for item in ordered),
                processed_at=max(item.processed_at for item in ordered),
                open=ordered[0].open,
                high=max(item.high for item in ordered),
                low=min(item.low for item in ordered),
                close=ordered[-1].close,
                volume=sum((item.volume for item in ordered), Decimal(0)),
                finalized=True,
                revision=max(item.revision for item in ordered),
            )
        )
    return tuple(result)


def missing_ranges(
    bars: Sequence[MarketBar],
    *,
    start: datetime,
    end: datetime,
    interval: BarIntervalValue,
    maximum_ranges: int = 128,
) -> tuple[BarRange, ...]:
    if end <= start:
        raise ValueError("missing-range end must follow start")
    if maximum_ranges < 1:
        raise ValueError("maximum_ranges must be positive")
    step = timedelta(minutes=_INTERVAL_MINUTES[interval])
    observed = {bar.start for bar in bars if bar.interval == interval and start <= bar.start < end}
    missing: list[datetime] = []
    cursor = start
    while cursor < end:
        if cursor not in observed:
            missing.append(cursor)
        cursor += step
    if not missing:
        return ()

    ranges: list[BarRange] = []
    range_start = missing[0]
    previous = missing[0]
    for value in missing[1:]:
        if value != previous + step:
            ranges.append(BarRange(range_start, previous + step))
            if len(ranges) >= maximum_ranges:
                return tuple(ranges)
            range_start = value
        previous = value
    ranges.append(BarRange(range_start, previous + step))
    return tuple(ranges[:maximum_ranges])


__all__ = ["BarAcceptance", "BarRange", "FinalizedBarLedger", "aggregate_finalized", "missing_ranges"]
