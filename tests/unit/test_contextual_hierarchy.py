from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from src.contextual.hierarchy import blend_current_regime, build_hierarchical_estimates
from src.contextual.types import ContextLevel, MarketRegime, StrategyDirection

AS_OF = datetime(2026, 8, 30, 12, tzinfo=UTC)
STRENGTHS = {
    ContextLevel.GLOBAL: 200.0,
    ContextLevel.ASSET_CLASS: 100.0,
    ContextLevel.PROFILE: 75.0,
    ContextLevel.ASSET: 50.0,
    ContextLevel.ASSET_REGIME: 40.0,
}


def _rows(
    count: int,
    *,
    symbol: str,
    direction: StrategyDirection,
    mean: float,
    start: int,
) -> list[dict[str, object]]:
    timestamps = pd.date_range(
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * start),
        periods=count,
        freq="5min",
    )
    oscillation = np.sin(np.arange(count) * 0.7) * 0.002 if count else np.array([])
    rows = []
    for index, (timestamp, net_return) in enumerate(zip(timestamps, mean + oscillation, strict=True)):
        rows.append(
            {
                "outcome_id": f"{direction.value}-{symbol}-{start + index}",
                "dataset_hash": "dataset-v1",
                "protocol_hash": "protocol-v1",
                "provider": "alpaca",
                "feed": "iex",
                "venue": "NASDAQ",
                "product": "equity",
                "asset_class": "equity",
                "profile": "us_liquid_equity",
                "symbol": symbol,
                "interval": "5m",
                "direction": direction.value,
                "mode": "paper",
                "strategy_id": "alpha",
                "decision_timestamp": timestamp,
                "outcome_available_at": timestamp + pd.Timedelta(minutes=5),
                "net_return": float(net_return),
                "regime_trend_normal": 0.55,
                "regime_trend_elevated_volatility": 0.15,
                "regime_range_liquid": 0.25,
                "regime_stressed_or_illiquid": 0.05,
            }
        )
    return rows


def _outcomes(local_count: int, *, local_mean: float) -> pd.DataFrame:
    parent = _rows(2_000, symbol="MSFT", direction=StrategyDirection.LONG, mean=0.0, start=0)
    local = _rows(
        local_count,
        symbol="AAPL",
        direction=StrategyDirection.LONG,
        mean=local_mean,
        start=2_100,
    )
    frame = pd.DataFrame((*parent, *local))
    frame["decision_timestamp"] = pd.to_datetime(frame["decision_timestamp"], utc=True)
    frame["outcome_available_at"] = pd.to_datetime(frame["outcome_available_at"], utc=True)
    return frame


def test_sparse_context_shrinks_to_parent_and_dense_context_moves_local() -> None:
    sparse = build_hierarchical_estimates(_outcomes(2, local_mean=0.02), AS_OF, STRENGTHS)
    dense = build_hierarchical_estimates(_outcomes(500, local_mean=0.02), AS_OF, STRENGTHS)

    assert abs(
        sparse.leaf("alpha", "AAPL").mean_net_edge
        - sparse.parent("alpha", "AAPL").mean_net_edge
    ) < 0.003
    assert dense.leaf("alpha", "AAPL").mean_net_edge > sparse.leaf("alpha", "AAPL").mean_net_edge
    assert dense.leaf("alpha", "AAPL").alpha > sparse.leaf("alpha", "AAPL").alpha


def test_long_outcomes_never_change_short_estimate() -> None:
    short = _rows(250, symbol="AAPL", direction=StrategyDirection.SHORT, mean=-0.001, start=0)
    long = _rows(250, symbol="AAPL", direction=StrategyDirection.LONG, mean=0.001, start=500)
    frame = pd.DataFrame((*short, *long))
    original = build_hierarchical_estimates(frame, AS_OF, STRENGTHS)
    changed = pd.concat(
        [
            frame,
            pd.DataFrame(
                _rows(1_000, symbol="AAPL", direction=StrategyDirection.LONG, mean=0.05, start=1_000)
            ),
        ],
        ignore_index=True,
    )

    assert original.leaf("alpha", "AAPL", "short") == build_hierarchical_estimates(
        changed, AS_OF, STRENGTHS
    ).leaf("alpha", "AAPL", "short")


def test_future_outcome_and_invalid_regime_mass_are_rejected() -> None:
    frame = _outcomes(5, local_mean=0.01)
    frame.loc[frame.index[-1], "outcome_available_at"] = pd.Timestamp(AS_OF) + pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="available by as_of"):
        build_hierarchical_estimates(frame, AS_OF, STRENGTHS)

    frame = _outcomes(5, local_mean=0.01)
    frame.loc[frame.index[-1], "regime_trend_normal"] = 0.90
    with pytest.raises(ValueError, match="sum to one"):
        build_hierarchical_estimates(frame, AS_OF, STRENGTHS)


def test_regime_leafs_remain_separate_from_non_regime_parent() -> None:
    result = build_hierarchical_estimates(_outcomes(100, local_mean=0.01), AS_OF, STRENGTHS)

    parent = result.leaf("alpha", "AAPL")
    regime = result.leaf("alpha", "AAPL", regime=MarketRegime.TREND_NORMAL)
    assert parent.level is ContextLevel.ASSET
    assert regime.level is ContextLevel.ASSET_REGIME
    assert regime.parent_estimate_id == parent.estimate_id

    posterior = {item: 0.0 for item in MarketRegime}
    posterior[MarketRegime.TREND_NORMAL] = 1.0
    blended = blend_current_regime(
        result,
        posterior,
        strategy_id="alpha",
        symbol="AAPL",
    )
    assert blended.mean_net_edge == pytest.approx(regime.mean_net_edge)
    assert blended.lower_net_edge <= blended.mean_net_edge
