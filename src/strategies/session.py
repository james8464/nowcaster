from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

import pandas as pd

from src.strategies.calendars import XNYS_CALENDAR


@dataclass(frozen=True, slots=True)
class SessionCalendar:
    """A deterministic recurring session calendar evaluated one timestamp at a time."""

    timezone: str = "UTC"
    open_time: time = time(0, 0)
    close_time: time = time(0, 0)
    continuous: bool = True
    calendar_id: str = "24x7"

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
            calendar_id="XNYS",
        )

    def session_labels(self, timestamps: pd.Series) -> pd.Series:
        local = self._local(timestamps)
        if self.continuous:
            return pd.Series(local.dt.date, index=timestamps.index, dtype="object")
        bounds = self._bounds(timestamps)
        return bounds["open"].where(self.in_session(timestamps))

    def in_session(self, timestamps: pd.Series) -> pd.Series:
        if self.continuous:
            return pd.Series(True, index=timestamps.index, dtype=bool)
        utc = pd.to_datetime(timestamps, utc=True)
        bounds = self._bounds(timestamps)
        return bounds["open"].notna() & utc.ge(bounds["open"]) & utc.lt(bounds["close"])

    def opening_range(self, timestamps: pd.Series, minutes: int) -> pd.Series:
        if minutes <= 0:
            raise ValueError("session window minutes must be positive")
        elapsed = self._minutes_from_open(timestamps)
        return self.in_session(timestamps) & elapsed.ge(0) & elapsed.lt(minutes)

    def last_window(self, timestamps: pd.Series, minutes: int) -> pd.Series:
        if minutes <= 0:
            raise ValueError("session window minutes must be positive")
        if self.continuous:
            local = self._local(timestamps)
            minute = local.dt.hour * 60 + local.dt.minute
            return minute.ge(24 * 60 - minutes)
        remaining = self._minutes_to_close(timestamps)
        return self.in_session(timestamps) & remaining.gt(0) & remaining.le(minutes)

    def active_window(self, timestamps: pd.Series, start_hour: int = 7, end_hour: int = 16) -> pd.Series:
        if not 0 <= start_hour < end_hour <= 24:
            raise ValueError("active-session hours must be ordered within a UTC day")
        utc = pd.to_datetime(timestamps, utc=True)
        return utc.dt.hour.ge(start_hour) & utc.dt.hour.lt(end_hour)

    def _local(self, timestamps: pd.Series) -> pd.Series:
        utc = pd.to_datetime(timestamps, utc=True)
        return utc.dt.tz_convert(ZoneInfo(self.timezone))

    def _minutes_from_open(self, timestamps: pd.Series) -> pd.Series:
        if not self.continuous and self.calendar_id == "XNYS":
            utc = pd.to_datetime(timestamps, utc=True)
            return (utc - self._bounds(timestamps)["open"]).dt.total_seconds() / 60
        local = self._local(timestamps)
        minute = local.dt.hour * 60 + local.dt.minute
        opening = self.open_time.hour * 60 + self.open_time.minute
        return minute - opening

    def _minutes_to_close(self, timestamps: pd.Series) -> pd.Series:
        if not self.continuous and self.calendar_id == "XNYS":
            utc = pd.to_datetime(timestamps, utc=True)
            return (self._bounds(timestamps)["close"] - utc).dt.total_seconds() / 60
        local = self._local(timestamps)
        minute = local.dt.hour * 60 + local.dt.minute
        closing = self.close_time.hour * 60 + self.close_time.minute
        return closing - minute

    def _bounds(self, timestamps: pd.Series) -> pd.DataFrame:
        values: list[tuple[pd.Timestamp | None, pd.Timestamp | None]] = []
        for value in pd.to_datetime(timestamps, utc=True):
            session = XNYS_CALENDAR.session_bounds(value.to_pydatetime())
            values.append((pd.Timestamp(session[0]), pd.Timestamp(session[1])) if session is not None else (None, None))
        return pd.DataFrame(values, columns=["open", "close"], index=timestamps.index)


__all__ = ["SessionCalendar"]
