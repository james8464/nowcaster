from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.features.builder import FeatureBuilder
from src.features.leakage import LookaheadError, assert_no_lookahead


@pytest.fixture
def feature_inputs():
    financials = pd.DataFrame(
        [
            {
                "company_id": "SBUX",
                "fiscal_quarter": f"202{year}Q{quarter}",
                "period_start": date(2020 + year, quarter * 3 - 2, 1),
                "period_end": date(2020 + year, quarter * 3, 28),
                "available_date": date(2020 + year, quarter * 3 + 1 if quarter < 4 else 12, 20),
                "revenue": 100 + year * 10 + quarter,
            }
            for year in range(4)
            for quarter in range(1, 5)
        ]
    )
    financials.loc[financials["fiscal_quarter"] == "2023Q4", "available_date"] = date(2024, 2, 5)
    earnings = pd.DataFrame([{"company_id": "SBUX", "fiscal_quarter": "2023Q4", "earnings_date": date(2024, 2, 5)}])
    alternative = pd.DataFrame(
        {
            "company_id": "SBUX",
            "signal": "wikipedia_pageviews",
            "observation_date": pd.date_range("2023-10-01", periods=130, freq="D").date,
            "available_date": pd.date_range("2023-10-02", periods=130, freq="D").date,
            "value": range(100, 230),
            "source": "wikimedia",
        }
    )
    return financials, earnings, alternative


def test_feature_builder_enforces_input_availability_at_cutoff(feature_inputs):
    financials, earnings, alternative = feature_inputs

    frame = FeatureBuilder(financials, earnings, alternative).build(horizons=[7])

    assert not frame.empty
    assert (frame["maximum_input_available_date"] <= frame["forecast_cutoff_date"]).all()
    assert frame["forecast_cutoff_date"].unique().tolist() == [date(2024, 1, 29)]


def test_future_observation_does_not_change_historical_features(feature_inputs):
    financials, earnings, alternative = feature_inputs
    before = FeatureBuilder(financials, earnings, alternative).build(horizons=[7])
    future = alternative.iloc[-1].copy()
    future["observation_date"] = date(2024, 2, 4)
    future["available_date"] = date(2024, 2, 5)
    future["value"] = 999999
    mutated = pd.concat([alternative, pd.DataFrame([future])], ignore_index=True)

    after = FeatureBuilder(financials, earnings, mutated).build(horizons=[7])

    pd.testing.assert_frame_equal(before, after)


def test_assert_no_lookahead_fails_loudly():
    frame = pd.DataFrame(
        [
            {
                "company_id": "SBUX",
                "fiscal_quarter": "2024Q1",
                "feature_name": "bad",
                "forecast_cutoff_date": date(2024, 1, 1),
                "maximum_input_available_date": date(2024, 1, 1) + timedelta(days=1),
            }
        ]
    )

    with pytest.raises(LookaheadError, match="SBUX"):
        assert_no_lookahead(frame)
