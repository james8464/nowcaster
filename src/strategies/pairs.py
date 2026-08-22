from __future__ import annotations

import numpy as np
import pandas as pd


def _timestamp_column(frame: pd.DataFrame) -> str:
    for name in ("close_timestamp", "open_timestamp", "timestamp"):
        if name in frame:
            return name
    raise ValueError("pair bars require a timestamp column")


def aligned_peer_close(primary: pd.DataFrame, peer: pd.DataFrame) -> pd.Series:
    primary_time = pd.to_datetime(primary[_timestamp_column(primary)], utc=True)
    peer_time = pd.to_datetime(peer[_timestamp_column(peer)], utc=True)
    if not peer_time.is_monotonic_increasing:
        raise ValueError("paired bars must be ordered by timestamp")
    peer_values = pd.Series(peer["close"].astype(float).to_numpy(), index=peer_time)
    if peer_values.index.has_duplicates:
        raise ValueError("paired bars must contain one row per timestamp")
    return pd.Series(peer_values.reindex(primary_time).to_numpy(), index=primary.index, dtype=float)


def rolling_cointegration_zscore(primary_close: pd.Series, peer_close: pd.Series, lookback: int) -> pd.Series:
    """Return the current residual z-score from trailing log-price OLS windows."""

    if lookback <= 1:
        raise ValueError("cointegration lookback must exceed one bar")
    primary_log = np.log(primary_close.astype(float).where(primary_close > 0))
    peer_log = np.log(peer_close.astype(float).where(peer_close > 0))
    result = pd.Series(np.nan, index=primary_close.index, dtype=float)
    for end in range(lookback - 1, len(primary_close)):
        start = end - lookback + 1
        y = primary_log.iloc[start : end + 1].to_numpy(dtype=float)
        x = peer_log.iloc[start : end + 1].to_numpy(dtype=float)
        if np.isnan(x).any() or np.isnan(y).any():
            continue
        variance = float(np.var(x))
        beta = float(np.cov(x, y, ddof=0)[0, 1] / variance) if variance > 1e-15 else 0.0
        intercept = float(np.mean(y - beta * x))
        residuals = y - (intercept + beta * x)
        deviation = float(np.std(residuals, ddof=0))
        if deviation > 0:
            result.iloc[end] = float(residuals[-1] / deviation)
    return result


__all__ = ["aligned_peer_close", "rolling_cointegration_zscore"]
