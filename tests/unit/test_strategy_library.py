from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
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
from src.strategies.pairs import rolling_cointegration_zscore
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
            "provider": "test-provider",
            "feed": "test-feed",
            "symbol": symbol,
            "interval": frequency,
            "open_timestamp": opens,
            "close_timestamp": close_times,
            "available_at": close_times,
            "revision": 1,
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


def _membership(symbols: list[str], *, available_at: str = "2026-08-20T00:00:00Z") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": symbols,
            "effective_from": pd.to_datetime(["2026-08-20T00:00:00Z"] * len(symbols), utc=True),
            "effective_to": pd.to_datetime([None] * len(symbols), utc=True),
            "available_at": pd.to_datetime([available_at] * len(symbols), utc=True),
            "member": True,
            "liquid": True,
            "eligible": True,
        }
    )


def _configured_spec(strategy_id: str) -> StrategySpec:
    path = Path(__file__).parents[2] / "config" / "strategies.yaml"
    return next(
        StrategySpec.model_validate(item)
        for item in yaml.safe_load(path.read_text())["strategies"]
        if item["strategy_id"] == strategy_id
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
            {"lookback": 4, "entry_zscore": 1.0},
        ),
        _bars(list(np.exp([1, 3, 5, 9])), frequency="1h", symbol="PRIMARY"),
        [0, 0, 0, -1],
        StrategyContext(
            paired_bars=_bars(list(np.exp([0, 1, 2, 3])), frequency="1h", symbol="PEER")
        ),
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
            },
            universe_membership=_membership(["A", "B", "C", "D", "E"]),
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

    result = generate_signals(
        spec,
        bars,
        StrategyContext(universe_bars={"A": bars}, universe_membership=_membership(["A"])),
    )

    assert result["signal"].tolist() == [0, 0]
    assert result.iloc[-1]["reason"] == "abstain: cross-sectional universe has 1 of 5 required instruments"


def test_signal_timestamps_distinguish_data_through_from_later_availability():
    bars = _bars([10, 11, 12])
    bars.loc[2, "available_at"] = bars.loc[2, "close_timestamp"] + pd.Timedelta(minutes=2)
    spec = _spec("donchian_breakout", StrategyFamily.TREND, {"lookback": 2})

    result = generate_signals(spec, bars, StrategyContext())

    assert result.iloc[-1]["decision_timestamp"] == bars.iloc[-1]["available_at"]
    assert result.iloc[-1]["data_through"] == bars.iloc[-1]["close_timestamp"]


@pytest.mark.parametrize(
    "missing",
    ["provider", "feed", "interval", "finalized", "available_at", "revision"],
)
def test_generate_signals_rejects_frames_without_revision_ledger_provenance(missing: str):
    bars = _bars([10, 11, 12]).drop(columns=missing)
    spec = _spec("donchian_breakout", StrategyFamily.TREND, {"lookback": 2})

    with pytest.raises(ValueError, match="revision ledger"):
        generate_signals(spec, bars, StrategyContext())


def test_a_delayed_prior_bar_is_not_used_by_an_earlier_decision():
    bars = _bars([10, 20], highs=[11, 21], lows=[9, 19])
    bars.loc[0, "available_at"] = bars.loc[1, "available_at"] + pd.Timedelta(minutes=1)
    spec = _spec("donchian_breakout", StrategyFamily.TREND, {"lookback": 1})

    result = generate_signals(spec, bars, StrategyContext())

    assert result.iloc[1]["signal"] == 0


@pytest.mark.parametrize(
    ("strategy_id", "context"),
    [
        ("donchian_breakout", StrategyContext()),
        ("volume_spike_breakout", StrategyContext()),
        ("opening_range_breakout", StrategyContext(session=SessionCalendar.equity_us())),
    ],
)
def test_configured_shifted_rules_abstain_until_their_first_valid_indicator_boundary(
    strategy_id: str, context: StrategyContext
):
    bars = _bars(
        [10.0] * 20 + [12.0],
        volumes=[10.0] * 20 + [30.0],
        start="2026-08-21T13:30:00Z",
    )

    result = generate_signals(_configured_spec(strategy_id), bars, context)

    assert result.iloc[19]["signal"] == 0
    assert result.iloc[19]["reason"] == "abstain: rule inputs are not yet valid"
    assert result.iloc[20]["signal"] == 1


def test_configured_volatility_scaled_trend_marks_zero_volatility_as_an_invalid_indicator():
    bars = _bars([10.0] * 21, start="2026-08-21T13:30:00Z", frequency="15min")

    result = generate_signals(
        _configured_spec("volatility_scaled_trend"),
        bars,
        StrategyContext(),
    )

    assert result.iloc[20]["signal"] == 0
    assert result.iloc[20]["reason"] == "abstain: rule inputs are not yet valid"


