from __future__ import annotations

import math

import numpy as np
import pandas as pd
import statsmodels.api as sm


def summarize_buckets(
    event_returns: pd.DataFrame,
    bootstrap_samples: int = 2_000,
    seed: int = 42,
    *,
    return_column: str = "abnormal_return",
) -> pd.DataFrame:
    if bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be positive")
    required = {"variant_bucket", return_column}
    missing = required - set(event_returns.columns)
    if missing:
        raise ValueError(f"Event returns are missing columns: {sorted(missing)}")
    generator = np.random.default_rng(seed)
    rows: list[dict[str, float | int | str]] = []
    for bucket, group in event_returns.groupby("variant_bucket", dropna=False):
        values = pd.to_numeric(group[return_column], errors="coerce").dropna().to_numpy(dtype=float)
        if len(values) == 0:
            continue
        bootstrap_means = generator.choice(values, size=(bootstrap_samples, len(values)), replace=True).mean(axis=1)
        standard_error = values.std(ddof=1) / math.sqrt(len(values)) if len(values) > 1 else math.nan
        rows.append(
            {
                "variant_bucket": str(bucket),
                "n": len(values),
                "mean": float(values.mean()),
                "median": float(np.median(values)),
                "standard_deviation": float(values.std(ddof=1)) if len(values) > 1 else math.nan,
                "hit_rate": float((values > 0).mean()),
                "t_statistic": float(values.mean() / standard_error)
                if standard_error and not math.isnan(standard_error)
                else math.nan,
                "ci_low": float(np.quantile(bootstrap_means, 0.025)),
                "ci_high": float(np.quantile(bootstrap_means, 0.975)),
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=["n", "mean", "median", "standard_deviation", "hit_rate", "t_statistic", "ci_low", "ci_high"]
        )
    return pd.DataFrame(rows).set_index("variant_bucket").sort_index()


def newey_west_variant_regression(
    event_returns: pd.DataFrame,
    *,
    max_lags: int = 1,
    return_column: str = "abnormal_return",
) -> dict[str, float | int | str]:
    data = event_returns[["variant_zscore", return_column]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(data) < 3:
        return {
            "n": len(data),
            "coefficient": math.nan,
            "standard_error": math.nan,
            "t_statistic": math.nan,
            "p_value": math.nan,
            "caveat": "Insufficient observations; multiple testing and event dependence remain material.",
        }
    design = sm.add_constant(data["variant_zscore"])
    fit = sm.OLS(data[return_column], design).fit(cov_type="HAC", cov_kwds={"maxlags": max_lags})
    return {
        "n": len(data),
        "coefficient": float(fit.params["variant_zscore"]),
        "standard_error": float(fit.bse["variant_zscore"]),
        "t_statistic": float(fit.tvalues["variant_zscore"]),
        "p_value": float(fit.pvalues["variant_zscore"]),
        "caveat": (
            "Exploratory association only: small samples, overlapping events, model selection, and multiple testing "
            "can overstate significance."
        ),
    }


def date_clustered_variant_regression(
    event_returns: pd.DataFrame,
    *,
    date_column: str = "event_date",
    return_column: str = "abnormal_return",
) -> dict[str, float | int | str]:
    required = {"variant_zscore", date_column, return_column}
    missing = required - set(event_returns.columns)
    if missing:
        raise ValueError(f"Event returns are missing columns: {sorted(missing)}")
    data = event_returns[["variant_zscore", date_column, return_column]].dropna().copy()
    data[["variant_zscore", return_column]] = data[["variant_zscore", return_column]].apply(
        pd.to_numeric, errors="coerce"
    )
    data = data.dropna()
    if len(data) < 6 or data[date_column].nunique() < 2:
        return {
            "n": len(data),
            "clusters": int(data[date_column].nunique()),
            "coefficient": math.nan,
            "standard_error": math.nan,
            "p_value": math.nan,
            "caveat": "Insufficient independent event dates for clustered inference.",
        }
    design = sm.add_constant(data["variant_zscore"])
    fit = sm.OLS(data[return_column], design).fit(
        cov_type="cluster", cov_kwds={"groups": data[date_column], "use_correction": True}
    )
    return {
        "n": len(data),
        "clusters": int(data[date_column].nunique()),
        "coefficient": float(fit.params["variant_zscore"]),
        "standard_error": float(fit.bse["variant_zscore"]),
        "p_value": float(fit.pvalues["variant_zscore"]),
        "caveat": "Date-clustered exploratory inference; selection and small-cluster bias may remain.",
    }
