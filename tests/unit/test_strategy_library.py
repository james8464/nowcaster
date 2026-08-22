from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.strategies.library import (
    STRATEGY_GENERATORS,
    STRATEGY_METADATA,
    StrategyContext,
    build_strategy_registry,
    generate_signals,
)
from src.strategies.session import SessionCalendar
from src.strategies.types import StrategyFamily, StrategySpec


def _bars(
    closes: list[float],
    *,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
    start: str = "2026-08-21T13:30:00Z",
    frequency: str = "5min",
    symbol: str = "TEST",
) -> pd.DataFrame:
    opens = pd.to_datetime(pd.date_range(start, periods=len(closes), freq=frequency), utc=True)
    duration = pd.Timedelta(frequency)
    close_times = opens + duration
    return pd.DataFrame(
        {
            "symbol": symbol,
            "open_timestamp": opens,
            "close_timestamp": close_times,
            "available_at": close_times,
            "finalized": True,
            "open": closes,
            "high": highs if highs is not None else [value + 0.5 for value in closes],
            "low": lows if lows is not None else [value - 0.5 for value in closes],
            "close": closes,
            "volume": volumes if volumes is not None else [10.0] * len(closes),
        }
    )


def _spec(strategy_id: str, family: StrategyFamily, parameters: dict[str, float | int]) -> StrategySpec:
    return StrategySpec(
        strategy_id=strategy_id,
        family=family,
        version="test",
        intervals=("5m",),
        warmup_bars=1,
        parameters=parameters,
    )


@dataclass(frozen=True)
class StrategyCase:
    spec: StrategySpec
    bars: pd.DataFrame
    want: list[int]
    context: StrategyContext = StrategyContext()