def test_pairs_abstain_when_auxiliary_bars_lack_point_in_time_provenance():
    bars = _bars([10, 11, 12, 13], frequency="1h", symbol="PRIMARY")
    peer = _bars([8, 9, 10, 11], frequency="1h", symbol="PEER").drop(columns="available_at")
    spec = _spec(
        "rolling_cointegration_pairs",
        StrategyFamily.RELATIVE_VALUE,
        {"lookback": 3, "entry_zscore": 1.0},
    )

    result = generate_signals(spec, bars, StrategyContext(paired_bars=peer))

    assert result["signal"].tolist() == [0, 0, 0, 0]
    assert result.iloc[-1]["reason"] == "abstain: paired instrument point-in-time data is unavailable"


def test_cross_sectional_rule_abstains_without_effective_available_membership_data():
    bars = _bars([10, 12], frequency="1h", symbol="A")
    universe = {
        symbol: _bars([10, close], frequency="1h", symbol=symbol)
        for symbol, close in {"A": 12, "B": 11, "C": 10, "D": 9, "E": 8}.items()
    }
    spec = _spec(
        "crypto_cross_sectional_momentum",
        StrategyFamily.RELATIVE_VALUE,
        {"lookback": 1, "minimum_universe": 5},
    )

    missing = generate_signals(spec, bars, StrategyContext(universe_bars=universe))
    delayed = generate_signals(
        spec,
        bars,
        StrategyContext(
            universe_bars=universe,
            universe_membership=_membership(list(universe), available_at="2026-08-22T00:00:00Z"),
        ),
    )

    assert missing.iloc[-1]["signal"] == 0
    assert delayed.iloc[-1]["signal"] == 0
    assert missing.iloc[-1]["reason"] == "abstain: point-in-time universe membership is unavailable"
    assert delayed.iloc[-1]["reason"] == "abstain: point-in-time universe membership is unavailable"


def test_cross_sectional_rule_requires_point_in_time_membership_and_liquidity_for_every_member():
    bars = _bars([10, 12], frequency="1h", symbol="A")
    universe = {
        symbol: _bars([10, close], frequency="1h", symbol=symbol)
        for symbol, close in {"A": 12, "B": 11, "C": 10, "D": 9, "E": 8}.items()
    }
    membership = _membership(list(universe))
    membership.loc[membership["symbol"] == "E", "liquid"] = False
    spec = _spec(
        "crypto_cross_sectional_momentum",
        StrategyFamily.RELATIVE_VALUE,
        {"lookback": 1, "minimum_universe": 5},
    )

    result = generate_signals(
        spec,
        bars,
        StrategyContext(universe_bars=universe, universe_membership=membership),
    )

    assert result.iloc[-1]["signal"] == 0
    assert result.iloc[-1]["reason"] == "abstain: cross-sectional universe has 4 of 5 required instruments"


def test_pair_residuals_use_the_varying_peer_and_change_the_strategy_decision():
    primary_log = pd.Series([1.0, 3.0, 5.0, 9.0])
    first_peer_log = pd.Series([0.0, 1.0, 2.0, 3.0])
    second_peer_log = pd.Series([0.0, 2.0, 2.0, 4.0])
    first_score = rolling_cointegration_zscore(np.exp(primary_log), np.exp(first_peer_log), lookback=4)
    second_score = rolling_cointegration_zscore(np.exp(primary_log), np.exp(second_peer_log), lookback=4)
    primary = _bars(list(np.exp(primary_log)), frequency="1h", symbol="PRIMARY")
    spec = _spec(
        "rolling_cointegration_pairs",
        StrategyFamily.RELATIVE_VALUE,
        {"lookback": 4, "entry_zscore": 1.0},
    )

    first = generate_signals(
        spec,
        primary,
        StrategyContext(
            paired_bars=_bars(list(np.exp(first_peer_log)), frequency="1h", symbol="PEER")
        ),
    )
    second = generate_signals(
        spec,
        primary,
        StrategyContext(
            paired_bars=_bars(list(np.exp(second_peer_log)), frequency="1h", symbol="PEER")
        ),
    )

    assert first_score.iloc[:3].isna().all()
    assert first_score.iloc[3] == pytest.approx(1.0954451150)
    assert second_score.iloc[3] == pytest.approx(0.5773502692)
    assert first.iloc[-1]["signal"] == -1
    assert second.iloc[-1]["signal"] == 0


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
