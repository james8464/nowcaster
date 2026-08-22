from __future__ import annotations

import numpy as np
import pandas as pd

CRYPTO_FEATURE_COLUMNS = (
    "feature_return_1d",
    "feature_return_5d",
    "feature_momentum_20d",
    "feature_trend_20_100",
    "feature_volatility_20d",
    "feature_volatility_60d",
    "feature_volume_zscore_20d",
    "feature_rsi_14d",
    "feature_weekday_sin",
    "feature_weekday_cos",
)


def _rsi(closes: pd.Series, window: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(window, min_periods=window).mean()
    loss = -delta.clip(upper=0).rolling(window, min_periods=window).mean()
    ratio = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + ratio)).fillna(50) / 100


def build_crypto_features(prices: pd.DataFrame, *, horizon_days: int = 5) -> pd.DataFrame:
    """Build daily features available strictly before the decision timestamp.

    Decisions are formed for day ``t`` from data through ``t-1``. The simulated
    entry is the next daily close and the label ends ``horizon_days`` bars later.
    """
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    required = {"symbol", "trading_date", "adjusted_close"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Missing crypto price columns: {sorted(missing)}")

    frames: list[pd.DataFrame] = []
    for symbol, raw in prices.groupby("symbol", sort=True):
        frame = raw.copy().sort_values("trading_date").reset_index(drop=True)
        frame["trading_date"] = pd.to_datetime(frame["trading_date"])
        if frame["trading_date"].duplicated().any():
            raise ValueError(f"Duplicate trading date for {symbol}")
        close = pd.to_numeric(frame["adjusted_close"], errors="coerce")
        if close.isna().any() or (close <= 0).any():
            raise ValueError(f"Crypto closes must be positive for {symbol}")
        volume = pd.to_numeric(frame.get("volume", pd.Series(np.nan, index=frame.index)), errors="coerce")

        known_close = close.shift(1)
        log_known = np.log(known_close)
        daily_returns = log_known.diff()
        known_volume = np.log1p(volume.shift(1).clip(lower=0))
        volume_mean = known_volume.rolling(20, min_periods=20).mean()
        volume_std = known_volume.rolling(20, min_periods=20).std(ddof=0).replace(0, np.nan)
        decision = frame["trading_date"]
        result = pd.DataFrame(
            {
                "symbol": str(symbol).upper(),
                "decision_date": decision.dt.date,
                "data_through_date": frame["trading_date"].shift(1).dt.date,
                "execution_date": frame["trading_date"].shift(-1).dt.date,
                "label_end": frame["trading_date"].shift(-(horizon_days + 1)).dt.date,
                "horizon_days": horizon_days,
                "feature_return_1d": daily_returns,
                "feature_return_5d": log_known.diff(5),
                "feature_momentum_20d": log_known.diff(20),
                "feature_trend_20_100": (
                    known_close.rolling(20, min_periods=20).mean() / known_close.rolling(100, min_periods=100).mean()
                    - 1
                ),
                "feature_volatility_20d": daily_returns.rolling(20, min_periods=20).std(ddof=1),
                "feature_volatility_60d": daily_returns.rolling(60, min_periods=60).std(ddof=1),
                "feature_volume_zscore_20d": (known_volume - volume_mean) / volume_std,
                "feature_rsi_14d": _rsi(known_close),
                "feature_weekday_sin": np.sin(2 * np.pi * decision.dt.dayofweek / 7),
                "feature_weekday_cos": np.cos(2 * np.pi * decision.dt.dayofweek / 7),
                "entry_close": close.shift(-1),
                "exit_close": close.shift(-(horizon_days + 1)),
            }
        )
        result["target_forward_return"] = np.log(result["exit_close"] / result["entry_close"])
        result["target_direction"] = (result["target_forward_return"] > 0).astype(int)
        result = result.dropna(
            subset=[*CRYPTO_FEATURE_COLUMNS, "execution_date", "label_end", "target_forward_return"]
        ).reset_index(drop=True)
        frames.append(result)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["decision_date", "symbol"]).reset_index(drop=True)
