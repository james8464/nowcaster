from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.deep_research.statistics import (
    bootstrap_positive_edge_probability,
    deflated_sharpe_probability,
    parameter_stability,
    probability_of_backtest_overfitting,
)


def test_seeded_block_bootstrap_distinguishes_edge_from_noise() -> None:
    edge = bootstrap_positive_edge_probability(
        [0.01] * 45 + [-0.002] * 5,
        block_size=5,
        samples=1_000,
        seed=17,
    )
    noise = bootstrap_positive_edge_probability(
        [0.01, -0.01] * 25,
        block_size=5,
        samples=1_000,
        seed=17,
    )

    assert edge >= 0.99
    assert 0.35 <= noise <= 0.65


def test_dsr_uses_the_complete_observed_trial_ledger() -> None:
    few = deflated_sharpe_probability(
        1.2,
        observations=400,
        trial_sharpes=[0.1, 0.2, 0.3],
        skew=0.0,
        kurtosis=3.0,
    )
    many = deflated_sharpe_probability(
        1.2,
        observations=400,
        trial_sharpes=np.linspace(-0.5, 1.1, 200).tolist(),
        skew=0.0,
        kurtosis=3.0,
    )

    assert few > many


def test_pbo_is_zero_when_one_candidate_dominates_every_chronological_block() -> None:
    performance = pd.DataFrame(
        {
            "stable": [0.02] * 20,
            "noise": [0.01, -0.01] * 10,
            "loss": [-0.02] * 20,
        }
    )
    assert probability_of_backtest_overfitting(performance, segments=4) == 0.0


def test_parameter_stability_requires_positive_nearby_performance_and_retained_score() -> None:
    assert parameter_stability([0.85, 0.9, 0.95], best_score=1.0) == pytest.approx(0.9)
    # Half the neighbors are positive and their median retains 40% of the best score.
    assert parameter_stability([-0.1, 0.9], best_score=1.0) == pytest.approx(0.2)


@pytest.mark.parametrize(
    "operation",
    [
        lambda: bootstrap_positive_edge_probability([0.1, float("nan")]),
        lambda: parameter_stability([0.5, float("inf")], best_score=1.0),
        lambda: probability_of_backtest_overfitting(pd.DataFrame({"only": [0.1, 0.2]})),
    ],
)
def test_statistics_reject_undefined_evidence(operation) -> None:
    with pytest.raises(ValueError):
        operation()
