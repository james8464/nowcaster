from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

import pandas as pd

from src.backtest.costs import CostAssumptions, calculate_transaction_cost

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "stop", "protective_stop", "target", "bracket_exit", "timed_exit"]
PositionEffect = Literal["auto", "open", "close"]
Liquidity = Literal["maker", "taker"]


@dataclass(frozen=True, slots=True)
class OrderIntent:
    order_id: str
    strategy_id: str
    symbol: str
    decision_timestamp: pd.Timestamp
    side: OrderSide
    quantity: float
    order_type: OrderType = "market"
    stop_price: float | None = None
    target_price: float | None = None
    position_effect: PositionEffect = "auto"
    liquidity: Liquidity = "taker"

    def __post_init__(self) -> None:
        timestamp = pd.Timestamp(self.decision_timestamp)
        if timestamp.tzinfo is None:
            raise ValueError("decision_timestamp must be timezone-aware")
        if not self.order_id.strip() or not self.strategy_id.strip() or not self.symbol.strip():
            raise ValueError("order, strategy, and symbol identifiers must not be empty")
        if self.quantity <= 0 or not math.isfinite(self.quantity):
            raise ValueError("order quantity must be finite and positive")
        if self.order_type in {"stop", "protective_stop"} and self.stop_price is None:
            raise ValueError("stop orders require stop_price")
        if self.order_type == "target" and self.target_price is None:
            raise ValueError("target orders require target_price")
        if self.order_type == "bracket_exit" and (self.stop_price is None or self.target_price is None):
            raise ValueError("bracket exits require stop_price and target_price")
        object.__setattr__(self, "decision_timestamp", timestamp.tz_convert("UTC"))
        object.__setattr__(self, "symbol", self.symbol.strip().upper())


@dataclass(frozen=True, slots=True)
class ExecutionAssumptions:
    costs: CostAssumptions = field(default_factory=CostAssumptions)
    latency: timedelta = timedelta(0)
    tick_size: float = 0.01
    lot_size: float = 1.0
    participation_rate: float = 1.0
    short_borrow_available: bool = True
    flatten_at_session_end: bool = False
    session_close: Callable[[Mapping[str, Any]], object | None] | None = None

    def __post_init__(self) -> None:
        if self.latency < timedelta(0):
            raise ValueError("latency cannot be negative")
        if self.tick_size <= 0 or self.lot_size <= 0:
            raise ValueError("tick_size and lot_size must be positive")
        if not 0 < self.participation_rate <= 1:
            raise ValueError("participation_rate must be in (0, 1]")
        if self.flatten_at_session_end and self.session_close is None:
            raise ValueError("session flattening requires an injected session_close callback")


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    strategy_id: str
    symbol: str
    decision_timestamp: pd.Timestamp
    execution_timestamp: pd.Timestamp
    side: OrderSide
    order_type: str
    requested_quantity: float
    quantity: float
    price: float
    notional: float
    fee: float
    commission: float
    spread_cost: float
    slippage_cost: float
    total_cost: float
    status: Literal["filled", "partial"]
    fill_reason: str


@dataclass(frozen=True, slots=True)
class OrderRejection:
    order_id: str
    strategy_id: str
    symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    fills: tuple[Fill, ...]
    rejections: tuple[OrderRejection, ...]
    positions: dict[str, float]


@dataclass(slots=True)
class _PendingOrder:
    order: OrderIntent
    rounded_quantity: float
    saw_eligible_bar: bool = False


def _round_quantity(quantity: float, lot_size: float) -> float:
    lots = math.floor((quantity + 1e-12) / lot_size)
    return float(round(lots * lot_size, 12))


def _round_price(price: float, tick_size: float, side: OrderSide) -> float:
    scaled = price / tick_size
    ticks = math.ceil(scaled - 1e-12) if side == "buy" else math.floor(scaled + 1e-12)
    return float(round(ticks * tick_size, 12))


def _utc_column(frame: pd.DataFrame, name: str) -> pd.Series:
    values = pd.to_datetime(frame[name], utc=True, errors="coerce")
    if values.isna().any():
        raise ValueError(f"bar {name} values must be valid timestamps")
    return values


