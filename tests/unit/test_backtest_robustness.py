from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest.robustness import (
    benjamini_hochberg,
    deflated_sharpe_probability,
    effective_sample_size,
    leave_one_group_out,
    lower_mean_confidence_bound,
    run_block_bootstrap,
    subperiod_analysis,
    volatility_regime_analysis,
)


def test_false_discovery_adjustment_is_monotonic() -> None:
    adjusted = benjamini_hochberg([0.01, 0.04, 0.2])
    assert adjusted == sorted(adjusted)
    assert all(0 <= value <= 1 for value in adjusted)


def test_block_bootstrap_is_deterministic_and_reports_loss_probability() -> None:
    returns = np.tile([0.01, -0.004, 0.006, -0.002], 40)
    first = run_block_bootstrap(returns, block_size=8, samples=500, seed=42)
    second = run_block_bootstrap(returns, block_size=8, samples=500, seed=42)
    assert first == second
    assert 0 <= first.probability_positive <= 1
    assert first.ci_low < first.ci_high


def test_deflated_sharpe_penalizes_many_trials() -> None:
    few_trials = np.asarray([-1.0, 1.0])
    many_trials = np.arange(100, dtype=float)
    many_trials = (many_trials - many_trials.mean()) / many_trials.std(ddof=1)
    few = deflated_sharpe_probability(1.2, observations=250, trial_sharpes=few_trials, skew=0, kurtosis=3)
    many = deflated_sharpe_probability(1.2, observations=250, trial_sharpes=many_trials, skew=0, kurtosis=3)
    assert 0 <= many <= few <= 1


def test_stability_analyses_cover_groups_periods_and_regimes() -> None:
    dates = pd.date_range("2022-01-01", periods=730, freq="D")
    frame = pd.DataFrame(
        {
            "date": dates,
            "symbol": np.where(np.arange(len(dates)) % 2, "BTC-USD", "ETH-USD"),
            "net_return": np.sin(np.arange(len(dates)) / 20) / 100,
            "forecast_volatility": 0.01 + np.arange(len(dates)) / 100_000,
        }
    )
    assert len(leave_one_group_out(frame, group_column="symbol")) == 2
    assert len(subperiod_analysis(frame)) == 2
    assert set(volatility_regime_analysis(frame)["regime"].astype(str)) == {"low", "medium", "high"}


def test_effective_sample_penalizes_serially_correlated_returns() -> None:
    independent = np.tile([0.01, -0.01], 50)
    autocorrelated = np.repeat(np.tile([0.01, -0.01], 5), 10)

    assert effective_sample_size(independent) == pytest.approx(100)
    assert 1 < effective_sample_size(autocorrelated) < 100


def test_lower_mean_bound_is_literal_and_rejects_malformed_evidence() -> None:
    assert lower_mean_confidence_bound(np.full(100, 0.01)) == pytest.approx(0.01)
    assert lower_mean_confidence_bound(np.tile([0.01, -0.004], 50)) > 0
    with pytest.raises(ValueError, match="finite"):
        lower_mean_confidence_bound([0.01, float("nan")])
