from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.backtest.event_study import run_event_study


def _prices(symbol: str, start: date, values: list[float]) -> list[dict]:
    return [
        {"symbol": symbol, "trading_date": start + timedelta(days=index), "adjusted_close": value}
        for index, value in enumerate(values)
    ]


def test_event_study_joins_signal_to_identical_market_and_sector_dates():
    prices = pd.DataFrame(
        _prices("SBUX", date(2024, 1, 1), [100, 101, 104, 103, 106])
        + _prices("SPY", date(2024, 1, 1), [100, 100, 102, 102, 103])
        + _prices("XLY", date(2024, 1, 1), [100, 101, 102, 102, 104])
    )
    signals = pd.DataFrame(
        [
            {
                "signal_id": "s1",
                "company_id": "SBUX",
                "event_date": date(2024, 1, 2),
                "variant": 0.1,
                "variant_zscore": 1.7,
                "variant_bucket": "strongly_positive",
            }
        ]
    )

    result = run_event_study(
        signals, prices, [(0, 2)], {"market": "SPY", "SBUX": "XLY"}, bootstrap_samples=100, seed=42
    )
    row = result.event_returns.iloc[0]

    assert row.raw_return == 103 / 101 - 1
    assert row.abnormal_return == row.raw_return - (102 / 100 - 1)
    assert row.sector_adjusted_return == row.raw_return - (102 / 101 - 1)
    assert row.start_date == date(2024, 1, 2)
    assert row.end_date == date(2024, 1, 4)
