from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.trading.types import BrokerAccount, BrokerOrder, BrokerOrderStatus, BrokerPosition

_OPEN = {
    BrokerOrderStatus.ACCEPTED,
    BrokerOrderStatus.PENDING_NEW,
    BrokerOrderStatus.NEW,
    BrokerOrderStatus.PARTIALLY_FILLED,
    BrokerOrderStatus.PENDING_CANCEL,
    BrokerOrderStatus.PENDING_REPLACE,
    BrokerOrderStatus.STOPPED,
    BrokerOrderStatus.SUSPENDED,
}


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    status: str
    order_mismatches: tuple[str, ...]
    position_mismatches: tuple[str, ...]
    account_mismatches: tuple[str, ...]

    @property
    def unresolved_count(self) -> int:
        return len(self.order_mismatches) + len(self.position_mismatches) + len(self.account_mismatches)


def reconcile_state(
    *,
    local_orders: tuple[BrokerOrder, ...],
    broker_orders: tuple[BrokerOrder, ...],
    local_positions: dict[str, Decimal],
    broker_positions: tuple[BrokerPosition, ...],
    account: BrokerAccount,
) -> ReconciliationResult:
    local_open = {item.client_order_id: item for item in local_orders if item.status in _OPEN}
    remote_open = {item.client_order_id: item for item in broker_orders if item.status in _OPEN}
    order_mismatches: list[str] = []
    for identity in sorted(set(local_open) | set(remote_open)):
        local = local_open.get(identity)
        remote = remote_open.get(identity)
        if local is None:
            order_mismatches.append(f"unexpected_broker_order:{identity}")
        elif remote is None:
            order_mismatches.append(f"missing_broker_order:{identity}")
        elif (local.status, local.filled_quantity, local.quantity) != (
            remote.status,
            remote.filled_quantity,
            remote.quantity,
        ):
            order_mismatches.append(f"order_state_mismatch:{identity}")

    remote_positions = {item.symbol: item.quantity for item in broker_positions}
    position_mismatches = [
        f"position_quantity_mismatch:{symbol}"
        for symbol in sorted(set(local_positions) | set(remote_positions))
        if local_positions.get(symbol, Decimal(0)) != remote_positions.get(symbol, Decimal(0))
    ]
    account_mismatches: list[str] = []
    if account.trading_blocked:
        account_mismatches.append("broker_trading_blocked")
    if account.status.upper() != "ACTIVE":
        account_mismatches.append("broker_account_not_active")
    mismatches = order_mismatches or position_mismatches or account_mismatches
    return ReconciliationResult(
        status="mismatch" if mismatches else "matched",
        order_mismatches=tuple(order_mismatches),
        position_mismatches=tuple(position_mismatches),
        account_mismatches=tuple(account_mismatches),
    )


__all__ = ["ReconciliationResult", "reconcile_state"]
