from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from src.strategies.types import canonical_hash
from src.trading.broker import OrderQueryStatus
from src.trading.types import (
    BrokerAccount,
    BrokerAsset,
    BrokerClock,
    BrokerOrder,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerPosition,
    TradingEnvironment,
)

_OPEN_STATUSES = {
    BrokerOrderStatus.ACCEPTED,
    BrokerOrderStatus.PENDING_NEW,
    BrokerOrderStatus.NEW,
    BrokerOrderStatus.PARTIALLY_FILLED,
    BrokerOrderStatus.PENDING_CANCEL,
    BrokerOrderStatus.PENDING_REPLACE,
    BrokerOrderStatus.STOPPED,
    BrokerOrderStatus.SUSPENDED,
}


class ShadowBrokerClient:
    """Deterministic broker-shaped state that never creates an external effect or fill."""

    def __init__(
        self,
        *,
        account: BrokerAccount,
        clock: BrokerClock,
        assets: Iterable[BrokerAsset] = (),
        positions: Iterable[BrokerPosition] = (),
        now: Callable[[], datetime] | None = None,
    ):
        self._account = account
        self._clock = clock
        self._assets = {asset.symbol: asset for asset in assets}
        self._positions = {position.symbol: position for position in positions}
        self._orders: dict[str, BrokerOrder] = {}
        self._request_hashes: dict[str, str] = {}
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def environment(self) -> TradingEnvironment:
        return TradingEnvironment.SHADOW

    def get_account(self) -> BrokerAccount:
        return self._account

    def get_clock(self) -> BrokerClock:
        return self._clock

    def get_asset(self, symbol: str) -> BrokerAsset:
        return self._assets[symbol.strip().upper()]

    def list_orders(self, *, status: OrderQueryStatus = "open") -> tuple[BrokerOrder, ...]:
        orders = sorted(self._orders.values(), key=lambda item: item.client_order_id)
        if status == "open":
            orders = [order for order in orders if order.status in _OPEN_STATUSES]
        elif status == "closed":
            orders = [order for order in orders if order.status not in _OPEN_STATUSES]
        elif status != "all":
            raise ValueError(f"unsupported order query status: {status}")
        return tuple(orders)

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrder:
        return self._orders[client_order_id]

    def list_positions(self) -> tuple[BrokerPosition, ...]:
        return tuple(sorted(self._positions.values(), key=lambda item: item.symbol))

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        request_hash = canonical_hash(request.model_dump(mode="json"))
        existing = self._orders.get(request.client_order_id)
        if existing is not None:
            if self._request_hashes[request.client_order_id] != request_hash:
                raise ValueError("conflicting shadow submission for one client order ID")
            return existing
        now = self._now()
        order = BrokerOrder(
            broker_order_id="shadow-" + request.client_order_id,
            client_order_id=request.client_order_id,
            environment=self.environment,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            filled_quantity=0,
            order_type=request.order_type,
            time_in_force=request.time_in_force,
            limit_price=request.limit_price,
            filled_average_price=None,
            status=BrokerOrderStatus.ACCEPTED,
            submitted_at=now,
            updated_at=now,
            received_at=now,
        )
        self._orders[request.client_order_id] = order
        self._request_hashes[request.client_order_id] = request_hash
        return order

    def cancel_order(self, broker_order_id: str) -> BrokerOrder:
        order = next(item for item in self._orders.values() if item.broker_order_id == broker_order_id)
        if order.status not in _OPEN_STATUSES:
            return order
        now = self._now()
        canceled = order.model_copy(
            update={
                "status": BrokerOrderStatus.CANCELED,
                "updated_at": now,
                "received_at": now,
            }
        )
        self._orders[order.client_order_id] = canceled
        return canceled

    def cancel_all_orders(self) -> int:
        open_orders = self.list_orders(status="open")
        for order in open_orders:
            self.cancel_order(order.broker_order_id)
        return len(open_orders)


__all__ = ["ShadowBrokerClient"]
