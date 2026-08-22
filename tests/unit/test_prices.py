from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.ingestion.prices import CsvPriceProvider, normalize_prices, parse_yahoo_chart, price_rows


def test_csv_provider_normalizes_dates_adjusted_close_and_symbol():
    path = Path(__file__).parents[1] / "fixtures" / "prices" / "sample_prices.csv"

    frame = CsvPriceProvider({"SBUX": path}).fetch("SBUX", date(2024, 2, 2), date(2024, 2, 8))

    assert list(frame.columns) == [
        "symbol",
        "trading_date",
        "raw_close",
        "adjusted_close",
        "volume",
        "currency",
        "adjustment_status",
    ]
    assert frame.iloc[-1].adjusted_close == 108
    assert frame.iloc[0].trading_date == date(2024, 2, 2)


def test_price_normalization_rejects_duplicates():
    raw = pd.DataFrame({"Date": ["2024-01-02", "2024-01-02"], "Close": [10, 11], "Adj Close": [10, 11]})

    with pytest.raises(ValueError, match="duplicate"):
        normalize_prices(raw, "SBUX")


def test_price_rows_have_stable_natural_keys():
    path = Path(__file__).parents[1] / "fixtures" / "prices" / "sample_prices.csv"
    frame = CsvPriceProvider({"SBUX": path}).fetch("SBUX", date(2024, 2, 2), date(2024, 2, 8))

    first, second = price_rows(frame, source="test"), price_rows(frame, source="test")

    assert first[0]["price_id"] == second[0]["price_id"]
    assert first[0]["source"] == "test"


def test_yahoo_chart_parser_preserves_raw_and_adjusted_prices():
    fixture = Path(__file__).parents[1] / "fixtures" / "prices" / "yahoo_chart_sample.json"

    frame = parse_yahoo_chart(fixture.read_text(encoding="utf-8"))

    assert frame.iloc[0].raw_close == 100.0
    assert frame.iloc[0].adjusted_close == 99.5
    assert frame.iloc[0].symbol == "SBUX"
