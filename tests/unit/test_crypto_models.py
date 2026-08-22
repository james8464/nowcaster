from __future__ import annotations

import numpy as np
import pandas as pd

from src.crypto.features import build_crypto_features
from src.crypto.models import make_crypto_walk_forward_folds, run_crypto_models


def _matrix(periods: int = 720) -> pd.DataFrame:
    dates = pd.date_range("2022-01-01", periods=periods, freq="D")
    noise = np.random.default_rng(42).normal(0, 0.012, periods)
    close = 20_000 * np.exp(np.cumsum(0.0003 + noise))
    prices = pd.DataFrame(
        {
            "symbol": "BTC-USD",
            "trading_date": dates.date,
            "adjusted_close": close,
            "volume": np.exp(20 + np.random.default_rng(7).normal(0, 0.2, periods)),
        }
    )
    return build_crypto_features(prices, horizon_days=5)


def test_crypto_folds_purge_overlapping_labels() -> None:
    folds = make_crypto_walk_forward_folds(_matrix(), minimum_train=180, test_size=45, embargo_days=5)
    assert folds
    assert all(fold.training_label_end < fold.test_decision_start for fold in folds)


def test_crypto_models_are_deterministic_and_probabilities_are_bounded() -> None:
    matrix = _matrix()
    first = run_crypto_models(matrix, minimum_train=180, test_size=45, seed=42)
    second = run_crypto_models(matrix, minimum_train=180, test_size=45, seed=42)
    pd.testing.assert_frame_equal(first.predictions, second.predictions)
    assert len(first.predictions) > 100
    assert first.predictions["direction_probability"].between(0, 1).all()
    assert first.predictions["training_samples"].min() >= 180

