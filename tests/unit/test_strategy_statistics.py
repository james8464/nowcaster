from __future__ import annotations

import math

import pandas as pd
import pytest

from src.backtest.costs import CostAssumptions
from src.backtest.execution import ExecutionAssumptions
from src.backtest.intraday import RiskLimits, run_intraday_backtest
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
    result = deflated_sharpe_probability(
        0.8,
        observations=25,
        trial_sharpes=[0.1, 0.2, 0.3, 0.4],
        skew=0,
        kurtosis=3,
    )

    # Independent literal: sample variance=1/60, expected maximum=0.13582847254166247,
    # PSR z-score=2.8320369087423507 under the Bailey-Lopez de Prado adjustment.
    assert result == pytest.approx(0.9976873745271267)
    assert (
        deflated_sharpe_probability(
            0.8,
            observations=25,
            trial_sharpes=[-0.4, 0.0, 0.4, 0.8],
            skew=0,
            kurtosis=3,
        )
        < result
    )


def test_deflated_sharpe_rejects_a_trial_count_that_disagrees_with_observed_trials() -> None:
    with pytest.raises(ValueError, match="trial count"):
        deflated_sharpe_probability(
            0.8,
            observations=25,
            trial_sharpes=[0.1, 0.2, 0.3, 0.4],
            trials=40,
            skew=0,
            kurtosis=3,
        )


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


def test_doubled_cost_survival_accepts_normalized_cost_returns_from_intraday_curve() -> None:
    opens = pd.date_range("2026-08-21 10:00", periods=2, freq="min", tz="UTC")
    bars = pd.DataFrame(
        {
            "symbol": "AAA",
            "open_timestamp": opens,
            "close_timestamp": opens + pd.Timedelta(minutes=1),
            "available_at": opens + pd.Timedelta(minutes=1),
            "finalized": True,
            "open": [100.0, 100.0],
            "high": [100.0, 110.0],
            "low": [100.0, 100.0],
            "close": [100.0, 110.0],
            "volume": 10_000.0,
            "halted": False,
        }
    )
    signals = pd.DataFrame(
        {
            "strategy_id": ["trend"],
            "symbol": ["AAA"],
            "decision_timestamp": [pd.Timestamp("2026-08-21 10:01", tz="UTC")],
            "data_through": [pd.Timestamp("2026-08-21 10:01", tz="UTC")],
            "signal": [1],
            "strength": [0.5],
        }
    )
    backtest = run_intraday_backtest(
        bars,
        signals,
        ExecutionAssumptions(costs=CostAssumptions(taker_fee_bps=100)),
        RiskLimits(initial_cash=1_000),
    )

    assert backtest.equity_curve.iloc[-1]["gross_return"] == pytest.approx(0.05)
    assert backtest.equity_curve.iloc[-1]["cost_return"] == pytest.approx(0.005)
    result = doubled_cost_survival(backtest.equity_curve)
    assert result.base_cumulative_return == pytest.approx(0.045)
    assert result.doubled_cost_cumulative_return == pytest.approx(0.04)
    assert result.survives is True
