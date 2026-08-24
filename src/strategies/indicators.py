from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from src.strategies.session import SessionCalendar

IndicatorFrame = pd.DataFrame


def _period(value: int) -> int:
    if value <= 0:
        raise ValueError("indicator periods must be positive")
    return value


def _float_series(values: pd.Series | Sequence[float]) -> pd.Series:
    if isinstance(values, pd.Series):
        return values.astype(float)
    return pd.Series(values, dtype=float)


def _wilder_average(values: pd.Series, period: int) -> pd.Series:
    period = _period(period)
    source = _float_series(values)
    result = pd.Series(np.nan, index=source.index, dtype=float)
    valid_run: list[float] = []
    previous: float | None = None
    for position, value in enumerate(source.to_numpy(dtype=float)):
        if np.isnan(value):
            valid_run.clear()
            previous = None
            continue
        if previous is None:
            valid_run.append(float(value))
            if len(valid_run) < period:
                continue
            previous = float(np.mean(valid_run[-period:]))
        else:
            previous = ((period - 1) * previous + float(value)) / period
        result.iloc[position] = previous
    return result


def ema(values: pd.Series | Sequence[float], period: int) -> pd.Series:
    period = _period(period)
    return _float_series(values).ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series | Sequence[float], period: int = 14) -> pd.Series:
    delta = _float_series(close).diff()
    average_gain = _wilder_average(delta.clip(lower=0), period)
    average_loss = _wilder_average(-delta.clip(upper=0), period)
    relative_strength = average_gain / average_loss
    result = 100 - 100 / (1 + relative_strength)
    result = result.mask((average_loss == 0) & (average_gain > 0), 100.0)
    return result.mask((average_loss == 0) & (average_gain == 0), 50.0)


