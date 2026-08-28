from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from statistics import NormalDist
from typing import Literal

from src.trading.types import ExecutionObservation


@dataclass(frozen=True, slots=True)
class CostAssumptions:
    maker_fee_bps: float = 0.0
    taker_fee_bps: float = 0.0
    commission_per_unit: float = 0.0
    half_spread_bps: float = 0.0
    slippage_bps: float = 0.0
    market_impact_bps: float = 0.0
    funding_bps_per_period: float = 0.0
    borrow_bps_per_period: float = 0.0

    def __post_init__(self) -> None:
        nonnegative = (
            self.maker_fee_bps,
            self.taker_fee_bps,
            self.commission_per_unit,
            self.half_spread_bps,
            self.slippage_bps,
            self.market_impact_bps,
            self.borrow_bps_per_period,
        )
        if any(value < 0 for value in nonnegative):
            raise ValueError("fees, commission, spread, slippage, impact, and borrow cost cannot be negative")


@dataclass(frozen=True, slots=True)
class TransactionCost:
    fee: float
    commission: float

    @property
    def total(self) -> float:
        return self.fee + self.commission


@dataclass(frozen=True, slots=True)
class CarryCost:
    funding: float
    borrow: float

    @property
    def total(self) -> float:
        return self.funding + self.borrow


@dataclass(frozen=True, slots=True)
class ExecutionModelError:
    observation_count: int
    effective_observations: Decimal
    mean_relative_error: Decimal | None
    upper_relative_error: Decimal | None
    mean_absolute_cost_error_bps: Decimal | None
    mean_underestimation_bps: Decimal | None
    conservative_cost_buffer_bps: Decimal | None
    missed_fill_rate: Decimal | None
    mean_fill_fraction: Decimal | None
    latency_p95_ms: Decimal | None
    status: Literal["calibrated", "unavailable"]


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _upper_mean_bound(values: Sequence[float], confidence: float) -> float:
    if not values:
        return math.nan
    mean = statistics.fmean(values)
    if len(values) == 1:
        return mean
    standard_error = statistics.stdev(values) / math.sqrt(len(values))
    return mean + NormalDist().inv_cdf(confidence) * standard_error


def execution_model_error(
    observations: Sequence[ExecutionObservation],
    *,
    confidence: float = 0.95,
    minimum_observations: int = 30,
) -> ExecutionModelError:
    """Estimate conservative simulator error from immutable broker observations."""
    if not 0.5 < confidence < 1:
        raise ValueError("execution confidence must be between 0.5 and 1")
    if minimum_observations <= 0:
        raise ValueError("minimum execution observations must be positive")
    unique: dict[str, ExecutionObservation] = {}
    for observation in observations:
        previous = unique.get(observation.observation_id)
        if previous is not None and previous != observation:
            raise ValueError("conflicting execution observation identity")
        unique[observation.observation_id] = observation
    ordered = tuple(unique[key] for key in sorted(unique))
    weights = [float(item.fill_fraction) for item in ordered]
    weight_sum = sum(weights)
    squared_weight_sum = sum(value * value for value in weights)
    effective = weight_sum * weight_sum / squared_weight_sum if squared_weight_sum else 0.0
    unavailable = len(ordered) < minimum_observations or effective < minimum_observations
    if not ordered:
        return ExecutionModelError(0, Decimal(0), None, None, None, None, None, None, None, None, "unavailable")

    relative_errors: list[float] = []
    absolute_errors: list[float] = []
    underestimation: list[float] = []
    for item in ordered:
        predicted = float(item.predicted_execution_cost_bps)
        realized = float(item.realized_execution_cost_bps)
        if item.missed_fill:
            relative_errors.append(1.0)
            absolute_errors.append(abs(predicted))
            underestimation.append(max(abs(predicted), 0.0))
            continue
        absolute_errors.append(abs(realized - predicted))
        relative_errors.append(abs(realized - predicted) / max(abs(predicted), 0.01))
        underestimation.append(max(realized - predicted, 0.0))

    latency = sorted(float(item.realized_latency_ms) for item in ordered)
    p95_index = max(math.ceil(0.95 * len(latency)) - 1, 0)
    report = ExecutionModelError(
        observation_count=len(ordered),
        effective_observations=_decimal(effective),
        mean_relative_error=_decimal(statistics.fmean(relative_errors)),
        upper_relative_error=_decimal(_upper_mean_bound(relative_errors, confidence)),
        mean_absolute_cost_error_bps=_decimal(statistics.fmean(absolute_errors)),
        mean_underestimation_bps=_decimal(statistics.fmean(underestimation)),
        conservative_cost_buffer_bps=_decimal(_upper_mean_bound(underestimation, confidence)),
        missed_fill_rate=_decimal(sum(item.missed_fill for item in ordered) / len(ordered)),
        mean_fill_fraction=_decimal(statistics.fmean(weights)),
        latency_p95_ms=_decimal(latency[p95_index]),
        status="unavailable" if unavailable else "calibrated",
    )
    return report


def calculate_transaction_cost(
    notional: float,
    quantity: float,
    assumptions: CostAssumptions,
    *,
    liquidity: Literal["maker", "taker"] = "taker",
) -> TransactionCost:
    if notional < 0 or quantity < 0:
        raise ValueError("notional and quantity must be non-negative")
    fee_bps = assumptions.maker_fee_bps if liquidity == "maker" else assumptions.taker_fee_bps
    return TransactionCost(
        fee=notional * fee_bps / 10_000,
        commission=quantity * assumptions.commission_per_unit,
    )


def calculate_carry_cost(
    position_quantity: float,
    price: float,
    assumptions: CostAssumptions,
    *,
    periods: float = 1.0,
) -> CarryCost:
    if price < 0 or periods < 0:
        raise ValueError("price and periods must be non-negative")
    funding = position_quantity * price * assumptions.funding_bps_per_period / 10_000 * periods
    borrow = max(-position_quantity, 0.0) * price * assumptions.borrow_bps_per_period / 10_000 * periods
    return CarryCost(funding=funding, borrow=borrow)


__all__ = [
    "CarryCost",
    "CostAssumptions",
    "ExecutionModelError",
    "TransactionCost",
    "calculate_carry_cost",
    "calculate_transaction_cost",
    "execution_model_error",
]
