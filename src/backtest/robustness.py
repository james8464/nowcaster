from __future__ import annotations

import itertools
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


@dataclass(frozen=True)
class PBOResult:
    probability: float
    combinations: int
    overfit_combinations: int
    logits: tuple[float, ...]


@dataclass(frozen=True)
class ParameterStabilityResult:
    best_parameters: tuple[tuple[str, object], ...]
    best_score: float
    neighboring_scores: tuple[float, ...]
    positive_neighbor_fraction: float
    neighbor_median_ratio: float
    stable: bool


@dataclass(frozen=True)
class AttributionRecord:
    group: str
    observations: int
    mean_return: float
    cumulative_return: float


@dataclass(frozen=True)
class PerformanceAttribution:
    by_fold: tuple[AttributionRecord, ...]
    by_year: tuple[AttributionRecord, ...]
    by_side: tuple[AttributionRecord, ...]


@dataclass(frozen=True)
class CostSurvivalResult:
    base_cumulative_return: float
    doubled_cost_cumulative_return: float
    survives: bool


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


def cscv_probability_of_backtest_overfitting(
    performance: pd.DataFrame,
    *,
    segments: int = 10,
) -> PBOResult:
    """Estimate PBO with combinatorially symmetric cross-validation.

    Rows are chronological observations and columns are candidate strategies. Each
    combination selects the in-sample winner, then ranks that exact candidate on
    the complementary observations. A below-median out-of-sample rank is an
    overfit combination.
    """

    values = performance.apply(pd.to_numeric, errors="coerce")
    if values.empty or values.shape[1] < 2 or values.isna().any().any():
        raise ValueError("performance requires finite observations for at least two candidates")
    if segments < 2 or segments % 2 or segments > len(values):
        raise ValueError("segments must be even and no greater than the observation count")
    blocks = np.array_split(np.arange(len(values)), segments)
    logits: list[float] = []
    half = segments // 2
    for selected_blocks in itertools.combinations(range(segments), half):
        selected = set(selected_blocks)
        train_index = np.concatenate([blocks[index] for index in selected])
        test_index = np.concatenate([blocks[index] for index in range(segments) if index not in selected])
        train_scores = values.iloc[train_index].mean(axis=0)
        winner = train_scores.to_numpy().argmax()
        test_scores = values.iloc[test_index].mean(axis=0)
        rank = float(test_scores.rank(method="average", ascending=True).iloc[winner])
        relative_rank = (rank - 0.5) / len(test_scores)
        logits.append(float(np.log(relative_rank / (1 - relative_rank))))
    overfit = sum(value <= 0 for value in logits)
    return PBOResult(
        probability=overfit / len(logits),
        combinations=len(logits),
        overfit_combinations=overfit,
        logits=tuple(logits),
    )


def parameter_stability(
    trials: pd.DataFrame,
    *,
    parameter_columns: list[str],
    score_column: str = "net_sharpe",
) -> ParameterStabilityResult:
    required = {*parameter_columns, score_column}
    missing = required - set(trials.columns)
    if missing or not parameter_columns:
        raise ValueError(f"parameter trials are missing columns: {sorted(missing)}")
    data = trials[list(parameter_columns) + [score_column]].copy()
    data[score_column] = pd.to_numeric(data[score_column], errors="coerce")
    if data.empty or data.isna().any().any():
        raise ValueError("parameter trials must be complete and finite")
    best_index = data[score_column].idxmax()
    best = data.loc[best_index]
    grids: dict[str, list[object]] = {}
    for column in parameter_columns:
        values = data[column].unique().tolist()
        try:
            grids[column] = sorted(values)
        except TypeError:
            grids[column] = sorted(values, key=str)
    neighbors = pd.Series(True, index=data.index)
    differs = pd.Series(False, index=data.index)
    for column in parameter_columns:
        grid = grids[column]
        best_position = grid.index(best[column])
        positions = data[column].map({value: index for index, value in enumerate(grid)})
        distance = (positions - best_position).abs()
        neighbors &= distance <= 1
        differs |= distance > 0
    scores = tuple(float(value) for value in data.loc[neighbors & differs, score_column])
    best_score = float(best[score_column])
    positive_fraction = float(np.mean(np.asarray(scores) > 0)) if scores else 0.0
    median_ratio = float(np.median(scores) / abs(best_score)) if scores and best_score != 0 else 0.0
    return ParameterStabilityResult(
        best_parameters=tuple((column, best[column]) for column in parameter_columns),
        best_score=best_score,
        neighboring_scores=scores,
        positive_neighbor_fraction=positive_fraction,
        neighbor_median_ratio=median_ratio,
        stable=positive_fraction >= 0.5 and median_ratio >= 0.5,
    )


def _attribution_records(groups: pd.Series, returns: pd.Series) -> tuple[AttributionRecord, ...]:
    rows: list[AttributionRecord] = []
    data = pd.DataFrame({"group": groups.astype(str), "return": returns})
    for group, frame in data.groupby("group", sort=True):
        values = frame["return"]
        rows.append(
            AttributionRecord(
                group=str(group),
                observations=len(values),
                mean_return=float(values.mean()),
                cumulative_return=float((1 + values).prod() - 1),
            )
        )
    return tuple(rows)


def performance_attribution(
    frame: pd.DataFrame,
    *,
    date_column: str = "execution_timestamp",
    fold_column: str = "fold",
    side_column: str = "side",
    return_column: str = "net_return",
) -> PerformanceAttribution:
    required = {date_column, fold_column, side_column, return_column}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"attribution input is missing columns: {sorted(missing)}")
    dates = pd.to_datetime(frame[date_column], utc=True, errors="coerce")
    returns = pd.to_numeric(frame[return_column], errors="coerce")
    if dates.isna().any() or returns.isna().any():
        raise ValueError("attribution dates and returns must be valid")
    return PerformanceAttribution(
        by_fold=_attribution_records(frame[fold_column], returns),
        by_year=_attribution_records(dates.dt.year, returns),
        by_side=_attribution_records(frame[side_column], returns),
    )


def doubled_cost_survival(
    gross_returns: np.ndarray | list[float],
    base_costs: np.ndarray | list[float],
) -> CostSurvivalResult:
    gross = np.asarray(gross_returns, dtype=float)
    costs = np.asarray(base_costs, dtype=float)
    if gross.ndim != 1 or costs.ndim != 1 or len(gross) == 0 or len(gross) != len(costs):
        raise ValueError("gross returns and costs must be equally sized non-empty vectors")
    if np.any(~np.isfinite(gross)) or np.any(~np.isfinite(costs)) or np.any(costs < 0):
        raise ValueError("gross returns must be finite and costs finite and non-negative")
    base = float(np.prod(1 + gross - costs) - 1)
    doubled = float(np.prod(1 + gross - 2 * costs) - 1)
    return CostSurvivalResult(
        base_cumulative_return=base,
        doubled_cost_cumulative_return=doubled,
        survives=doubled >= 0,
    )


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
