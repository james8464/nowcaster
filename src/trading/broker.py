from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from src.trading.types import (
    BrokerAccount,
    BrokerAsset,
    BrokerClock,
    BrokerOrder,
    BrokerOrderRequest,
    BrokerPosition,
    TradingEnvironment,
)

OrderQueryStatus = Literal["open", "closed", "all"]


@runtime_checkable
class BrokerClient(Protocol):
    @property
    def environment(self) -> TradingEnvironment: ...

    def get_account(self) -> BrokerAccount: ...

    def get_clock(self) -> BrokerClock: ...

    def get_asset(self, symbol: str) -> BrokerAsset: ...

    def list_orders(self, *, status: OrderQueryStatus = "open") -> tuple[BrokerOrder, ...]: ...

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrder: ...

    def list_positions(self) -> tuple[BrokerPosition, ...]: ...

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder: ...

    def cancel_order(self, broker_order_id: str) -> BrokerOrder: ...

    def cancel_all_orders(self) -> int: ...


__all__ = ["BrokerClient", "OrderQueryStatus"]
