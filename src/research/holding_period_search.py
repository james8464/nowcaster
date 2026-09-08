"""Bounded, retrospective longer-horizon search for paper-study candidates."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

from src.backtest.opportunities import audit_strategy_opportunities
from src.models.trade_outcomes import BarrierPolicy
from src.research.opportunity_audit import gap_safe_atr
from src.strategies.library import StrategyContext
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, canonical_hash

STRATEGY_IDS = (
    "bollinger_keltner_squeeze",
    "macd_histogram_trend",
    "volatility_scaled_trend",
)
ROUND_TRIP_COST_BPS = 34
STRESSED_ROUND_TRIP_COST_BPS = 68
MINIMUM_TARGET_DISTANCE_BPS = 68


def _summary(outcomes: pd.DataFrame) -> dict[str, Any]:
    if outcomes.empty:
        return {
            "trades": 0,
            "losses": 0,
            "mean_net_return": None,
            "mean_stressed_return": None,
        }
    gross = pd.to_numeric(outcomes["gross_return"], errors="raise")
    net = gross - ROUND_TRIP_COST_BPS / 10_000
    stressed = gross - STRESSED_ROUND_TRIP_COST_BPS / 10_000
    return {
        "trades": len(outcomes),
        "losses": int((stressed < 0).sum()),
        "mean_net_return": float(net.mean()),
        "mean_stressed_return": float(stressed.mean()),
    }


def _folds(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    policy: BarrierPolicy,
    *,
    strategy_id: str,
    family: str,
) -> list[dict[str, Any]]:
    boundaries = [int(len(bars) * index / 4) for index in range(5)]
    summaries: list[dict[str, Any]] = []
    for fold in range(4):
        start = pd.Timestamp(bars.iloc[boundaries[fold]]["open_timestamp"])
        end = (
            pd.Timestamp(bars.iloc[boundaries[fold + 1]]["open_timestamp"])
            if fold < 3
            else pd.Timestamp(bars.iloc[-1]["close_timestamp"])
        )
        fold_signals = signals.copy()
        decisions = pd.to_datetime(fold_signals["decision_timestamp"], utc=True)
        active = (decisions >= start) & (decisions < end)
        fold_signals.loc[~active, "signal"] = 0
        fold_signals.loc[~active, "strength"] = 0.0
        truncated_bars = bars.iloc[: boundaries[fold + 1]].copy() if fold < 3 else bars
        truncated_signals = fold_signals.iloc[: len(truncated_bars)].copy()
        outcomes = audit_strategy_opportunities(
            truncated_bars,
            truncated_signals,
            policy,
            strategy_id=strategy_id,
            family=family,
        ).outcomes
        summaries.append({"fold": fold + 1, "start": start.isoformat(), "end": end.isoformat(), **_summary(outcomes)})
    return summaries


def _rank_value(value: Any) -> float:
    if value is None:
        return -math.inf
    result = float(value)
    return result if math.isfinite(result) else -math.inf


def _select_candidate(configurations: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the deterministic configuration with the strongest worst fold."""

    if not configurations:
        raise ValueError("candidate selection requires at least one configuration")
    return max(
        configurations,
        key=lambda row: (
            min(_rank_value(fold["mean_stressed_return"]) for fold in row["folds"]),
            _rank_value(row["full"]["mean_stressed_return"]),
            str(row["candidate_id"]),
        ),
    )


def _signal_hash(signals: pd.DataFrame, length: int) -> str:
    prefix = signals.iloc[:length]
    return canonical_hash(
        [
            {
                "decision_timestamp": pd.Timestamp(row["decision_timestamp"]).isoformat(),
                "data_through": pd.Timestamp(row["data_through"]).isoformat(),
                "signal": int(row["signal"]),
                "strength": float(row["strength"]),
            }
            for _, row in prefix.iterrows()
        ]
    )


def _generated_signals(bars: pd.DataFrame, strategy_id: str, registry: StrategyRegistry) -> pd.DataFrame:
    items = {item.spec.strategy_id: item for item in registry.enabled()}
    if strategy_id not in items:
        raise ValueError(f"strategy is not registered and enabled: {strategy_id}")
    item = items[strategy_id]
    context = StrategyContext.for_market(
        str(bars.iloc[0].get("provider", "unknown")), str(bars.iloc[0].get("feed", "unknown"))
    )
    opened = pd.to_datetime(bars["open_timestamp"], utc=True)
    previous_close = pd.to_datetime(bars["close_timestamp"], utc=True).shift(1)
    segments = opened.ne(previous_close).cumsum()
    frames = [
        item.generator(item.spec, segment.reset_index(drop=True).copy(), context).reset_index(drop=True)
        for _, segment in bars.groupby(segments, sort=False)
    ]
    signals = pd.concat(frames, ignore_index=True)
    if len(signals) != len(bars):
        raise ValueError("strategy generator must emit one row per bar")
    return signals