def true_range(
    high: pd.Series | Sequence[float],
    low: pd.Series | Sequence[float],
    close: pd.Series | Sequence[float],
) -> pd.Series:
    high_series = _float_series(high)
    low_series = _float_series(low)
    previous_close = _float_series(close).shift(1)
    return pd.concat(
        [
            high_series - low_series,
            (high_series - previous_close).abs(),
            (low_series - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(
    high: pd.Series | Sequence[float],
    low: pd.Series | Sequence[float],
    close: pd.Series | Sequence[float],
    period: int = 14,
) -> pd.Series:
    return _wilder_average(true_range(high, low, close), period)


def adx(
    high: pd.Series | Sequence[float],
    low: pd.Series | Sequence[float],
    close: pd.Series | Sequence[float],
    period: int = 14,
) -> pd.Series:
    high_series = _float_series(high)
    low_series = _float_series(low)
    upward = high_series.diff()
    downward = -low_series.diff()
    plus_dm = upward.where((upward > downward) & (upward > 0), 0.0)
    minus_dm = downward.where((downward > upward) & (downward > 0), 0.0)
    average_range = atr(high_series, low_series, close, period)
    plus_di = 100 * _wilder_average(plus_dm, period) / average_range
    minus_di = 100 * _wilder_average(minus_dm, period) / average_range
    denominator = plus_di + minus_di
    directional_index = 100 * (plus_di - minus_di).abs() / denominator
    directional_index = directional_index.mask(denominator == 0, 0.0)
    return _wilder_average(directional_index, period)


def macd(
    close: pd.Series | Sequence[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    close_series = _float_series(close)
    line = ema(close_series, fast_period) - ema(close_series, slow_period)
    signal = ema(line, signal_period)
    return line, signal, line - signal


def stochastic(
    high: pd.Series | Sequence[float],
    low: pd.Series | Sequence[float],
    close: pd.Series | Sequence[float],
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    k_period = _period(k_period)
    d_period = _period(d_period)
    highest = _float_series(high).rolling(k_period, min_periods=k_period).max()
    lowest = _float_series(low).rolling(k_period, min_periods=k_period).min()
    spread = highest - lowest
    percent_k = 100 * (_float_series(close) - lowest) / spread
    percent_k = percent_k.mask(spread == 0, 50.0)
    return percent_k, percent_k.rolling(d_period, min_periods=d_period).mean()


def bollinger_bands(
    values: pd.Series | Sequence[float], period: int = 20, deviations: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    period = _period(period)
    if deviations <= 0:
        raise ValueError("band deviations must be positive")
    source = _float_series(values)
    middle = source.rolling(period, min_periods=period).mean()
    deviation = source.rolling(period, min_periods=period).std(ddof=0)
    return middle, middle + deviations * deviation, middle - deviations * deviation


def keltner_channels(
    high: pd.Series | Sequence[float],
    low: pd.Series | Sequence[float],
    close: pd.Series | Sequence[float],
    period: int = 20,
    atr_period: int = 20,
    multiplier: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    if multiplier <= 0:
        raise ValueError("Keltner multiplier must be positive")
    middle = ema(close, period)
    average_range = atr(high, low, close, atr_period)
    return middle, middle + multiplier * average_range, middle - multiplier * average_range


def donchian_channels(
    high: pd.Series | Sequence[float], low: pd.Series | Sequence[float], period: int = 20
) -> tuple[pd.Series, pd.Series]:
    period = _period(period)
    upper = _float_series(high).shift(1).rolling(period, min_periods=period).max()
    lower = _float_series(low).shift(1).rolling(period, min_periods=period).min()
    return upper, lower


def session_vwap(
    high: pd.Series | Sequence[float],
    low: pd.Series | Sequence[float],
    close: pd.Series | Sequence[float],
    volume: pd.Series | Sequence[float],
    timestamps: pd.Series,
    session: SessionCalendar,
) -> pd.Series:
    typical = (_float_series(high) + _float_series(low) + _float_series(close)) / 3
    volume_series = _float_series(volume)
    labels = session.session_labels(timestamps)
    cumulative_notional = (typical * volume_series).groupby(labels, sort=False).cumsum()
    cumulative_volume = volume_series.groupby(labels, sort=False).cumsum()
    return cumulative_notional / cumulative_volume.replace(0, np.nan)


def relative_volume(volume: pd.Series | Sequence[float], lookback: int = 20) -> pd.Series:
    lookback = _period(lookback)
    source = _float_series(volume)
    baseline = source.shift(1).rolling(lookback, min_periods=lookback).mean()
    return source / baseline.replace(0, np.nan)


def rolling_zscore(values: pd.Series | Sequence[float], lookback: int = 20) -> pd.Series:
    lookback = _period(lookback)
    source = _float_series(values)
    average = source.rolling(lookback, min_periods=lookback).mean()
    deviation = source.rolling(lookback, min_periods=lookback).std(ddof=0)
    return (source - average) / deviation.replace(0, np.nan)


def build_indicators(bars: pd.DataFrame, session: SessionCalendar) -> IndicatorFrame:
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"bars are missing required columns: {', '.join(sorted(missing))}")
    if "finalized" in bars and not bars["finalized"].astype(bool).all():
        raise ValueError("indicators require finalized bars")
    timestamp_name = "open_timestamp" if "open_timestamp" in bars else "timestamp"
    if timestamp_name not in bars:
        raise ValueError("bars require an open_timestamp or timestamp column")
    timestamps = pd.to_datetime(bars[timestamp_name], utc=True)
    if not timestamps.is_monotonic_increasing:
        raise ValueError("bars must be ordered by timestamp")

    result = bars.copy()
    result["ema_12"] = ema(result["close"], 12)
    result["ema_26"] = ema(result["close"], 26)
    result["rsi_14"] = rsi(result["close"], 14)
    result["atr_14"] = atr(result["high"], result["low"], result["close"], 14)
    result["adx_14"] = adx(result["high"], result["low"], result["close"], 14)
    result["macd"], result["macd_signal"], result["macd_histogram"] = macd(result["close"])
    result["stochastic_k"], result["stochastic_d"] = stochastic(result["high"], result["low"], result["close"])
    result["bollinger_middle"], result["bollinger_upper"], result["bollinger_lower"] = bollinger_bands(result["close"])
    result["keltner_middle"], result["keltner_upper"], result["keltner_lower"] = keltner_channels(
        result["high"], result["low"], result["close"]
    )
    result["donchian_upper"], result["donchian_lower"] = donchian_channels(result["high"], result["low"])
    result["session_vwap"] = session_vwap(
        result["high"], result["low"], result["close"], result["volume"], timestamps, session
    )
    result["relative_volume_20"] = relative_volume(result["volume"])
    result["return_zscore_20"] = rolling_zscore(result["close"].pct_change(), 20)
    result["session"] = session.session_labels(timestamps)
    result["opening_range_30"] = session.opening_range(timestamps, 30)
    result["last_half_hour"] = session.last_window(timestamps, 30)
    return result


__all__ = [
    "IndicatorFrame",
    "adx",
    "atr",
    "bollinger_bands",
    "build_indicators",
    "donchian_channels",
    "ema",
    "keltner_channels",
    "macd",
    "relative_volume",
    "rolling_zscore",
    "rsi",
    "session_vwap",
    "stochastic",
    "true_range",
]
