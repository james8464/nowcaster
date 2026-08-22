from __future__ import annotations

from datetime import date

import pandas as pd

from src.validation.fundamentals import validate_financials


def test_validation_reports_negative_revenue_duplicate_period_and_impossible_dates():
    frame = pd.DataFrame(
        [
            {
                "company_id": "SBUX",
                "fiscal_quarter": "2024Q1",
                "period_end": date(2024, 1, 1),
                "available_date": date(2023, 12, 31),
                "revenue": -5,
            },
            {
                "company_id": "SBUX",
                "fiscal_quarter": "2024Q1",
                "period_end": date(2024, 1, 1),
                "available_date": date(2024, 2, 1),
                "revenue": 5,
            },
        ]
    )

    issues = validate_financials(frame)
    rules = {issue.rule for issue in issues}

    assert {"negative_revenue", "duplicate_company_quarter", "available_before_period_end"} <= rules


def test_validation_accepts_clean_quarterly_history():
    frame = pd.DataFrame(
        [
            {
                "company_id": "SBUX",
                "fiscal_quarter": "2024Q1",
                "period_end": date(2023, 12, 31),
                "available_date": date(2024, 1, 31),
                "revenue": 9_350_000_000,
            }
        ]
    )

    assert validate_financials(frame) == []
