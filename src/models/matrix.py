from __future__ import annotations

import numpy as np
import pandas as pd

KEY_COLUMNS = [
    "company_id",
    "fiscal_quarter",
    "earnings_date",
    "forecast_cutoff_date",
    "horizon_days",
]


def build_model_matrix(feature_rows: pd.DataFrame, financials: pd.DataFrame) -> pd.DataFrame:
    """Pivot audited point-in-time features and attach the realized revenue target."""
    required_features = set(KEY_COLUMNS) | {
        "feature_name",
        "feature_value",
        "maximum_input_available_date",
    }
    required_financials = {"company_id", "fiscal_quarter", "revenue"}
    missing_features = required_features - set(feature_rows.columns)
    missing_financials = required_financials - set(financials.columns)
    if missing_features:
        raise ValueError(f"Feature rows are missing columns: {sorted(missing_features)}")
    if missing_financials:
        raise ValueError(f"Financials are missing columns: {sorted(missing_financials)}")
    if feature_rows.empty:
        return pd.DataFrame()

    duplicate_mask = feature_rows.duplicated(KEY_COLUMNS + ["feature_name"], keep=False)
    if duplicate_mask.any():
        raise ValueError("Each feature must be unique by company, quarter, cutoff, and horizon")

    features = feature_rows.copy()
    features["maximum_input_available_date"] = pd.to_datetime(features["maximum_input_available_date"]).dt.date
    wide = features.pivot(index=KEY_COLUMNS, columns="feature_name", values="feature_value").reset_index()
    wide.columns.name = None
    audit = features.groupby(KEY_COLUMNS, as_index=False, dropna=False)["maximum_input_available_date"].max()
    actuals = financials[["company_id", "fiscal_quarter", "revenue"]].copy()
    actuals = actuals.drop_duplicates(["company_id", "fiscal_quarter"], keep="last").rename(
        columns={"revenue": "actual_revenue"}
    )
    matrix = wide.merge(audit, on=KEY_COLUMNS, how="left", validate="one_to_one")
    matrix = matrix.merge(actuals, on=["company_id", "fiscal_quarter"], how="inner", validate="many_to_one")
    if "revenue_year_ago" not in matrix:
        raise ValueError("The revenue_year_ago feature is required to construct the target")
    valid = (matrix["actual_revenue"] > 0) & (matrix["revenue_year_ago"] > 0)
    matrix["target_revenue_yoy_log_growth"] = np.where(
        valid,
        np.log(matrix["actual_revenue"] / matrix["revenue_year_ago"]),
        np.nan,
    )
    return matrix.sort_values(["forecast_cutoff_date", "company_id", "fiscal_quarter", "horizon_days"]).reset_index(
        drop=True
    )
