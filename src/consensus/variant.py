from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.consensus.base import select_expectation
from src.utils.provenance import canonical_hash


def safe_zscore(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    standard_deviation = numeric.std(ddof=0)
    if pd.isna(standard_deviation) or standard_deviation == 0:
        return numeric.where(numeric.isna(), 0.0)
    return (numeric - numeric.mean()) / standard_deviation


def bucket_variant(zscore: float) -> str:
    if pd.isna(zscore):
        return "unclassified"
    if zscore >= 1.5:
        return "strongly_positive"
    if zscore >= 0.5:
        return "positive"
    if zscore > -0.5:
        return "neutral"
    if zscore > -1.5:
        return "negative"
    return "strongly_negative"


def build_variant_signals(forecasts: pd.DataFrame, expectations: pd.DataFrame) -> pd.DataFrame:
    """Compare forecasts with the latest expectation observable at each forecast cutoff."""
    required_forecasts = {
        "company_id",
        "fiscal_quarter",
        "forecast_cutoff_date",
        "horizon_days",
        "forecast_revenue",
    }
    missing = required_forecasts - set(forecasts.columns)
    if missing:
        raise ValueError(f"Forecasts are missing columns: {sorted(missing)}")
    rows: list[dict[str, object]] = []
    for row in forecasts.itertuples(index=False):
        cutoff = pd.Timestamp(row.forecast_cutoff_date).date()
        selected = select_expectation(
            expectations,
            cutoff,
            company_id=str(row.company_id),
            fiscal_quarter=str(row.fiscal_quarter),
        )
        if selected is None or selected.revenue <= 0:
            continue
        forecast_id = str(
            getattr(
                row,
                "forecast_id",
                canonical_hash([row.company_id, row.fiscal_quarter, cutoff, row.horizon_days, row.forecast_revenue])[
                    :24
                ],
            )
        )
        variant = (float(row.forecast_revenue) - selected.revenue) / selected.revenue
        freshness_days = (cutoff - selected.as_of_date).days
        forecast_confidence = getattr(row, "confidence_score", math.nan)
        rows.append(
            {
                "signal_id": canonical_hash([forecast_id, selected.estimate_id])[:24],
                "forecast_id": forecast_id,
                "estimate_id": selected.estimate_id,
                "company_id": row.company_id,
                "fiscal_quarter": row.fiscal_quarter,
                "forecast_cutoff_date": cutoff,
                "horizon_days": int(row.horizon_days),
                "forecast_revenue": float(row.forecast_revenue),
                "expectation_revenue": selected.revenue,
                "expectation_as_of_date": selected.as_of_date,
                "expectation_mode": selected.mode,
                "expectation_label": selected.display_label,
                "variant": variant,
                "confidence_score": float(forecast_confidence) if pd.notna(forecast_confidence) else np.nan,
                "confidence_components": {
                    "forecast_confidence": float(forecast_confidence) if pd.notna(forecast_confidence) else None,
                    "expectation_freshness_days": freshness_days,
                    "number_of_analysts": selected.number_of_analysts,
                },
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    grouping = ["forecast_cutoff_date", "horizon_days"]
    result["variant_zscore"] = result.groupby(grouping, dropna=False)["variant"].transform(safe_zscore)
    result["variant_bucket"] = result["variant_zscore"].map(bucket_variant)
    if (result["expectation_as_of_date"] > result["forecast_cutoff_date"]).any():
        raise AssertionError("Variant signal contains a future expectation revision")
    return result.sort_values(grouping + ["company_id"]).reset_index(drop=True)
