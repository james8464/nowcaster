"""Exact, shared-capital accounting for authenticated one-bar strategy outcomes."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

PositionKey = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class PortfolioPeriod:
    gross_return: float
    net_return: float
    source_costs: float
    costs: float
    turnover: float
    gross_exposure: float


def realize_weighted_outcomes(
    weights: Mapping[PositionKey, float],
    outcomes: pd.DataFrame,
    decision: datetime,
    interval: timedelta,
    previous_weights: Mapping[PositionKey, float],
    rebalance_cost_rate: float,
) -> PortfolioPeriod:
    """Weights are account fractions; net directional returns are never sign-flipped again.

    Missing execution evidence invalidates the replay, rather than being silently treated
    as a profitable fill or cash. Source costs and additional allocation turnover costs
    are both charged; this deliberately conservative model may double-count some costs.
    """
    if not math.isfinite(rebalance_cost_rate) or rebalance_cost_rate < 0:
        raise ValueError("rebalancing cost rate must be finite and nonnegative")
    for positions in (weights, previous_weights):
        if (
            any(not math.isfinite(value) or value < 0 for value in positions.values())
            or sum(positions.values()) > 1 + 1e-9
        ):
            raise ValueError("portfolio exposure must be finite, nonnegative and unlevered")
    gross = source_costs = 0.0
    for (symbol, direction, strategy_id), weight in sorted(weights.items()):
        if weight <= 0:
            continue
        selected = outcomes.loc[
            (outcomes["symbol"] == symbol)
            & (outcomes["direction"] == direction)
            & (outcomes["strategy_id"] == strategy_id)
            & (pd.to_datetime(outcomes["decision_timestamp"], utc=True) == decision)
        ]
        if len(selected) != 1:
            raise ValueError(f"one exact resolved outcome is required for {symbol}/{direction}/{strategy_id}")
        row = selected.iloc[0]
        if (
            row.get("holding_horizon_bars", 1) != 1
            or not decision < pd.Timestamp(row["outcome_available_at"]) <= decision + interval
        ):
            raise ValueError("outcome horizon is not a fully resolved, non-overlapping one-bar execution")
        gross_value, cost_value, net_value = (float(row[key]) for key in ("gross_return", "modeled_cost", "net_return"))
        if (
            not all(math.isfinite(value) for value in (gross_value, cost_value, net_value))
            or cost_value < 0
            or net_value <= -1
            or not math.isclose(gross_value - cost_value, net_value, rel_tol=1e-9, abs_tol=1e-12)
        ):
            raise ValueError("outcome return and cost accounting is invalid")
        gross += weight * gross_value
        source_costs += weight * cost_value
    turnover = sum(
        abs(weights.get(key, 0.0) - previous_weights.get(key, 0.0)) for key in set(weights) | set(previous_weights)
    )
    costs = source_costs + turnover * rebalance_cost_rate
    if gross - costs <= -1:
        raise ValueError("portfolio cost stress exhausts account equity")
    return PortfolioPeriod(gross, gross - costs, source_costs, costs, turnover, sum(weights.values()))
