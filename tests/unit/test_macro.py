from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.ingestion.macro import MacroObservation, parse_fred, validate_point_in_time_macro


def test_fred_parser_preserves_release_vintage_and_availability():
    fixture = Path(__file__).parents[1] / "fixtures" / "macro" / "fred_sample.json"

    rows = parse_fred(json.loads(fixture.read_text()), "RSAFS")

    assert rows[0].observation_date == date(2024, 1, 1)
    assert rows[0].available_date == date(2024, 2, 15)
    assert rows[0].vintage_date == date(2024, 2, 15)
    assert rows[0].value == 700000


def test_macro_validation_rejects_latest_revised_values_for_historical_feature_use():
    frame = pd.DataFrame(
        [
            {
                "series_id": "RSAFS",
                "observation_date": date(2020, 1, 1),
                "available_date": date(2020, 2, 15),
                "vintage_date": date(2026, 8, 22),
                "source_version": "latest_revised",
            }
        ]
    )

    with pytest.raises(ValueError, match="vintage-safe"):
        validate_point_in_time_macro(frame)


def test_macro_observation_rejects_availability_before_observation():
    with pytest.raises(ValueError, match="availability"):
        MacroObservation(
            series_id="RSAFS",
            observation_date=date(2024, 2, 1),
            available_date=date(2024, 1, 31),
            vintage_date=date(2024, 1, 31),
            value=1.0,
            unit="index",
        )
