from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class CostAssumptions:
    maker_fee_bps: float = 0.0
    taker_fee_bps: float = 0.0
    commission_per_unit: float = 0.0
    half_spread_bps: float = 0.0
    slippage_bps: float = 0.0
    funding_bps_per_period: float = 0.0
    borrow_bps_per_period: float = 0.0

    def __post_init__(self) -> None:
        nonnegative = (
            self.maker_fee_bps,
            self.taker_fee_bps,
            self.commission_per_unit,
            self.half_spread_bps,
            self.slippage_bps,
            self.borrow_bps_per_period,
        )
        if any(value < 0 for value in nonnegative):
            raise ValueError("fees, commission, spread, slippage, and borrow cost cannot be negative")


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
    "TransactionCost",
    "calculate_carry_cost",
    "calculate_transaction_cost",
]
