from __future__ import annotations

from datetime import date

import pandas as pd

from src.consensus.base import Expectation
from src.utils.provenance import canonical_hash


def historical_expectation_proxy(
    financials: pd.DataFrame,
    *,
    company_id: str,
    fiscal_quarter: str,
    cutoff: date,
) -> Expectation | None:
    """Use the latest observable same-quarter revenue from the prior year as a transparent proxy."""
    required = {"company_id", "fiscal_quarter", "revenue", "available_date"}
    missing = required - set(financials.columns)
    if missing:
        raise ValueError(f"Financials are missing columns: {sorted(missing)}")
    match = pd.Series([fiscal_quarter]).str.extract(r"^(\d{4})Q([1-4])$").iloc[0]
    if match.isna().any():
        raise ValueError(f"Invalid fiscal quarter: {fiscal_quarter}")
    prior_quarter = f"{int(match.iloc[0]) - 1}Q{match.iloc[1]}"
    eligible = financials.copy()
    eligible["available_date"] = pd.to_datetime(eligible["available_date"], errors="raise").dt.date
    eligible = eligible[
        (eligible["company_id"] == company_id)
        & (eligible["fiscal_quarter"] == prior_quarter)
        & (eligible["available_date"] <= cutoff)
    ]
    if eligible.empty:
        return None
    row = eligible.sort_values("available_date").iloc[-1]
    estimate_id = canonical_hash([company_id, fiscal_quarter, cutoff, row.revenue, "expectation_proxy"])[:24]
    return Expectation(
        estimate_id=estimate_id,
        company_id=company_id,
        fiscal_quarter=fiscal_quarter,
        as_of_date=row.available_date,
        revenue=float(row.revenue),
        eps=None,
        number_of_analysts=None,
        mode="expectation_proxy",
        display_label="Historical expectation proxy",
    )