CASES = [
    StrategyCase(
        _spec(
            "ema_adx_trend",
            StrategyFamily.TREND,
            {"fast_period": 2, "slow_period": 3, "adx_period": 2, "adx_threshold": 0},
        ),
        _bars([1, 2, 3, 4]),
        [0, 0, 1, 1],
    ),
    StrategyCase(
        _spec(
            "macd_histogram_trend",
            StrategyFamily.TREND,
            {"fast_period": 2, "slow_period": 3, "signal_period": 2},
        ),
        _bars([1, 2, 3, 4]),
        [0, 0, 0, 1],
    ),
    StrategyCase(
        _spec("donchian_breakout", StrategyFamily.TREND, {"lookback": 2}),
        _bars([2, 3, 5, 1]),
        [0, 0, 1, -1],
    ),
    StrategyCase(
        _spec("supertrend", StrategyFamily.TREND, {"atr_period": 2, "multiplier": 1.0}),
        _bars([10, 10, 14, 8]),
        [0, 0, 1, -1],
    ),
    StrategyCase(
        _spec("vwap_trend_continuation", StrategyFamily.TREND, {"slope_bars": 1}),
        _bars([10, 11, 12]),
        [0, 1, 1],
    ),
    StrategyCase(
        _spec("rsi_reversal", StrategyFamily.MEAN_REVERSION, {"period": 2, "oversold": 40, "overbought": 60}),
        _bars([3, 2, 1, 2]),
        [0, 0, 1, 0],
    ),
    StrategyCase(
        _spec(
            "connors_rsi",
            StrategyFamily.MEAN_REVERSION,
            {"rsi_period": 2, "streak_period": 2, "rank_period": 3},
        ),
        _bars([1, 2, 4, 8, 16]),
        [0, 0, 0, -1, -1],
    ),
    StrategyCase(
        _spec("bollinger_reversion", StrategyFamily.MEAN_REVERSION, {"period": 3, "deviations": 1.0}),
        _bars([10, 10, 10, 5, 15]),
        [0, 0, 0, 1, -1],
    ),
    StrategyCase(
        _spec("vwap_zscore_reversion", StrategyFamily.MEAN_REVERSION, {"lookback": 3, "entry_zscore": 1.0}),
        _bars([10, 10, 10, 1, 20]),
        [0, 0, 0, 1, -1],
    ),
    StrategyCase(
        _spec("stochastic_reversal", StrategyFamily.MEAN_REVERSION, {"k_period": 3, "d_period": 2}),
        _bars([3, 3, 1, 5.5], highs=[6, 6, 6, 6], lows=[0, 0, 0, 0]),
        [0, 0, 1, -1],
    ),
    StrategyCase(
        _spec("extreme_return_reversal", StrategyFamily.MEAN_REVERSION, {"lookback": 3, "entry_zscore": 1.0}),
        _bars([10, 10, 10, 5, 15]),
        [0, 0, 0, 1, -1],
    ),
    StrategyCase(
        _spec("bollinger_keltner_squeeze", StrategyFamily.VOLATILITY_VOLUME, {"period": 3, "atr_period": 3}),
        _bars([10, 10, 10, 15]),
        [0, 0, 0, 1],
    ),
    StrategyCase(
        _spec(
            "volume_spike_breakout",
            StrategyFamily.VOLATILITY_VOLUME,
            {"volume_lookback": 2, "volume_multiple": 2.0},
        ),
        _bars([10, 10, 12, 8], volumes=[10, 10, 30, 100]),
        [0, 0, 1, -1],
    ),
    StrategyCase(
        _spec(
            "volatility_scaled_trend",
            StrategyFamily.VOLATILITY_VOLUME,
            {"trend_lookback": 2, "volatility_lookback": 2},
        ),
        _bars([10, 9, 12, 8]),
        [0, 0, 1, -1],
    ),
    StrategyCase(
        _spec(
            "opening_range_breakout",
            StrategyFamily.SESSION,
            {"range_minutes": 10, "relative_volume_lookback": 2},
        ),
        _bars([10, 10, 12, 8], volumes=[10, 10, 30, 100]),
        [0, 0, 1, -1],
        StrategyContext(session=SessionCalendar.equity_us()),
    ),
    StrategyCase(
        _spec("etf_last_half_hour_momentum", StrategyFamily.SESSION, {"lookback": 1}),
        _bars([10, 11, 10], start="2026-08-21T19:25:00Z"),
        [0, 1, -1],
        StrategyContext(session=SessionCalendar.equity_us()),
    ),
    StrategyCase(
        _spec("bitcoin_active_session_momentum", StrategyFamily.SESSION, {"lookback": 1}),
        _bars([10, 11, 10], start="2026-08-21T06:00:00Z", frequency="1h", symbol="BTCUSDT"),
        [0, 1, -1],
    ),
    StrategyCase(
        _spec(
            "rolling_cointegration_pairs",
            StrategyFamily.RELATIVE_VALUE,
            {"lookback": 3, "entry_zscore": 1.0},
        ),
        _bars([10, 10, 10, 5, 20], frequency="1h", symbol="PRIMARY"),
        [0, 0, 0, 1, -1],
        StrategyContext(paired_bars=_bars([10, 10, 10, 10, 10], frequency="1h", symbol="PEER")),
    ),
    StrategyCase(
        _spec(
            "crypto_cross_sectional_momentum",
            StrategyFamily.RELATIVE_VALUE,
            {"lookback": 1, "minimum_universe": 5},
        ),
        _bars([10, 12], frequency="1h", symbol="A"),
        [0, 1],
        StrategyContext(
            universe_bars={
                "A": _bars([10, 12], frequency="1h", symbol="A"),
                "B": _bars([10, 11], frequency="1h", symbol="B"),
                "C": _bars([10, 10], frequency="1h", symbol="C"),
                "D": _bars([10, 9], frequency="1h", symbol="D"),
                "E": _bars([10, 8], frequency="1h", symbol="E"),
            }
        ),
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.spec.strategy_id)
def test_each_strategy_has_literal_long_short_or_abstain_decisions(case: StrategyCase):
    result = generate_signals(case.spec, case.bars, case.context)

    assert result["signal"].tolist() == case.want
    assert set(result["signal"]) <= {-1, 0, 1}
    assert result["strength"].between(0, 1).all()


def test_cross_sectional_rule_abstains_with_an_explicit_reason_when_universe_is_too_small():
    spec = _spec(
        "crypto_cross_sectional_momentum",
        StrategyFamily.RELATIVE_VALUE,
        {"lookback": 1, "minimum_universe": 5},
    )
    bars = _bars([10, 12], frequency="1h", symbol="A")

    result = generate_signals(spec, bars, StrategyContext(universe_bars={"A": bars}))

    assert result["signal"].tolist() == [0, 0]
    assert result.iloc[-1]["reason"] == "abstain: cross-sectional universe has 1 of 5 required instruments"


def test_signal_timestamps_distinguish_data_through_from_later_availability():
    bars = _bars([10, 11, 12])
    bars.loc[2, "available_at"] = bars.loc[2, "close_timestamp"] + pd.Timedelta(minutes=2)
    spec = _spec("donchian_breakout", StrategyFamily.TREND, {"lookback": 2})

    result = generate_signals(spec, bars, StrategyContext())

    assert result.iloc[-1]["decision_timestamp"] == bars.iloc[-1]["available_at"]
    assert result.iloc[-1]["data_through"] == bars.iloc[-1]["close_timestamp"]


def test_every_configured_strategy_has_a_static_generator_and_honest_research_metadata():
    path = Path(__file__).parents[2] / "config" / "strategies.yaml"
    configured = [StrategySpec.model_validate(item) for item in yaml.safe_load(path.read_text())["strategies"]]

    registry = build_strategy_registry(configured)
    configured_ids = {spec.strategy_id for spec in configured}

    assert set(STRATEGY_GENERATORS) == configured_ids
    assert set(STRATEGY_METADATA) == configured_ids
    assert {item.spec.strategy_id for item in registry.enabled()} == configured_ids
    for item in registry.enabled():
        assert item.metadata.research_only is True
        assert item.metadata.evidence_strength in {"research_prior", "heuristic"}
        assert item.metadata.description.endswith(".")
        assert "backtest" in item.metadata.evidence_note.lower()