def _validated_bars(bars: pd.DataFrame) -> pd.DataFrame:
    required = {
        "symbol",
        "open_timestamp",
        "close_timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "finalized",
    }
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"Market bars are missing columns: {sorted(missing)}")
    result = bars.copy()
    result["symbol"] = result["symbol"].astype(str).str.upper()
    result["open_timestamp"] = _utc_column(result, "open_timestamp")
    result["close_timestamp"] = _utc_column(result, "close_timestamp")
    if "available_at" in result:
        result["available_at"] = _utc_column(result, "available_at")
    numeric = ["open", "high", "low", "close", "volume"]
    result[numeric] = result[numeric].apply(pd.to_numeric, errors="coerce")
    if result[numeric].isna().any().any():
        raise ValueError("bar prices and volume must be numeric")
    if (~result["finalized"].astype(bool)).any():
        raise ValueError("execution requires finalized bars")
    if (result["close_timestamp"] <= result["open_timestamp"]).any():
        raise ValueError("bar closes must follow bar opens")
    if (result["volume"] < 0).any():
        raise ValueError("bar volume cannot be negative")
    if "revision" in result:
        revision_contract = {"provider", "feed", "interval", "available_at", "revision"}
        missing_revision_fields = revision_contract - set(result.columns)
        if missing_revision_fields:
            raise ValueError(f"Revision bars are missing columns: {sorted(missing_revision_fields)}")
        if (pd.to_numeric(result["revision"], errors="coerce") <= 0).any():
            raise ValueError("bar revisions must be positive")
        if (result["available_at"] < result["close_timestamp"]).any():
            raise ValueError("finalized bars cannot be available before their close")
        logical_key = ["provider", "feed", "symbol", "interval", "open_timestamp"]
        if result.duplicated(logical_key).any():
            raise ValueError("execution requires one causally resolved revision per logical bar")
    return result.sort_values(["open_timestamp", "symbol"], kind="stable").reset_index(drop=True)


def _trigger_price(order: OrderIntent, bar: pd.Series) -> tuple[float, str] | None:
    opening = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])
    if order.order_type in {"market", "timed_exit"}:
        return opening, "next_actionable_bar"
    stop_touched = False
    target_touched = False
    if order.stop_price is not None:
        stop_touched = high >= order.stop_price if order.side == "buy" else low <= order.stop_price
    if order.target_price is not None:
        target_touched = low <= order.target_price if order.side == "buy" else high >= order.target_price
    if order.order_type == "bracket_exit" and stop_touched and target_touched:
        assert order.stop_price is not None
        stop = max(opening, order.stop_price) if order.side == "buy" else min(opening, order.stop_price)
        return stop, "adverse_stop_before_target"
    if order.order_type in {"stop", "protective_stop", "bracket_exit"} and stop_touched:
        assert order.stop_price is not None
        stop = max(opening, order.stop_price) if order.side == "buy" else min(opening, order.stop_price)
        return stop, "stop_triggered"
    if order.order_type in {"target", "bracket_exit"} and target_touched:
        assert order.target_price is not None
        target = min(opening, order.target_price) if order.side == "buy" else max(opening, order.target_price)
        return target, "target_triggered"
    return None


def _quote_price(raw_price: float, side: OrderSide, bar: pd.Series, assumptions: ExecutionAssumptions) -> float:
    quote_name = "ask_open" if side == "buy" else "bid_open"
    if quote_name in bar and pd.notna(bar[quote_name]) and raw_price == float(bar["open"]):
        return float(bar[quote_name])
    direction = 1 if side == "buy" else -1
    return raw_price * (1 + direction * assumptions.costs.half_spread_bps / 10_000)


def _make_fill(
    order: OrderIntent,
    bar: pd.Series,
    assumptions: ExecutionAssumptions,
    quantity: float,
    raw_price: float,
    reason: str,
    *,
    execution_timestamp: pd.Timestamp | None = None,
    order_type: str | None = None,
) -> Fill:
    quote = _quote_price(raw_price, order.side, bar, assumptions)
    direction = 1 if order.side == "buy" else -1
    slipped = quote * (1 + direction * assumptions.costs.slippage_bps / 10_000)
    price = _round_price(slipped, assumptions.tick_size, order.side)
    notional = quantity * price
    transaction = calculate_transaction_cost(notional, quantity, assumptions.costs, liquidity=order.liquidity)
    reference = raw_price
    spread_cost = max(direction * (quote - reference) * quantity, 0.0)
    slippage_cost = max(direction * (price - quote) * quantity, 0.0)
    total_cost = transaction.total + spread_cost + slippage_cost
    return Fill(
        order_id=order.order_id,
        strategy_id=order.strategy_id,
        symbol=order.symbol,
        decision_timestamp=order.decision_timestamp,
        execution_timestamp=execution_timestamp or pd.Timestamp(bar["open_timestamp"]),
        side=order.side,
        order_type=order_type or order.order_type,
        requested_quantity=order.quantity,
        quantity=quantity,
        price=price,
        notional=notional,
        fee=transaction.fee,
        commission=transaction.commission,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        total_cost=total_cost,
        status="partial" if quantity + 1e-12 < order.quantity else "filled",
        fill_reason=reason,
    )


