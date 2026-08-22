from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

import pandas as pd


@dataclass(frozen=True, slots=True)
class SessionCalendar:
    """A deterministic recurring session calendar evaluated one timestamp at a time."""

    timezone: str = "UTC"
    open_time: time = time(0, 0)
    close_time: time = time(0, 0)
    continuous: bool = True

    @classmethod
    def continuous_utc(cls) -> SessionCalendar:
        return cls()

    @classmethod
    def equity_us(cls) -> SessionCalendar:
        return cls(
            timezone="America/New_York",
            open_time=time(9, 30),
            close_time=time(16, 0),
            continuous=False,
        )

    def session_labels(self, timestamps: pd.Series) -> pd.Series:
        local = self._local(timestamps)
        return pd.Series(local.dt.date, index=timestamps.index, dtype="object")

    def opening_range(self, timestamps: pd.Series, minutes: int) -> pd.Series:
        if minutes <= 0:
            raise ValueError("session window minutes must be positive")
        elapsed = self._minutes_from_open(timestamps)
        return elapsed.ge(0) & elapsed.lt(minutes)

    def last_window(self, timestamps: pd.Series, minutes: int) -> pd.Series:
        if minutes <= 0:
            raise ValueError("session window minutes must be positive")
        if self.continuous:
            local = self._local(timestamps)
            minute = local.dt.hour * 60 + local.dt.minute
            return minute.ge(24 * 60 - minutes)
        remaining = self._minutes_to_close(timestamps)
        return remaining.ge(0) & remaining.le(minutes)

    def active_window(self, timestamps: pd.Series, start_hour: int = 7, end_hour: int = 16) -> pd.Series:
        if not 0 <= start_hour < end_hour <= 24:
            raise ValueError("active-session hours must be ordered within a UTC day")
        utc = pd.to_datetime(timestamps, utc=True)
        return utc.dt.hour.ge(start_hour) & utc.dt.hour.lt(end_hour)

    def _local(self, timestamps: pd.Series) -> pd.Series:
        utc = pd.to_datetime(timestamps, utc=True)
        return utc.dt.tz_convert(ZoneInfo(self.timezone))

    def _minutes_from_open(self, timestamps: pd.Series) -> pd.Series:
        local = self._local(timestamps)
        minute = local.dt.hour * 60 + local.dt.minute
        opening = self.open_time.hour * 60 + self.open_time.minute
        return minute - opening

    def _minutes_to_close(self, timestamps: pd.Series) -> pd.Series:
        local = self._local(timestamps)
        minute = local.dt.hour * 60 + local.dt.minute
        closing = self.close_time.hour * 60 + self.close_time.minute
        return closing - minute


__all__ = ["SessionCalendar"]
