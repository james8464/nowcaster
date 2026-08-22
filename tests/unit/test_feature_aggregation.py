from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.features.aggregation import aggregate_attention_as_of


def test_attention_aggregation_excludes_unavailable_and_future_observations():
    observations = pd.DataFrame(
        {
            "observation_date": pd.date_range("2024-01-01", periods=35, freq="D").date,
            "available_date": pd.date_range("2024-01-02", periods=35, freq="D").date,
            "value": list(range(1, 36)),
        }
    )

    values = aggregate_attention_as_of(observations, cutoff=date(2024, 2, 1), trailing_days=28)

    assert values["trailing_mean"] == pytest.approx(sum(range(4, 32)) / 28)
    assert values["maximum_input_available_date"] == date(2024, 2, 1)


def test_attention_aggregation_returns_missing_flags_for_insufficient_history():
    observations = pd.DataFrame(
        {"observation_date": [date(2024, 1, 1)], "available_date": [date(2024, 1, 2)], "value": [10]}
    )

    values = aggregate_attention_as_of(observations, cutoff=date(2024, 1, 3), trailing_days=28)

    assert values["insufficient_history"] == 1.0
    assert pd.isna(values["momentum"])
