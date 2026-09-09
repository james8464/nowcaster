"""Decimal arithmetic for independent fixed-rule historical paper accounts."""

from __future__ import annotations

from copy import deepcopy
from decimal import ROUND_DOWN, Decimal
from typing import Any

import pandas as pd

D = Decimal
LOT_INCREMENTS = {"BTCUSDT": D("0.00001"), "ETHUSDT": D("0.0001")}


def json_values(value: Any) -> Any:
    """Detach mutable state and serialize monetary values without float rounding."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: json_values(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_values(item) for item in value]
    return deepcopy(value)


class ReplayAccount:
    """One cash account; a shared external decision stream supplies frozen plans."""

    def __init__(self, symbol: str, multiplier: int, emit):
        self.multiplier = multiplier
        self.lot = LOT_INCREMENTS[symbol]
        self.fee = D(".001") * multiplier
        self.spread = D(".0002") * multiplier
        self.slippage = D(".0005") * multiplier
        self.cash = self.equity = self.peak = D(10000)
        self.realized = self.fees = self.spread_cost = self.slippage_cost = D(0)
        self.maximum_drawdown = D(0)
        self.position: dict | None = None
        self.pending: dict | None = None
        self.trades: list[dict] = []
        self.equity_curve: list[dict] = []
        self.counts = dict.fromkeys(
            (
                "entries",
                "rejections",
                "completed_trades",
                "wins",
                "losses",
                "breakeven",
                "ambiguous_exits",
                "gap_tainted_exits",
                "gaps",
                "cancellations",
                "busy",
            ),
            0,
        )
        self._emit = emit

    def emit(self, kind: str, timestamp: str, **payload):
        self._emit(kind, timestamp, {"cost_multiplier": self.multiplier, **payload})

    def fill(self, reference: Decimal, *, buy: bool) -> Decimal:
        side = 1 if buy else -1
        return reference * (1 + side * self.spread) * (1 + side * self.slippage)

    def proceeds(self, reference: Decimal) -> Decimal:
        return self.fill(reference, buy=False) * (1 - self.fee)

    def costs(self, reference: Decimal, quantity: Decimal, *, buy: bool) -> dict:
        side = 1 if buy else -1
        return {
            "fees": quantity * self.fill(reference, buy=buy) * self.fee,
            "spread_cost": quantity * reference * self.spread,
            "slippage_cost": quantity * reference * (1 + side * self.spread) * self.slippage,
        }

    def add_costs(self, costs: dict):
        self.fees += costs["fees"]
        self.spread_cost += costs["spread_cost"]
        self.slippage_cost += costs["slippage_cost"]

    def gap(self, opening: dict):
        self.counts["gaps"] += 1
        if self.pending is not None:
            self.counts["cancellations"] += 1
            self.emit("cancellation", opening["open_timestamp"], reason="missing_interval", decision=self.pending)
            self.pending = None

    def open(self, opening: dict):
        if self.pending is None:
            return
        decision, self.pending = self.pending, None
        raw = D(opening["open"])
        entry = self.fill(raw, buy=True)
        stop = entry - D(decision["stop_distance"])
        target = entry + D(decision["target_distance"])
        rejection = None
        if D(decision["target_distance"]) / entry * 10000 < 68:
            rejection = "minimum_target_distance"
        elif stop <= 0:
            rejection = "invalid_stop"
        elif pd.Timestamp(opening["open_timestamp"]) >= pd.Timestamp(decision["expires_at"]):
            rejection = "expired_before_entry"
        unit_cost = entry * (1 + self.fee)
        quantity = D(0)
        if rejection is None:
            loss_per_unit = unit_cost - self.proceeds(stop)
            quantity = (min(self.cash * D(".25") / unit_cost, D(25) / loss_per_unit) / self.lot).to_integral_value(
                rounding=ROUND_DOWN
            ) * self.lot
            if quantity <= 0 or quantity * entry < 5:
                rejection = "minimum_notional"
        if rejection is not None:
            self.counts["rejections"] += 1
            self.emit("rejection", opening["open_timestamp"], reason=rejection, decision=decision, opening=opening)
            return
        costs = self.costs(raw, quantity, buy=True)
        debit = quantity * unit_cost
        self.cash -= debit
        self.add_costs(costs)
        self.position = {
            "decision": decision,
            "quantity": quantity,
            "entry_reference": raw,
            "entry_price": entry,
            "entry_cash_flow": -debit,
            "entry_costs": costs,
            "entry_open_timestamp": opening["open_timestamp"],
            "entry_close_timestamp": opening["close_timestamp"],
            "stop": stop,
            "target": target,
            "expires_at": decision["expires_at"],
            "tainted": False,
        }
        self.counts["entries"] += 1
        self.emit("entry", opening["open_timestamp"], **self.position)

    def close(self, bar: dict, *, gap: bool):
        if self.position is None:
            return
        position = self.position
        low, high, opened = (D(bar[key]) for key in ("low", "high", "open"))
        stop_hit, target_hit = low <= position["stop"], high >= position["target"]
        ambiguous = stop_hit and target_hit and not gap
        if gap:
            reason, reference = "gap_liquidation", opened
        elif stop_hit:
            reason, reference = "stop", min(opened, position["stop"])
        elif pd.Timestamp(bar["close_timestamp"]) >= pd.Timestamp(position["expires_at"]):
            reason, reference = "expiry", D(bar["close"])
        elif target_hit:
            reason, reference = "target", position["target"]
        else:
            return
        quantity = position["quantity"]
        costs = self.costs(reference, quantity, buy=False)
        credit = quantity * self.proceeds(reference)
        pnl = credit + position["entry_cash_flow"]
        self.cash += credit
        self.realized += pnl
        self.add_costs(costs)
        trade = {
            **position,
            "exit_reason": reason,
            "exit_reference": reference,
            "exit_price": self.fill(reference, buy=False),
            "exit_cash_flow": credit,
            "exit_costs": costs,
            "net_pnl": pnl,
            "exit_open_timestamp": bar["open_timestamp"],
            "exit_close_timestamp": bar["close_timestamp"],
            "outcome_available_at": bar["close_timestamp"],
            "ambiguous": ambiguous,
            "tainted": gap,
            **{key: position["entry_costs"][key] + costs[key] for key in costs},
        }
        self.trades.append(trade)
        self.position = None
        self.counts["completed_trades"] += 1
        self.counts["wins" if pnl > 0 else "losses" if pnl < 0 else "breakeven"] += 1
        self.counts["ambiguous_exits"] += int(ambiguous)
        self.counts["gap_tainted_exits"] += int(gap)
        self.emit("exit", bar["close_timestamp"], **trade)

    def decide(self, decision: dict):
        if not decision["eligible"]:
            return
        if self.position is not None or self.pending is not None:
            self.counts["busy"] += 1
            self.emit("rejection", decision["at"], reason="busy", decision_id=decision["decision_id"])
            return
        self.pending = deepcopy(decision)
        self.emit("pending_entry", decision["at"], decision=self.pending)

    def mark(self, bar: dict):
        liquidation = D(0) if self.position is None else self.position["quantity"] * self.proceeds(D(bar["close"]))
        self.equity = self.cash + liquidation
        self.peak = max(self.peak, self.equity)
        drawdown = (self.peak - self.equity) / self.peak
        self.maximum_drawdown = max(self.maximum_drawdown, drawdown)
        row = {
            "at": bar["close_timestamp"],
            "cash": self.cash,
            "equity": self.equity,
            "marked_liquidation_value": liquidation,
            "realized_pnl": self.realized,
            "net_pnl": self.equity - 10000,
            "drawdown": drawdown,
        }
        self.equity_curve.append(row)
        self.emit("valuation", bar["close_timestamp"], **row)

    def result(self) -> dict:
        return json_values(
            {
                "cost_multiplier": self.multiplier,
                "initial_cash": D(10000),
                "cash": self.cash,
                "marked_equity": self.equity,
                "realized_pnl": self.realized,
                "net_pnl": self.equity - 10000,
                "marked_liquidation_value": self.equity - self.cash,
                "fees": self.fees,
                "spread_cost": self.spread_cost,
                "slippage_cost": self.slippage_cost,
                "maximum_drawdown": self.maximum_drawdown,
                "trades": self.trades,
                "open_position": self.position,
                "pending_entry": self.pending,
                "counts": self.counts,
                "equity_curve": self.equity_curve,
            }
        )
