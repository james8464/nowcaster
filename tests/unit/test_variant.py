from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.consensus.variant import bucket_variant, build_variant_signals, safe_zscore


def test_safe_zscore_handles_constant_and_missing_values():
    result = safe_zscore(pd.Series([2.0, 2.0, float("nan")]))

    assert result.iloc[:2].tolist() == [0.0, 0.0]
    assert pd.isna(result.iloc[2])


@pytest.mark.parametrize(
    ("zscore", "expected"),
    [(1.6, "strongly_positive"), (0.7, "positive"), (0.0, "neutral"), (-0.7, "negative"), (-1.6, "strongly_negative")],
)
def test_variant_buckets(zscore, expected):
    assert bucket_variant(zscore) == expected


def test_variant_signal_uses_only_expectation_available_at_cutoff():
    cutoff = date(2024, 4, 20)
    forecasts = pd.DataFrame(
        [
            {
                "forecast_id": "f1",
                "company_id": "SBUX",
                "fiscal_quarter": "2024Q2",
                "forecast_cutoff_date": cutoff,
                "horizon_days": 7,
                "forecast_revenue": 9900.0,
                "confidence_score": 80.0,
            }
        ]
    )
    expectations = pd.DataFrame(
        [
            {
                "estimate_id": "e1",
                "company_id": "SBUX",
                "fiscal_quarter": "2024Q2",
                "as_of_date": date(2024, 4, 18),
                "consensus_revenue": 9000.0,
                "mode": "manual_csv",
            },
            {
                "estimate_id": "e2",
                "company_id": "SBUX",
                "fiscal_quarter": "2024Q2",
                "as_of_date": date(2024, 4, 22),
                "consensus_revenue": 10000.0,
                "mode": "manual_csv",
            },
        ]
    )

    signal = build_variant_signals(forecasts, expectations).iloc[0]

    assert signal.estimate_id == "e1"
    assert signal.variant == pytest.approx(0.1)
    assert signal.expectation_mode == "manual_csv"
    assert signal.expectation_as_of_date <= signal.forecast_cutoff_date
