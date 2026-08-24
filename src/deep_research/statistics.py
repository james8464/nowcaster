from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd

from src.backtest.robustness import (
    cscv_probability_of_backtest_overfitting,
    run_block_bootstrap,
)
from src.backtest.robustness import (
    deflated_sharpe_probability as _deflated_sharpe_probability,
)


def bootstrap_positive_edge_probability(
    returns: Sequence[float],
    *,
    block_size: int = 10,
    samples: int = 2_000,
    seed: int = 42,
) -> float:
    values = np.asarray(returns, dtype=float)
    if values.ndim != 1 or not len(values) or np.any(~np.isfinite(values)):
        raise ValueError("returns must be a non-empty finite one-dimensional sequence")
    return run_block_bootstrap(
        values,
        block_size=block_size,
        samples=samples,
        seed=seed,
    ).probability_positive


def deflated_sharpe_probability(
    sharpe: float,
    *,
    observations: int,
    trial_sharpes: Sequence[float],
    skew: float,
    kurtosis: float,
) -> float:
    """Use the full committed trial ledger to correct a selected Sharpe."""

    return _deflated_sharpe_probability(
        sharpe,
        observations=observations,
        trial_sharpes=trial_sharpes,
        trials=len(trial_sharpes),
        skew=skew,
        kurtosis=kurtosis,
    )


def probability_of_backtest_overfitting(performance: pd.DataFrame, *, segments: int = 10) -> float:
    return cscv_probability_of_backtest_overfitting(performance, segments=segments).probability


def parameter_stability(neighbor_scores: Sequence[float], *, best_score: float) -> float:
    values = np.asarray(neighbor_scores, dtype=float)
    if (
        values.ndim != 1
        or not len(values)
        or np.any(~np.isfinite(values))
        or not math.isfinite(best_score)
        or best_score <= 0
    ):
        raise ValueError("parameter stability requires finite neighbors and a positive finite best score")
    positive_fraction = float(np.mean(values > 0))
    retained_score = float(np.clip(np.median(values) / best_score, 0.0, 1.0))
    return float(np.clip(positive_fraction * retained_score, 0.0, 1.0))


__all__ = [
    "bootstrap_positive_edge_probability",
    "deflated_sharpe_probability",
    "parameter_stability",
    "probability_of_backtest_overfitting",
]
