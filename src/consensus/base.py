from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd


@dataclass(frozen=True)
class Expectation:
    estimate_id: str
    company_id: str
    fiscal_quarter: str
    as_of_date: date
    revenue: float
    eps: float | None
    number_of_analysts: int | None
    mode: str
    display_label: str


class ConsensusProvider(Protocol):
    def estimates(self, as_of: date) -> pd.DataFrame: ...


DISPLAY_LABELS = {
    "manual_csv": "Manually imported consensus",
    "api": "API consensus",
    "expectation_proxy": "Historical expectation proxy",
}


def select_expectation(
    estimates: pd.DataFrame,
    cutoff: date,
    *,
    company_id: str | None = None,
    fiscal_quarter: str | None = None,
) -> Expectation | None:
    """Select the latest estimate that was observable by ``cutoff``."""
    if estimates.empty:
        return None
    required = {"company_id", "fiscal_quarter", "as_of_date", "consensus_revenue", "mode"}
    missing = required - set(estimates.columns)
    if missing:
        raise ValueError(f"Estimates are missing columns: {sorted(missing)}")
    eligible = estimates.copy()
    eligible["as_of_date"] = pd.to_datetime(eligible["as_of_date"], errors="raise").dt.date
    eligible = eligible[eligible["as_of_date"] <= cutoff]
    if company_id is not None:
        eligible = eligible[eligible["company_id"] == company_id]
    if fiscal_quarter is not None:
        eligible = eligible[eligible["fiscal_quarter"] == fiscal_quarter]
    if eligible.empty:
        return None
    row = eligible.sort_values("as_of_date").iloc[-1]
    mode = str(row["mode"])
    eps = row.get("consensus_eps")
    analysts = row.get("number_of_analysts")
    return Expectation(
        estimate_id=str(row.get("estimate_id", "")),
        company_id=str(row["company_id"]),
        fiscal_quarter=str(row["fiscal_quarter"]),
        as_of_date=row["as_of_date"],
        revenue=float(row["consensus_revenue"]),
        eps=float(eps) if pd.notna(eps) else None,
        number_of_analysts=int(analysts) if pd.notna(analysts) else None,
        mode=mode,
        display_label=DISPLAY_LABELS.get(mode, mode.replace("_", " ").title()),
    )
