from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from src.contextual.regimes import (
    REGIME_FEATURE_COLUMNS,
    causal_regime_features,
    fit_regime_model,
    predict_regime_posteriors,
)
from src.contextual.types import MarketRegime


def _bars(rows: int = 360) -> pd.DataFrame:
    random = np.random.default_rng(20260830)
    opened = pd.date_range(datetime(2026, 1, 1, tzinfo=UTC), periods=rows, freq="5min")
    volatility = np.where(np.arange(rows) % 90 < 20, 0.012, 0.002)
    drift = np.where((np.arange(rows) // 60) % 2 == 0, 0.001, -0.0004)
    returns = drift + random.normal(0, volatility)
    close = 100 * np.exp(np.cumsum(returns))
    volume = 2_000 * np.exp(random.normal(0, 0.4, rows))
    return pd.DataFrame(
        {
            "open_timestamp": opened,
            "close_timestamp": opened + pd.Timedelta(minutes=5),
            "available_at": opened + pd.Timedelta(minutes=5, seconds=1),
            "finalized": True,
            "open": close / np.exp(returns),
            "high": close * (1 + np.abs(returns) / 2),
            "low": close * (1 - np.abs(returns) / 2),
            "close": close,
            "volume": volume,
            "spread_bps": np.clip(2 + 300 * volatility + random.normal(0, 0.2, rows), 0.1, None),
            "bid_depth_notional": volume * 40,
            "ask_depth_notional": volume * 35,
        }
    )


def test_regime_posteriors_are_normalized_and_future_invariant() -> None:
    bars = _bars()
    features = causal_regime_features(bars.iloc[:320])
    fit = fit_regime_model(features.iloc[:260], minimum_train=80)
    before = predict_regime_posteriors(fit, features.iloc[260:300])
    mutated = bars.copy()
    mutated.loc[300:, ["close", "volume", "spread_bps"]] *= 100
    after = predict_regime_posteriors(
        fit,
        causal_regime_features(mutated).iloc[260:300],
    )

    np.testing.assert_allclose(before.probabilities, after.probabilities)
    np.testing.assert_allclose(before.probabilities.sum(axis=1), 1.0)
    assert before.regimes == tuple(MarketRegime)
    assert fit.model_hash


def test_current_bar_is_excluded_from_its_regime_features() -> None:
    bars = _bars(180)
    before = causal_regime_features(bars)
    changed = bars.copy()
    changed.loc[100, ["open", "high", "low", "close", "volume"]] *= 20
    after = causal_regime_features(changed)

    np.testing.assert_allclose(
        before.loc[100, REGIME_FEATURE_COLUMNS],
        after.loc[100, REGIME_FEATURE_COLUMNS],
        equal_nan=True,
    )
    assert not np.allclose(
        before.loc[101, REGIME_FEATURE_COLUMNS],
        after.loc[101, REGIME_FEATURE_COLUMNS],
        equal_nan=True,
    )


def test_sparse_or_single_class_fit_uses_conservative_parent_fallback() -> None:
    bars = _bars(50)
    bars[["open", "high", "low", "close"]] = 100.0
    bars["volume"] = 1_000.0
    features = causal_regime_features(bars)

    fit = fit_regime_model(features, minimum_train=80)
    posterior = predict_regime_posteriors(fit, features.iloc[-5:])

    assert fit.status == "parent_fallback"
    stressed_index = posterior.regimes.index(MarketRegime.STRESSED_OR_ILLIQUID)
    assert (posterior.probabilities[:, stressed_index] >= posterior.probabilities.max(axis=1)).all()
