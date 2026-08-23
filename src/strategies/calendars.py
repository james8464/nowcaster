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


class _XNYSRegularHolidays(AbstractHolidayCalendar):
    rules = [
        Holiday("New Year's Day", month=1, day=1, observance=nearest_workday),
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
        duration = _duration(interval)
        first_day = start.astimezone(self.timezone).date() - timedelta(days=1)
        last_day = end.astimezone(self.timezone).date() + timedelta(days=1)
        holidays = {
            timestamp.date()
            for timestamp in _XNYSRegularHolidays().holidays(
                start=pd.Timestamp(first_day),
                end=pd.Timestamp(last_day),
            )
        }
        expected: list[datetime] = []
        current_day = first_day
        while current_day <= last_day:
            if current_day.weekday() < 5 and current_day not in holidays:
                opened = datetime.combine(current_day, time(9, 30), self.timezone).astimezone(UTC)
                closed = datetime.combine(current_day, time(16), self.timezone).astimezone(UTC)
                cursor = opened
                while cursor + duration <= closed:
                    if start <= cursor < end:
                        expected.append(cursor)
                    cursor += duration
            current_day += timedelta(days=1)
        return tuple(expected)


CONTINUOUS_CALENDAR = ContinuousCalendar("24x7", "continuous-v1")
XNYS_CALENDAR = XNYSCalendar("XNYS", "offline-rules-2026.1")


def calendar_for(provider: str, feed: str) -> ExpectedBarCalendar:
    del feed
    return XNYS_CALENDAR if provider.strip().lower() == "alpaca" else CONTINUOUS_CALENDAR


def _duration(interval: BarInterval) -> timedelta:
    from src.ingestion.bars import INTERVAL_DURATION

    return INTERVAL_DURATION[interval]


__all__ = [
    "CONTINUOUS_CALENDAR",
    "ExpectedBarCalendar",
    "XNYS_CALENDAR",
    "calendar_for",
]
