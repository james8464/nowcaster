from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class QualityIssue:
    rule: str
    severity: str
    entity_key: str
    observed_value: Any
    message: str


def validate_financials(frame: pd.DataFrame) -> list[QualityIssue]:
    if frame.empty:
        return [QualityIssue("empty_financials", "error", "dataset", None, "No financial rows were provided")]
    issues: list[QualityIssue] = []
    duplicate_mask = frame.duplicated(["company_id", "fiscal_quarter"], keep=False)
    for row in frame[duplicate_mask].itertuples():
        issues.append(
            QualityIssue(
                "duplicate_company_quarter",
                "error",
                f"{row.company_id}:{row.fiscal_quarter}",
                None,
                "Multiple normalized rows exist for the company-quarter",
            )
        )
    for row in frame.itertuples():
        key = f"{row.company_id}:{row.fiscal_quarter}"
        if pd.notna(row.revenue) and row.revenue <= 0:
            issues.append(QualityIssue("negative_revenue", "error", key, row.revenue, "Revenue must be positive"))
        if row.available_date < row.period_end:
            issues.append(
                QualityIssue(
                    "available_before_period_end",
                    "error",
                    key,
                    row.available_date,
                    "Availability cannot precede period end",
                )
            )
    return issues
