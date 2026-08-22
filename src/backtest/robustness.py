from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm


@dataclass(frozen=True)
class BootstrapResult:
    mean_return: float
    ci_low: float
    ci_high: float
    probability_positive: float
    samples: int
    block_size: int


def run_block_bootstrap(
    returns: np.ndarray | list[float],
    *,
    block_size: int = 10,
    samples: int = 2_000,
    seed: int = 42,
) -> BootstrapResult:
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0 or block_size <= 0 or samples <= 0:
        raise ValueError("returns, block_size, and samples must be non-empty and positive")
    block_size = min(block_size, len(values))
    generator = np.random.default_rng(seed)
    means = np.empty(samples)
    blocks_needed = int(np.ceil(len(values) / block_size))
    offsets = np.arange(block_size)
    for sample in range(samples):
        starts = generator.integers(0, len(values), size=blocks_needed)
        indices = ((starts[:, None] + offsets) % len(values)).ravel()[: len(values)]
        means[sample] = values[indices].mean()
    return BootstrapResult(
        mean_return=float(values.mean()),
        ci_low=float(np.quantile(means, 0.025)),
        ci_high=float(np.quantile(means, 0.975)),
        probability_positive=float((means > 0).mean()),
        samples=samples,
        block_size=block_size,
    )


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    if not p_values:
        return []
    values = np.asarray(p_values, dtype=float)
    if np.any(~np.isfinite(values)) or np.any((values < 0) | (values > 1)):
        raise ValueError("p-values must be finite and in [0, 1]")
    order = np.argsort(values)
    ranked = values[order]
    adjusted_ranked = ranked * len(values) / np.arange(1, len(values) + 1)
    adjusted_ranked = np.minimum.accumulate(adjusted_ranked[::-1])[::-1]
    adjusted = np.empty_like(adjusted_ranked)
    adjusted[order] = np.clip(adjusted_ranked, 0, 1)
    return adjusted.tolist()


def deflated_sharpe_probability(
    sharpe: float,
    *,
    observations: int,
    trials: int,
    skew: float,
    kurtosis: float,
) -> float:
    if observations < 3 or trials <= 0:
        return 0.0
    expected_maximum = norm.ppf(1 - 1 / max(trials + 1, 2)) / np.sqrt(observations - 1)
    variance = (1 - skew * sharpe + ((kurtosis - 1) / 4) * sharpe**2) / (observations - 1)
    if variance <= 0 or not np.isfinite(variance):
        return 0.0
    return float(np.clip(norm.cdf((sharpe - expected_maximum) / np.sqrt(variance)), 0, 1))


def leave_one_group_out(
    frame: pd.DataFrame,
    *,
    group_column: str,
    return_column: str = "net_return",
) -> pd.DataFrame:
    if group_column not in frame or return_column not in frame:
        raise ValueError("group and return columns are required")
    rows: list[dict[str, object]] = []
    for group in sorted(frame[group_column].dropna().unique(), key=str):
        remaining = pd.to_numeric(frame.loc[frame[group_column] != group, return_column], errors="coerce").dropna()
        rows.append(
            {
                "excluded_group": str(group),
                "observations": len(remaining),
                "mean_return": float(remaining.mean()) if len(remaining) else float("nan"),
                "cumulative_return": float((1 + remaining).prod() - 1) if len(remaining) else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def subperiod_analysis(
    frame: pd.DataFrame,
    *,
    date_column: str = "date",
    return_column: str = "net_return",
    frequency: str = "YE",
) -> pd.DataFrame:
    if date_column not in frame or return_column not in frame:
        raise ValueError("date and return columns are required")
    data = frame[[date_column, return_column]].copy()
    data[date_column] = pd.to_datetime(data[date_column], errors="coerce")
    data[return_column] = pd.to_numeric(data[return_column], errors="coerce")
    data = data.dropna().set_index(date_column)
    grouped = data[return_column].resample(frequency)
    result = grouped.agg(observations="count", mean_return="mean")
    result["cumulative_return"] = grouped.apply(lambda values: float((1 + values).prod() - 1))
    return result.reset_index().rename(columns={date_column: "period_end"})


def volatility_regime_analysis(
    frame: pd.DataFrame,
    *,
    return_column: str = "net_return",
    volatility_column: str = "forecast_volatility",
) -> pd.DataFrame:
    if return_column not in frame or volatility_column not in frame:
        raise ValueError("return and volatility columns are required")
    data = frame[[return_column, volatility_column]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(data) < 6 or data[volatility_column].nunique() < 3:
        return pd.DataFrame(columns=["regime", "observations", "mean_return", "hit_rate"])
    data["regime"] = pd.qcut(data[volatility_column], q=3, labels=["low", "medium", "high"], duplicates="drop")
    return (
        data.groupby("regime", observed=True)[return_column]
        .agg(observations="count", mean_return="mean", hit_rate=lambda values: float((values > 0).mean()))
        .reset_index()
    )
