from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src.strategies.library import (
    StrategyContext,
    audit_prefix_invariance,
    generate_signals,
)
from src.strategies.session import SessionCalendar
from src.strategies.types import StrategySpec


def _configured_specs() -> list[StrategySpec]:
    path = Path(__file__).parents[2] / "config" / "strategies.yaml"
    return [StrategySpec.model_validate(item) for item in yaml.safe_load(path.read_text())["strategies"]]


def _bars(symbol: str, closes: np.ndarray) -> pd.DataFrame:
    opens = pd.date_range("2026-07-01T00:00:00Z", periods=len(closes), freq="1h")
    close_times = opens + pd.Timedelta(hours=1)
    spread = 0.5 + np.abs(np.sin(np.arange(len(closes)) / 11))
    volume = 100 + (np.arange(len(closes)) % 17) * 11
    return pd.DataFrame(
        {
            "symbol": symbol,
            "open_timestamp": opens,
            "close_timestamp": close_times,
            "available_at": close_times + pd.Timedelta(seconds=5),
            "revision": 1,
            "finalized": True,
            "open": closes - 0.1,
            "high": closes + spread,
            "low": closes - spread,
            "close": closes,
            "volume": volume.astype(float),
        }
    )


def _contexts(primary: pd.DataFrame, cutoff: int) -> tuple[StrategyContext, StrategyContext]:
    peer = _bars("PEER", 80 + np.arange(len(primary)) * 0.04 + np.sin(np.arange(len(primary)) / 8))
    universe = {
        "PRIMARY": primary,
        "B": _bars("B", 90 + np.arange(len(primary)) * 0.06),
        "C": _bars("C", 70 + np.arange(len(primary)) * 0.02),
        "D": _bars("D", 110 - np.arange(len(primary)) * 0.01),
        "E": _bars("E", 60 + np.cos(np.arange(len(primary)) / 6)),
    }
    calendar = SessionCalendar.equity_us()
    before = StrategyContext(
        session=calendar,
        paired_bars=peer.iloc[:cutoff].copy(),
        universe_bars={symbol: frame.iloc[:cutoff].copy() for symbol, frame in universe.items()},
    )
    after = StrategyContext(session=calendar, paired_bars=peer, universe_bars=universe)
    return before, after


def test_prefix_audit_catches_an_intentionally_future_dependent_generator():
    spec = next(spec for spec in _configured_specs() if spec.strategy_id == "donchian_breakout")
    bars = _bars("PRIMARY", np.array([100.0, 99.0, 98.0, 120.0]))

    def future_dependent(
        candidate: StrategySpec, frame: pd.DataFrame, context: StrategyContext
    ) -> pd.DataFrame:
        result = generate_signals(candidate, frame, context)
        result["signal"] = 1 if frame.iloc[-1]["close"] > frame.iloc[0]["close"] else -1
        return result

    audit = audit_prefix_invariance(
        spec,
        bars.iloc[:3].copy(),
        bars,
        StrategyContext(),
        StrategyContext(),
        generator=future_dependent,
    )

    assert audit.passed is False
    assert audit.mismatch_column == "signal"
    assert audit.mismatch_row == 0


@pytest.mark.parametrize("spec", _configured_specs(), ids=lambda spec: spec.strategy_id)
def test_every_registered_strategy_is_unchanged_when_future_bars_and_revisions_are_appended(spec: StrategySpec):
    count = 150
    cutoff = 135
    positions = np.arange(count)
    closes = 100 + positions * 0.05 + np.sin(positions / 5) * 2
    extended = _bars("PRIMARY", closes)
    extended.loc[cutoff:, "revision"] = 2
    extended.loc[cutoff:, "close"] += np.linspace(0, 40, count - cutoff)
    extended.loc[cutoff:, "high"] = np.maximum(
        extended.loc[cutoff:, "high"], extended.loc[cutoff:, "close"] + 0.5
    )
    extended.loc[cutoff:, "low"] = np.minimum(
        extended.loc[cutoff:, "low"], extended.loc[cutoff:, "close"] - 0.5
    )
    before_context, after_context = _contexts(extended, cutoff)

    audit = audit_prefix_invariance(
        spec,
        extended.iloc[:cutoff].copy(),
        extended,
        before_context,
        after_context,
    )

    assert audit.passed, audit.reason