def _is_session_close(bar: pd.Series, assumptions: ExecutionAssumptions) -> bool:
    if assumptions.session_close is None:
        return False
    declared = assumptions.session_close(bar)
    if declared is None or pd.isna(declared):
        return False
    timestamp = pd.Timestamp(declared)
    if timestamp.tzinfo is None:
        raise ValueError("injected session close timestamps must be timezone-aware")
    return timestamp.tz_convert("UTC") == pd.Timestamp(bar["close_timestamp"])


def _close_limit(order: OrderIntent, position: float) -> tuple[float, str | None]:
    if order.position_effect != "close":
        return float("inf"), None
    reducible = position if order.side == "sell" else -position
    if reducible > 1e-12:
        return reducible, None
    if order.order_type in {"protective_stop", "target", "bracket_exit"}:
        return 0.0, "stale_protective_exit"
    return 0.0, "close_side_does_not_reduce_position"


def _session_rejection(bar: pd.Series, reason: str) -> OrderRejection:
    return OrderRejection(
        order_id=f"session-flatten:{bar['symbol']}:{bar['close_timestamp']}",
        strategy_id="session_flatten",
        symbol=str(bar["symbol"]),
        reason=reason,
    )


def run_execution(
    bars: pd.DataFrame,
    orders: Sequence[OrderIntent],
    assumptions: ExecutionAssumptions,
    *,
    initial_positions: Mapping[str, float] | None = None,
    initial_used_capacity: Mapping[tuple[str, pd.Timestamp], float] | None = None,
) -> ExecutionResult:
    ordered_bars = _validated_bars(bars)
    positions = {str(symbol).upper(): float(quantity) for symbol, quantity in (initial_positions or {}).items()}
    if any(not math.isfinite(quantity) for quantity in positions.values()):
        raise ValueError("initial positions must be finite")
    used_capacity_by_bar = {
        (str(symbol).upper(), pd.Timestamp(timestamp).tz_convert("UTC")): float(quantity)
        for (symbol, timestamp), quantity in (initial_used_capacity or {}).items()
    }
    if any(quantity < 0 or not math.isfinite(quantity) for quantity in used_capacity_by_bar.values()):
        raise ValueError("initial used capacity must be finite and non-negative")
    pending: list[_PendingOrder] = []
    rejections: list[OrderRejection] = []
    for order in sorted(orders, key=lambda item: (item.decision_timestamp, item.order_id)):
        rounded = _round_quantity(order.quantity, assumptions.lot_size)
        if rounded <= 0:
            rejections.append(
                OrderRejection(order.order_id, order.strategy_id, order.symbol, "quantity_below_lot_size")
            )
        elif order.side == "sell" and order.position_effect == "open" and not assumptions.short_borrow_available:
            rejections.append(
                OrderRejection(order.order_id, order.strategy_id, order.symbol, "short_borrow_unavailable")
            )
        else:
            pending.append(_PendingOrder(order, rounded))

    fills: list[Fill] = []
    for _index, bar in ordered_bars.iterrows():
        at_session_close = assumptions.flatten_at_session_end and _is_session_close(bar, assumptions)
        if bool(bar.get("halted", False)) or float(bar["volume"]) <= 0:
            if at_session_close and abs(positions.get(str(bar["symbol"]), 0.0)) > 1e-12:
                rejections.append(_session_rejection(bar, "session_close_bar_not_actionable"))
            continue
        capacity_key = (str(bar["symbol"]), pd.Timestamp(bar["open_timestamp"]))
        used_capacity = used_capacity_by_bar.get(capacity_key, 0.0)
        for item in list(pending):
            order = item.order
            if order.symbol != str(bar["symbol"]):
                continue
            eligible_at = order.decision_timestamp + assumptions.latency
            if pd.Timestamp(bar["open_timestamp"]) < eligible_at:
                continue
            if pd.Timestamp(bar["close_timestamp"]) <= order.decision_timestamp:
                continue
            item.saw_eligible_bar = True
            position = positions.get(order.symbol, 0.0)
            close_limit, close_rejection = _close_limit(order, position)
            if close_rejection is not None:
                rejections.append(OrderRejection(order.order_id, order.strategy_id, order.symbol, close_rejection))
                pending.remove(item)
                continue
            triggered = _trigger_price(order, bar)
            if triggered is None:
                continue
            capacity = _round_quantity(
                max(float(bar["volume"]) * assumptions.participation_rate - used_capacity, 0.0),
                assumptions.lot_size,
            )
            quantity = min(item.rounded_quantity, capacity, close_limit)
            if quantity <= 0:
                continue
            remainder_reason: str | None = None
            if (
                order.side == "sell"
                and order.position_effect != "close"
                and position - quantity < -1e-12
                and not assumptions.short_borrow_available
            ):
                quantity = min(quantity, max(position, 0.0))
                remainder_reason = "short_borrow_unavailable"
                if quantity <= 0:
                    rejections.append(OrderRejection(order.order_id, order.strategy_id, order.symbol, remainder_reason))
                    pending.remove(item)
                    continue
            raw_price, reason = triggered
            fill = _make_fill(order, bar, assumptions, quantity, raw_price, reason)
            fills.append(fill)
            positions[order.symbol] = positions.get(order.symbol, 0.0) + quantity * (1 if order.side == "buy" else -1)
            used_capacity += quantity
            pending.remove(item)
            if remainder_reason is not None:
                rejections.append(OrderRejection(order.order_id, order.strategy_id, order.symbol, remainder_reason))
            elif order.position_effect == "close" and quantity + 1e-12 < item.rounded_quantity:
                rejection_reason = (
                    "close_quantity_exceeds_position"
                    if close_limit <= capacity
                    else "close_quantity_exceeds_position_capacity"
                )
                rejections.append(OrderRejection(order.order_id, order.strategy_id, order.symbol, rejection_reason))

        if at_session_close:
            symbol = str(bar["symbol"])
            quantity = positions.get(symbol, 0.0)
            if abs(quantity) > 1e-12:
                side: OrderSide = "sell" if quantity > 0 else "buy"
                flatten = OrderIntent(
                    order_id=f"session-flatten:{symbol}:{bar['close_timestamp']}",
                    strategy_id="session_flatten",
                    symbol=symbol,
                    decision_timestamp=pd.Timestamp(bar["open_timestamp"]),
                    side=side,
                    quantity=abs(quantity),
                    order_type="timed_exit",
                    position_effect="close",
                )
                capacity = _round_quantity(
                    max(float(bar["volume"]) * assumptions.participation_rate - used_capacity, 0.0),
                    assumptions.lot_size,
                )
                executable = min(abs(quantity), capacity)
                if executable <= 0:
                    rejections.append(_session_rejection(bar, "session_close_bar_not_actionable"))
                    continue
                fill = _make_fill(
                    flatten,
                    bar,
                    assumptions,
                    executable,
                    float(bar["close"]),
                    "scheduled_session_flatten",
                    execution_timestamp=pd.Timestamp(bar["close_timestamp"]),
                    order_type="session_flatten",
                )
                fills.append(fill)
                positions[symbol] += executable * (1 if side == "buy" else -1)
                if executable + 1e-12 < abs(quantity):
                    rejections.append(_session_rejection(bar, "close_quantity_exceeds_position_capacity"))

    for item in pending:
        reason = (
            "trigger_not_reached"
            if item.saw_eligible_bar and item.order.order_type not in {"market", "timed_exit"}
            else "no_actionable_bar_after_latency"
        )
        rejections.append(OrderRejection(item.order.order_id, item.order.strategy_id, item.order.symbol, reason))
    fills.sort(key=lambda item: (item.execution_timestamp, item.order_id))
    rejections.sort(key=lambda item: item.order_id)
    return ExecutionResult(tuple(fills), tuple(rejections), positions)


__all__ = [
    "ExecutionAssumptions",
    "ExecutionResult",
    "Fill",
    "OrderIntent",
    "OrderRejection",
    "run_execution",
]
