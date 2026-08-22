from __future__ import annotations

from datetime import date

import pandas as pd

from src.consensus.csv_provider import CsvConsensusProvider
from src.consensus.variant import build_variant_signals


def test_csv_expectations_flow_into_auditable_variant_signal(tmp_path):
    path = tmp_path / "expectations.csv"
    pd.DataFrame(
        [
            {
                "ticker": "SBUX",
                "fiscal_quarter": "2024Q2",
                "as_of_date": "2024-04-18",
                "consensus_revenue": 9000.0,
                "consensus_eps": 0.8,
                "number_of_analysts": 20,
            }
        ]
    ).to_csv(path, index=False)
    expectations = CsvConsensusProvider(path).estimates(date(2024, 4, 20))
    forecasts = pd.DataFrame(
        [
            {
                "forecast_id": "f1",
                "company_id": "SBUX",
                "fiscal_quarter": "2024Q2",
                "forecast_cutoff_date": date(2024, 4, 20),
                "horizon_days": 7,
                "forecast_revenue": 9450.0,
                "confidence_score": 75.0,
            }
        ]
    )

    signals = build_variant_signals(forecasts, expectations)

    assert len(signals) == 1
    assert signals.iloc[0].variant == 0.05
    assert signals.iloc[0].expectation_mode == "manual_csv"
