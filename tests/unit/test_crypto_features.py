from __future__ import annotations

import numpy as np
import pandas as pd

from src.crypto.features import build_crypto_features


def _prices(periods: int = 240) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=periods, freq="D")
    close = 30_000 * np.exp(np.linspace(0, 0.3, periods) + 0.02 * np.sin(np.arange(periods) / 7))
    return pd.DataFrame(
        {
            "symbol": "BTC-USD",
            "trading_date": dates.date,
            "adjusted_close": close,
            "volume": 1_000_000 + np.arange(periods) * 1_000,
        }
    )


def test_crypto_features_use_only_prior_closes() -> None:
    prices = _prices()
    features = build_crypto_features(prices, horizon_days=5)
    target = features.iloc[20]
    changed = prices.copy()
    future = pd.to_datetime(changed["trading_date"]) >= pd.Timestamp(target["decision_date"])
    changed.loc[future, "adjusted_close"] *= 10
    rebuilt = build_crypto_features(changed, horizon_days=5)
    rebuilt_target = rebuilt.loc[rebuilt["decision_date"] == target["decision_date"]].iloc[0]
    feature_columns = [column for column in features if column.startswith("feature_")]
    pd.testing.assert_series_equal(target[feature_columns], rebuilt_target[feature_columns])


def test_crypto_target_starts_after_decision_and_ends_at_label_date() -> None:
    prices = _prices()
    features = build_crypto_features(prices, horizon_days=5)
    row = features.iloc[0]
    assert row["execution_date"] > row["decision_date"]
    assert row["label_end"] > row["execution_date"]
    expected = np.log(row["exit_close"] / row["entry_close"])
    assert row["target_forward_return"] == expected
