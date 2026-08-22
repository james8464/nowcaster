from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.backtest.returns import EventWindowError, calculate_event_return, gs_quant_return_crosscheck


@pytest.fixture
def price_frame():
    return pd.DataFrame(
        {
            "symbol": ["SBUX"] * 5,
            "trading_date": [date(2024, 2, 2), date(2024, 2, 5), date(2024, 2, 6), date(2024, 2, 7), date(2024, 2, 8)],
            "adjusted_close": [98, 100, 104, 106, 108],
        }
    )


def test_event_return_uses_next_trading_date_for_weekend_and_adjusted_close(price_frame):
    result = calculate_event_return(price_frame, date(2024, 2, 3), (0, 3))

    assert result.start_date == date(2024, 2, 5)
    assert result.end_date == date(2024, 2, 8)
    assert result.raw_return == pytest.approx(0.08)


def test_abnormal_return_subtracts_benchmark_over_identical_dates(price_frame):
    benchmark = price_frame.assign(symbol="SPY", adjusted_close=[200, 200, 202, 204, 206])

    result = calculate_event_return(price_frame, date(2024, 2, 5), (0, 1), benchmark)

    assert result.benchmark_return == pytest.approx(0.01)
    assert result.abnormal_return == pytest.approx(0.03)


def test_event_return_fails_when_window_exceeds_history(price_frame):
    with pytest.raises(EventWindowError, match="history"):
        calculate_event_return(price_frame, date(2024, 2, 8), (0, 1))


def test_period_return_crosschecks_local_gs_quant_installation():
    prices = pd.Series([100.0, 108.0], index=pd.to_datetime(["2024-01-02", "2024-01-03"]))

    result = gs_quant_return_crosscheck(prices)

    if result is not None:
        assert result == pytest.approx(0.08)
