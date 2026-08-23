from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
)

from src.strategies.types import BarInterval


def _xnys_new_year_observance(value: datetime) -> datetime:
    """XNYS observes Sunday New Year's Day on Monday, but not Saturday on Friday."""

    return value + timedelta(days=1) if value.weekday() == 6 else value


@dataclass(frozen=True, slots=True)
class ExpectedBarCalendar:
    calendar_id: str
    version: str

    def expected_opens(
        self,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> tuple[datetime, ...]:
        raise NotImplementedError

    def close_for(self, open_timestamp: datetime, interval: BarInterval) -> datetime:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ContinuousCalendar(ExpectedBarCalendar):
    def expected_opens(
        self,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> tuple[datetime, ...]:
        duration = _duration(interval)
        cursor = start
        result: list[datetime] = []
        while cursor < end:
            result.append(cursor)
            cursor += duration
        return tuple(result)

    def close_for(self, open_timestamp: datetime, interval: BarInterval) -> datetime:
        return open_timestamp + _duration(interval)


class _XNYSRegularHolidays(AbstractHolidayCalendar):
    rules = [
        Holiday("New Year's Day", month=1, day=1, observance=_xnys_new_year_observance),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday(
            "Juneteenth",
            month=6,
            day=19,
            start_date="2022-01-01",
            observance=nearest_workday,
        ),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas Day", month=12, day=25, observance=nearest_workday),
    ]


@dataclass(frozen=True, slots=True)
class XNYSCalendar(ExpectedBarCalendar):
    timezone: ZoneInfo = ZoneInfo("America/New_York")

    def expected_opens(
        self,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> tuple[datetime, ...]:
        first_day = start.astimezone(self.timezone).date() - timedelta(days=1)
        last_day = end.astimezone(self.timezone).date() + timedelta(days=1)
        expected: list[datetime] = []
        current_day = first_day
        while current_day <= last_day:
            session = self._session(current_day)
            if session is not None:
                opened, closed = session
                if interval is BarInterval.ONE_DAY:
                    label = datetime.combine(current_day, time(0), self.timezone).astimezone(UTC)
                    if start <= label < end:
                        expected.append(label)
                else:
                    duration = _duration(interval)
                    cursor = _floor_utc(opened, duration)
                    while cursor < closed:
                        if cursor + duration > opened and start <= cursor < end:
                            expected.append(cursor)
                        cursor += duration
            current_day += timedelta(days=1)
        return tuple(expected)

    def close_for(self, open_timestamp: datetime, interval: BarInterval) -> datetime:
        local_day = open_timestamp.astimezone(self.timezone).date()
        session = self._session(local_day)
        if session is None:
            return open_timestamp + _duration(interval)
        opened, closed = session
        if interval is BarInterval.ONE_DAY:
            return closed
        bucket_close = open_timestamp + _duration(interval)
        if bucket_close <= opened or open_timestamp >= closed:
            return bucket_close
        return min(bucket_close, closed)

    def _session(self, session_date: date) -> tuple[datetime, datetime] | None:
        if session_date.weekday() >= 5 or session_date in self._holidays(session_date):
            return None
        opened = datetime.combine(session_date, time(9, 30), self.timezone).astimezone(UTC)
        close_time = time(13) if self._is_early_close(session_date) else time(16)
        closed = datetime.combine(session_date, close_time, self.timezone).astimezone(UTC)
        return opened, closed

    @staticmethod
    def _holidays(session_date: date) -> set[date]:
        return {
            timestamp.date()
            for timestamp in _XNYSRegularHolidays().holidays(
                start=pd.Timestamp(session_date),
                end=pd.Timestamp(session_date),
            )
        }

    @staticmethod
    def _is_early_close(session_date: date) -> bool:
        thanksgiving = max(
            day for day in (date(session_date.year, 11, value) for value in range(22, 29)) if day.weekday() == 3
        )
        return session_date in {
            thanksgiving + timedelta(days=1),
            date(session_date.year, 7, 3),
            date(session_date.year, 12, 24),
        }


CONTINUOUS_CALENDAR = ContinuousCalendar("24x7", "continuous-v1")
XNYS_CALENDAR = XNYSCalendar("XNYS", "offline-rules-2026.3")


def calendar_for(provider: str, feed: str) -> ExpectedBarCalendar:
    del feed
    return XNYS_CALENDAR if provider.strip().lower() == "alpaca" else CONTINUOUS_CALENDAR


def _duration(interval: BarInterval) -> timedelta:
    from src.ingestion.bars import INTERVAL_DURATION

    return INTERVAL_DURATION[interval]


def _floor_utc(value: datetime, duration: timedelta) -> datetime:
    seconds = int(duration.total_seconds())
    epoch_seconds = int(value.timestamp())
    return datetime.fromtimestamp(epoch_seconds - epoch_seconds % seconds, tz=UTC)


__all__ = [
    "CONTINUOUS_CALENDAR",
    "ExpectedBarCalendar",
    "XNYS_CALENDAR",
    "calendar_for",
]
