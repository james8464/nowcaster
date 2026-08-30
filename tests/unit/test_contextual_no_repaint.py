from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.config.settings import Settings
from src.contextual.eligibility import eligibility_inputs_from_bars
from src.contextual.types import StrategyDirection
from src.strategies.types import BarInterval

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _bars() -> pd.DataFrame:
    opened = pd.date_range(datetime(2026, 8, 1, tzinfo=UTC), periods=150, freq="5min")
    close = 100 * np.exp(np.linspace(0, 0.05, len(opened)))
    return pd.DataFrame(
        {
            "provider": "binance",
            "feed": "spot",
            "symbol": "BTCUSDT",
            "interval": BarInterval.FIVE_MINUTES.value,
            "open_timestamp": opened,
            "close_timestamp": opened + pd.Timedelta(minutes=5),
            "available_at": opened + pd.Timedelta(minutes=5, seconds=1),
            "finalized": True,
            "revision": 1,
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": np.linspace(1_000, 2_000, len(opened)),
            "payload_hash": [f"{index:064x}" for index in range(len(opened))],
        }
    )


def test_future_market_rows_cannot_change_prior_eligibility_inputs() -> None:
    settings = Settings.load(PROJECT_ROOT, mode="test")
    instrument = next(item for item in settings.instruments.instruments if item.symbol == "BTCUSDT")
    bars = _bars()
    as_of = pd.Timestamp(bars.iloc[99]["available_at"]).to_pydatetime()
    prefix = eligibility_inputs_from_bars(
        bars.iloc[:100],
        as_of=as_of,
        instrument=instrument,
        interval=BarInterval.FIVE_MINUTES,
        direction=StrategyDirection.LONG,
    )
    changed = bars.copy()
    changed.loc[100:, "volume"] *= 1_000
    changed.loc[100:, "close"] *= 10

    assert (
        eligibility_inputs_from_bars(
            changed,
            as_of=as_of,
            instrument=instrument,
            interval=BarInterval.FIVE_MINUTES,
            direction=StrategyDirection.LONG,
        )
        == prefix
    )


def test_bar_derived_inputs_reject_mixed_instrument_identity() -> None:
    settings = Settings.load(PROJECT_ROOT, mode="test")
    instrument = next(item for item in settings.instruments.instruments if item.symbol == "BTCUSDT")
    bars = _bars().iloc[:100].copy()
    bars.loc[bars.index[0], "symbol"] = "ETHUSDT"
    as_of = pd.Timestamp(bars.iloc[-1]["available_at"]).to_pydatetime()

    with pytest.raises(ValueError, match="exact instrument identity"):
        eligibility_inputs_from_bars(
            bars,
            as_of=as_of,
            instrument=instrument,
            interval=BarInterval.FIVE_MINUTES,
            direction=StrategyDirection.LONG,
        )
