from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from src.ingestion.wikipedia import parse_pageviews, wikipedia_rows


def test_wikipedia_parser_applies_conservative_availability_lag():
    fixture = Path(__file__).parents[1] / "fixtures" / "alternative" / "wikipedia_sample.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    rows = parse_pageviews(payload, company_id="SBUX", availability_lag_days=1)

    assert rows[0].observation_date == date(2024, 1, 1)
    assert rows[0].available_date == rows[0].observation_date + timedelta(days=1)
    assert rows[0].value == 1000
    assert rows[0].signal == "wikipedia_pageviews"


def test_wikipedia_database_rows_preserve_article_dimension():
    fixture = Path(__file__).parents[1] / "fixtures" / "alternative" / "wikipedia_sample.json"
    observations = parse_pageviews(json.loads(fixture.read_text()), company_id="SBUX")

    rows = wikipedia_rows(observations, source="wikimedia_test")

    assert rows[0]["dimensions"]["article"] == "Starbucks"
    assert rows[0]["source"] == "wikimedia_test"
