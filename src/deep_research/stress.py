from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

REQUIRED_SCENARIOS = (
    "baseline",
    "doubled_costs",
    "severe_costs",
    "delayed_fills",
    "reduced_liquidity",
    "skipped_best_trades",
    "clustered_losses",
    "parameter_neighbors",
    "alternate_start_dates",
    "block_bootstrap",
)


@dataclass(frozen=True, slots=True)
class StressScenario:
    name: str
    cumulative_return: float
    maximum_drawdown: float
    sharpe: float
    passed: bool


@dataclass(frozen=True, slots=True)
class StressReport:
    scenarios: tuple[StressScenario, ...]
    evidence_grade: str
    caveat: str

    def by_name(self, name: str) -> StressScenario:
        try:
            return next(scenario for scenario in self.scenarios if scenario.name == name)
        except StopIteration as error:
            raise KeyError(name) from error


def _metrics(name: str, returns: np.ndarray) -> StressScenario:
    if not len(returns) or np.any(~np.isfinite(returns)):
        return StressScenario(name, 0.0, 1.0, 0.0, False)
    equity = np.cumprod(1.0 + np.clip(returns, -0.999999, None))
    cumulative = float(equity[-1] - 1.0)
    peaks = np.maximum.accumulate(equity)
    drawdown = float(np.max((peaks - equity) / peaks))
    deviation = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(np.mean(returns) / deviation * math.sqrt(len(returns))) if deviation > 0 else 0.0
    return StressScenario(name, cumulative, drawdown, sharpe, cumulative > 0 and drawdown <= 0.10)


def _stationary_bootstrap_cumulative(
    returns: np.ndarray,
    *,
    seed: int,
    paths: int = 500,
    mean_block: int = 10,
) -> np.ndarray:
    generator = np.random.default_rng(seed)
    cumulative = np.empty(paths, dtype=float)
    restart_probability = 1.0 / min(mean_block, len(returns))
    for path in range(paths):
        indices = np.empty(len(returns), dtype=int)
        indices[0] = int(generator.integers(0, len(returns)))
        for position in range(1, len(returns)):
            if generator.random() < restart_probability:
                indices[position] = int(generator.integers(0, len(returns)))
            else:
                indices[position] = (indices[position - 1] + 1) % len(returns)
        cumulative[path] = float(np.prod(1 + np.clip(returns[indices], -0.999999, None)) - 1)
    return cumulative


def evaluate_stress_matrix(
    gross_returns: list[float] | np.ndarray,
    costs: list[float] | np.ndarray,
    *,
    seed: int = 42,
    liquidity_observed: bool = False,
) -> StressReport:
    gross = np.asarray(gross_returns, dtype=float)
    cost = np.asarray(costs, dtype=float)
    if (
        gross.ndim != 1
        or cost.ndim != 1
        or not len(gross)
        or len(gross) != len(cost)
        or np.any(~np.isfinite(gross))
        or np.any(~np.isfinite(cost))
        or np.any(cost < 0)
    ):
        raise ValueError("stress simulation requires aligned finite returns and non-negative costs")

    baseline = gross - cost
    delayed = np.concatenate(([0.0], gross[:-1])) - cost
    reduced_liquidity = gross * 0.75 - cost * 2
    skipped = baseline.copy()
    winners = np.flatnonzero(skipped > 0)
    if len(winners):
        skip_count = max(1, math.ceil(len(winners) * 0.05))
        selected = winners[np.argsort(skipped[winners])[-skip_count:]]
        skipped[selected] = 0.0
    alternate_offset = max(1, len(baseline) // 10)
    bootstraps = _stationary_bootstrap_cumulative(baseline, seed=seed)
    bootstrap_tail = float(np.quantile(bootstraps, 0.05))

    scenarios = [
        _metrics("baseline", baseline),
        _metrics("doubled_costs", gross - 2 * cost),
        _metrics("severe_costs", gross - 4 * cost),
        _metrics("delayed_fills", delayed),
        _metrics("reduced_liquidity", reduced_liquidity),
        _metrics("skipped_best_trades", skipped),
        _metrics("clustered_losses", np.sort(baseline)),
        _metrics("parameter_neighbors", gross * 0.9 - cost),
        _metrics("alternate_start_dates", baseline[alternate_offset:]),
        StressScenario(
            "block_bootstrap",
            bootstrap_tail,
            max(0.0, -bootstrap_tail),
            0.0,
            bootstrap_tail > 0,
        ),
    ]
    assert tuple(scenario.name for scenario in scenarios) == REQUIRED_SCENARIOS
    if liquidity_observed:
        grade = "observed_liquidity"
        caveat = "Spread and liquidity inputs were observed for the intended venue."
    else:
        grade = "conservative_default_liquidity"
        caveat = "Conservative defaults were used because observed liquidity inputs were unavailable."
    return StressReport(tuple(scenarios), grade, caveat)


__all__ = ["REQUIRED_SCENARIOS", "StressReport", "StressScenario", "evaluate_stress_matrix"]