def _apply_long_eligibility(
    bars: pd.DataFrame, raw_signals: pd.DataFrame, *, target_atr: float
) -> tuple[pd.DataFrame, int]:
    distance_bps = target_atr * pd.to_numeric(bars["atr"], errors="coerce") / pd.to_numeric(
        bars["close"], errors="raise"
    ) * 10_000
    signals = raw_signals.copy()
    active = pd.to_numeric(signals["signal"], errors="raise").eq(1)
    target_eligible = distance_bps.ge(MINIMUM_TARGET_DISTANCE_BPS).fillna(False)
    excluded = active & ~target_eligible
    signals.loc[~(active & target_eligible), "signal"] = 0
    signals.loc[~(active & target_eligible), "strength"] = 0.0
    return signals, int(excluded.sum())


def eligible_long_signals(
    bars: pd.DataFrame, candidate: Mapping[str, Any], registry: StrategyRegistry
) -> pd.DataFrame:
    """Generate the candidate's causal long signals with the frozen distance floor."""

    ordered = bars.sort_values("open_timestamp", kind="stable").reset_index(drop=True).copy()
    ordered["atr"] = gap_safe_atr(ordered, period=14)
    raw = _generated_signals(ordered, str(candidate["strategy_id"]), registry)
    eligible, _ = _apply_long_eligibility(ordered, raw, target_atr=float(candidate["target_atr"]))
    return eligible


def search_scope(bars: pd.DataFrame, *, symbol: str, registry: StrategyRegistry) -> dict[str, Any]:
    """Evaluate the twelve predeclared hourly configurations for one spot asset."""

    if len(bars) < 4:
        raise ValueError("holding-period search requires at least four bars")
    ordered = bars.sort_values("open_timestamp", kind="stable").reset_index(drop=True).copy()
    one_interval = ordered.get("interval", pd.Series(dtype=str)).astype(str).nunique() == 1
    if not one_interval or str(ordered.iloc[0]["interval"]) != "1h":
        raise ValueError("holding-period search requires one hourly scope")
    if ordered.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().nunique() != 1:
        raise ValueError("holding-period search requires one symbol")
    normalized_symbol = symbol.strip().upper()
    if str(ordered.iloc[0]["symbol"]).upper() != normalized_symbol:
        raise ValueError("requested symbol does not match bars")
    ordered["atr"] = gap_safe_atr(ordered, period=14)

    registered = {item.spec.strategy_id: item for item in registry.enabled()}
    missing = set(STRATEGY_IDS) - set(registered)
    if missing:
        raise ValueError(f"holding-period registry is missing strategies: {sorted(missing)}")
    signals_by_strategy = {
        strategy_id: _generated_signals(ordered, strategy_id, registry)
        for strategy_id in STRATEGY_IDS
    }
    configurations: list[dict[str, Any]] = []
    prefix_length = int(len(ordered) * 0.75)
    for strategy_id in STRATEGY_IDS:
        item = registered[strategy_id]
        raw_signals = signals_by_strategy[strategy_id]
        if len(raw_signals) != len(ordered):
            raise ValueError("strategy generator must emit one row per bar")
        definition_hash = item.spec.definition_hash
        for stop_atr in (1, 2):
            target_atr = 1.5 * stop_atr
            for maximum_bars in (6, 12):
                definition = {
                    "symbol": normalized_symbol,
                    "strategy_id": strategy_id,
                    "strategy_definition_hash": definition_hash,
                    "stop_atr": stop_atr,
                    "target_atr": target_atr,
                    "maximum_bars": maximum_bars,
                }
                candidate_id = canonical_hash(definition)
                signals, excluded = _apply_long_eligibility(ordered, raw_signals, target_atr=target_atr)
                policy = BarrierPolicy(
                    target_r=target_atr,
                    stop_r=stop_atr,
                    maximum_bars=maximum_bars,
                    round_trip_cost_bps=ROUND_TRIP_COST_BPS,
                )
                audit = audit_strategy_opportunities(
                    ordered,
                    signals,
                    policy,
                    strategy_id=strategy_id,
                    family=item.spec.family.value,
                )
                folds = _folds(
                    ordered,
                    signals,
                    policy,
                    strategy_id=strategy_id,
                    family=item.spec.family.value,
                )
                configurations.append(
                    {
                        **definition,
                        "candidate_id": candidate_id,
                        "screen_passed": all(
                            fold["trades"] >= 30
                            and _rank_value(fold["mean_stressed_return"]) > 0
                            for fold in folds
                        ),
                        "folds": folds,
                        "full": _summary(audit.outcomes),
                        "diagnostics": {
                            **audit.diagnostics,
                            "minimum_target_distance_excluded": excluded,
                        },
                        "signal_prefix_hash": _signal_hash(raw_signals, prefix_length),
                    }
                )
    selected = _select_candidate(configurations)
    return {
        "symbol": normalized_symbol,
        "interval": BarInterval.ONE_HOUR.value,
        "configurations": configurations,
        "selected_candidate": selected,
        "promotable": False,
        "status": "experimental_observation_only",
        "assumptions": {
            "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
            "stressed_round_trip_cost_bps": STRESSED_ROUND_TRIP_COST_BPS,
            "minimum_target_distance_bps": MINIMUM_TARGET_DISTANCE_BPS,
            "entry": "next continuous hourly bar open",
            "ambiguous_barrier": "stop_first",
            "expiry": "expiry_before_target",
        },
    }


__all__ = ["eligible_long_signals", "search_scope"]
