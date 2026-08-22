from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


class EventWindowError(ValueError):
    pass


@dataclass(frozen=True)
class EventReturn:
    event_date: date
    window_start: int
    window_end: int
    start_date: date
    end_date: date
    raw_return: float
    benchmark_return: float | None = None
    abnormal_return: float | None = None


def _return_between(frame: pd.DataFrame, start_date: date, end_date: date) -> float:
    indexed = frame.set_index("trading_date")["adjusted_close"]
    if start_date not in indexed.index or end_date not in indexed.index:
        raise EventWindowError("Benchmark price history does not cover the identical event dates")
    return float(indexed.loc[end_date] / indexed.loc[start_date] - 1)


def calculate_event_return(
    prices: pd.DataFrame,
    event_date: date,
    window: tuple[int, int],
    benchmark: pd.DataFrame | None = None,
) -> EventReturn:
    if window[0] > window[1]:
        raise EventWindowError("Event window start must not exceed end")
    ordered = prices.sort_values("trading_date").drop_duplicates("trading_date").reset_index(drop=True)
    eligible = ordered.index[ordered["trading_date"] >= event_date]
    if len(eligible) == 0:
        raise EventWindowError("Price history ends before the event")
    anchor = int(eligible[0])
    start_index, end_index = anchor + window[0], anchor + window[1]
    if start_index < 0 or end_index >= len(ordered):
        raise EventWindowError("Insufficient price history for requested event window")
    start_row, end_row = ordered.iloc[start_index], ordered.iloc[end_index]
    raw_return = float(end_row.adjusted_close / start_row.adjusted_close - 1)
    benchmark_return = None
    abnormal_return = None
    if benchmark is not None:
        benchmark_return = _return_between(benchmark, start_row.trading_date, end_row.trading_date)
        abnormal_return = raw_return - benchmark_return
    return EventReturn(
        event_date=event_date,
        window_start=window[0],
        window_end=window[1],
        start_date=start_row.trading_date,
        end_date=end_row.trading_date,
        raw_return=raw_return,
        benchmark_return=benchmark_return,
        abnormal_return=abnormal_return,
    )


def gs_quant_return_crosscheck(prices: pd.Series) -> float | None:
    """Cross-check a period return when the optional local GS Quant package is importable."""
    try:
        from gs_quant.timeseries.econometrics import returns
    except ImportError:
        return None
    values = returns(prices, obs=len(prices) - 1)
    return float(values.iloc[-1])
