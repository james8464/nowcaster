from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.strategies.indicators import (
    adx,
    atr,
    bollinger_bands,
    build_indicators,
    donchian_channels,
    ema,
    keltner_channels,
    macd,
    relative_volume,
    rolling_zscore,
    rsi,
    session_vwap,
    stochastic,
)
from src.strategies.session import SessionCalendar


def _series(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def test_ema_uses_only_the_trailing_recursive_history_and_has_an_explicit_warmup():
    result = ema(_series([1, 2, 3, 4]), period=3)

    assert np.isnan(result.iloc[0])
    assert np.isnan(result.iloc[1])
    assert result.iloc[2] == pytest.approx(2.25)
    assert result.iloc[3] == pytest.approx(3.125)


def test_rsi_uses_wilder_averages_seeded_from_literal_gains_and_losses():
    result = rsi(_series([1, 2, 3, 2, 4]), period=2)

    assert result.iloc[:2].isna().all()
    assert result.iloc[2:].tolist() == pytest.approx([100.0, 50.0, 83.3333333333])


def test_atr_uses_literal_true_ranges_and_wilder_smoothing():
    result = atr(
        _series([10, 12, 14, 13, 15]),
        _series([8, 9, 11, 10, 12]),
        _series([9, 11, 13, 12, 14]),
        period=2,
    )

    assert np.isnan(result.iloc[0])
    assert result.iloc[1:].tolist() == pytest.approx([2.5, 2.75, 2.875, 2.9375])


def test_adx_is_seeded_only_after_two_complete_wilder_windows():
    result = adx(
        _series([10, 12, 14, 13, 15]),
        _series([8, 9, 11, 10, 12]),
        _series([9, 11, 13, 12, 14]),
        period=2,
    )

    assert result.iloc[:2].isna().all()
    assert result.iloc[2:].tolist() == pytest.approx([100.0, 60.0, 64.6153846154])


def test_macd_signal_waits_for_complete_slow_and_signal_warmups():
    line, signal, histogram = macd(_series([1, 2, 3, 4]), fast_period=2, slow_period=3, signal_period=2)

    assert line.iloc[2:].tolist() == pytest.approx([0.3055555556, 0.3935185185])
    assert np.isnan(signal.iloc[2])
    assert signal.iloc[3] == pytest.approx(0.3641975309)
    assert histogram.iloc[3] == pytest.approx(0.0293209877)


def test_stochastic_uses_trailing_extrema_and_trailing_k_average():
    percent_k, percent_d = stochastic(
        _series([3, 4, 5, 6]),
        _series([1, 2, 3, 4]),
        _series([2, 3.5, 4, 5.5]),
        k_period=3,
        d_period=2,
    )

    assert percent_k.iloc[2:].tolist() == pytest.approx([75.0, 87.5])
    assert np.isnan(percent_d.iloc[2])
    assert percent_d.iloc[3] == pytest.approx(81.25)


def test_bollinger_and_keltner_bands_have_literal_trailing_values():
    middle, upper, lower = bollinger_bands(_series([1, 2, 3, 4]), period=3, deviations=2)
    k_middle, k_upper, k_lower = keltner_channels(
        _series([10, 12, 14]),
        _series([8, 9, 11]),
        _series([9, 11, 13]),
        period=2,
        atr_period=2,
        multiplier=2,
    )

    assert middle.iloc[2] == pytest.approx(2.0)
    assert upper.iloc[2] == pytest.approx(3.6329931619)
    assert lower.iloc[2] == pytest.approx(0.3670068381)
    assert k_middle.iloc[1] == pytest.approx(10.3333333333)
    assert k_upper.iloc[1] == pytest.approx(15.3333333333)
    assert k_lower.iloc[1] == pytest.approx(5.3333333333)


def test_donchian_channels_exclude_the_decision_bar_from_breakout_levels():
    upper, lower = donchian_channels(_series([10, 12, 14, 13]), _series([8, 9, 11, 10]), period=2)

    assert upper.iloc[:2].isna().all()
    assert lower.iloc[:2].isna().all()
    assert upper.iloc[2:].tolist() == [12.0, 14.0]
    assert lower.iloc[2:].tolist() == [8.0, 9.0]


def test_session_vwap_resets_at_each_causal_session_boundary():
    timestamps = pd.Series(
        pd.to_datetime(
            [
                "2026-08-21T13:30:00Z",
                "2026-08-21T13:35:00Z",
                "2026-08-22T13:30:00Z",
            ],
            utc=True,
        )
    )
    prices = _series([10, 14, 20])
    calendar = SessionCalendar.equity_us()

    result = session_vwap(prices, prices, prices, _series([1, 3, 2]), timestamps, calendar)

    assert result.tolist() == pytest.approx([10.0, 13.0, 20.0])


def test_relative_volume_compares_current_volume_with_prior_bars_only():
    result = relative_volume(_series([10, 20, 30, 60]), lookback=2)

    assert result.iloc[:2].isna().all()
    assert result.iloc[2:].tolist() == pytest.approx([2.0, 2.4])


def test_rolling_zscore_uses_a_population_standard_deviation_and_no_future_values():
    result = rolling_zscore(_series([1, 2, 3, 100]), lookback=3)

    assert result.iloc[:2].isna().all()
    assert result.iloc[2] == pytest.approx(1.2247448714)


def test_session_windows_are_derived_from_each_timestamp_without_future_rows():
    calendar = SessionCalendar.equity_us()
    timestamps = pd.Series(
        pd.to_datetime(
            [
                "2026-08-21T13:30:00Z",
                "2026-08-21T13:55:00Z",
                "2026-08-21T19:30:00Z",
                "2026-08-21T19:55:00Z",
            ],
            utc=True,
        )
    )

    assert calendar.opening_range(timestamps, minutes=30).tolist() == [True, True, False, False]
    assert calendar.last_window(timestamps, minutes=30).tolist() == [False, False, True, True]


def test_build_indicators_preserves_every_historical_value_when_future_bars_are_appended():
    timestamps = pd.date_range("2026-08-21T13:30:00Z", periods=35, freq="5min")
    closes = pd.Series(np.arange(35, dtype=float) + 100)
    bars = pd.DataFrame(
        {
            "open_timestamp": timestamps,
            "finalized": True,
            "open": closes,
            "high": closes + 1,
            "low": closes - 1,
            "close": closes,
            "volume": np.arange(35, dtype=float) + 10,
        }
    )
    prefix = bars.iloc[:30].copy()
    extended = bars.copy()
    extended.loc[30:, "close"] += 1000
    extended.loc[30:, "high"] = extended.loc[30:, "close"] + 1

    before = build_indicators(prefix, SessionCalendar.equity_us())
    after = build_indicators(extended, SessionCalendar.equity_us()).iloc[:30]

    pd.testing.assert_frame_equal(before, after)
    assert pd.isna(before.iloc[0]["ema_12"])
