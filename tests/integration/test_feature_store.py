from __future__ import annotations

from datetime import date

import pandas as pd

from src.database.engine import Database
from src.features.builder import FeatureBuilder, feature_rows


def test_feature_store_persists_auditable_long_form_rows(tmp_path):
    financials = pd.DataFrame(
        [
            {
                "company_id": "SBUX",
                "fiscal_quarter": f"2023Q{quarter}",
                "period_start": date(2023, quarter * 3 - 2, 1),
                "period_end": date(2023, quarter * 3, 28),
                "available_date": date(2023, min(quarter * 3 + 1, 12), 20),
                "revenue": 100 + quarter,
            }
            for quarter in range(1, 5)
        ]
    )
    financials.loc[3, "available_date"] = date(2024, 2, 1)
    earnings = pd.DataFrame([{"company_id": "SBUX", "fiscal_quarter": "2023Q4", "earnings_date": date(2024, 2, 1)}])
    alternative = pd.DataFrame(
        {
            "company_id": ["SBUX"] * 30,
            "signal": ["wikipedia_pageviews"] * 30,
            "observation_date": pd.date_range("2023-12-01", periods=30).date,
            "available_date": pd.date_range("2023-12-02", periods=30).date,
            "value": range(30),
            "source": ["wikimedia"] * 30,
        }
    )
    frame = FeatureBuilder(financials, earnings, alternative).build(horizons=[7])
    database = Database.from_url(f"duckdb:///{tmp_path / 'features.duckdb'}")
    database.initialize()

    inserted = database.upsert("features_quarterly", feature_rows(frame))

    assert inserted == len(frame)
    assert (
        database.scalar("select max(maximum_input_available_date <= forecast_cutoff_date) from features_quarterly")
        is True
    )
