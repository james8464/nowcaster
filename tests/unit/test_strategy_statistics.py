from __future__ import annotations

import math

import pandas as pd
import pytest

from src.backtest.robustness import (
    cscv_probability_of_backtest_overfitting,
    deflated_sharpe_probability,
    doubled_cost_survival,
    parameter_stability,
    performance_attribution,
    run_block_bootstrap,
)


def test_seeded_circular_block_bootstrap_has_literal_interval_and_probability() -> None:
    result = run_block_bootstrap([0.01, -0.02, 0.03, 0.0], block_size=2, samples=8, seed=7)

    assert result.mean_return == pytest.approx(0.005)
    assert result.ci_low == 0
    assert result.ci_high == 0.01
    assert result.probability_positive == 0.625
    assert result.samples == 8
    assert result.block_size == 2


def test_deflated_sharpe_uses_observations_moments_and_actual_trial_count() -> None:
    result = deflated_sharpe_probability(0.8, observations=25, trials=4, skew=0, kurtosis=3)

    assert result == pytest.approx(0.9963042863677345)
    assert deflated_sharpe_probability(0.8, observations=25, trials=40, skew=0, kurtosis=3) < result


def test_cscv_pbo_selects_in_sample_winner_then_ranks_it_out_of_sample() -> None:
    performance = pd.DataFrame(
        {
            "fragile": [0.04, 0.04, -0.04, -0.04],
            "stable": [0.01, 0.01, 0.01, 0.01],
        }
    )

    result = cscv_probability_of_backtest_overfitting(performance, segments=4)

    assert result.combinations == 6
    assert result.overfit_combinations == 2
    assert result.probability == pytest.approx(1 / 3)
    assert result.logits == pytest.approx(
        (-math.log(3), math.log(3), math.log(3), math.log(3), math.log(3), -math.log(3))
    )


def test_parameter_stability_reports_literal_neighbor_scores_around_the_winner() -> None:
    trials = pd.DataFrame(
        {
            "lookback": [5, 10, 15],
            "net_sharpe": [0.01, 0.03, 0.02],
        }
    )

    result = parameter_stability(trials, parameter_columns=["lookback"], score_column="net_sharpe")

    assert result.best_parameters == (("lookback", 10),)
    assert result.best_score == 0.03
    assert result.neighboring_scores == (0.01, 0.02)
    assert result.positive_neighbor_fraction == 1
    assert result.neighbor_median_ratio == pytest.approx(0.5)
    assert result.stable is True


def test_fold_year_and_side_attribution_compounds_each_literal_group() -> None:
    trades = pd.DataFrame(
        {
            "execution_timestamp": pd.to_datetime(["2024-01-02", "2024-02-02", "2025-01-02", "2025-02-02"], utc=True),
            "fold": [1, 1, 2, 2],
            "side": ["long", "short", "long", "short"],
            "net_return": [0.1, -0.1, 0.2, 0.0],
        }
    )

    result = performance_attribution(trades)

    assert [record.group for record in result.by_fold] == ["1", "2"]
    assert [record.cumulative_return for record in result.by_fold] == pytest.approx([-0.01, 0.2])
    assert [record.group for record in result.by_year] == ["2024", "2025"]
    assert [record.cumulative_return for record in result.by_year] == pytest.approx([-0.01, 0.2])
    assert [record.group for record in result.by_side] == ["long", "short"]
    assert [record.cumulative_return for record in result.by_side] == pytest.approx([0.32, -0.1])


def test_doubled_cost_survival_reprices_each_return_before_compounding() -> None:
    result = doubled_cost_survival([0.1, -0.05], [0.01, 0.01])

    assert result.base_cumulative_return == pytest.approx(0.0246)
    assert result.doubled_cost_cumulative_return == pytest.approx(0.0044)
    assert result.survives is True
