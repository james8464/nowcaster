"""Approximate dependence-aware screening, never a calibrated profit probability."""

import math

import numpy as np


def bootstrap_screen(daily_returns: list[float], *, study_number: int, candidate_count: int) -> dict:
    """Non-circular seven-day moving blocks; deterministic 10,000 resamples.

    Inputs must include *all* complete consecutive UTC days, including zeros.
    The ledger refuses assessment if any internal day is incomplete. A mean
    daily-return percentile is a screening statistic, not an investment promise.
    """
    if study_number < 1 or candidate_count < 1 or not all(math.isfinite(x) for x in daily_returns):
        raise ValueError("invalid bootstrap inputs")
    alpha = 0.05 / (study_number * (study_number + 1) * candidate_count)
    result = dict(
        method="approximate_7_day_moving_block_bootstrap_mean_daily_return",
        observations=len(daily_returns),
        resamples=10000,
        alpha=alpha,
        tail_samples=10000 * alpha,
        lower_bound=None,
    )
    if len(daily_returns) < 28:
        return result | {"reason": "at_least_four_7_day_blocks_required"}
    if alpha * 10000 < 20:
        return result | {"reason": "insufficient_adjusted_tail_resolution"}
    values = np.asarray(daily_returns, dtype=float)
    rng = np.random.default_rng(20260908)
    starts = rng.integers(0, len(values) - 6, size=(10000, math.ceil(len(values) / 7)))
    indices = (starts[:, :, None] + np.arange(7)).reshape(10000, -1)[:, : len(values)]
    means = values[indices].mean(axis=1)
    return result | {"lower_bound": float(np.quantile(means, alpha, method="lower")), "reason": None}
