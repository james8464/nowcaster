from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from src.backtest.execution import DecisionProvenance, OrderIntent
from src.trading.broker import BrokerClient
from src.trading.idempotency import client_order_id
from src.trading.shadow import ShadowBrokerClient
from src.trading.types import (
    BrokerAccount,
    BrokerAsset,
    BrokerClock,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerPosition,
    TradingEnvironment,
)

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


def _intent(*, quantity: float = 1.0) -> OrderIntent:
    return OrderIntent(
        order_id="research-order-1",
        strategy_id="ema_adx_trend",
        symbol="AAPL",
        decision_timestamp=NOW,
        side="buy",
        quantity=quantity,
        order_type="market",
        source_decisions=(
            DecisionProvenance(
                strategy_id="ema_adx_trend",
                symbol="AAPL",
                decision_hash="a" * 64,
                decision_timestamp=NOW,
                signal=1,
                strength=0.75,
            ),
        ),
    )


def _request(identifier: str) -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id=identifier,
        symbol="AAPL",
        side="buy",
        quantity="1",
        order_type="limit",
        time_in_force="day",
        limit_price="190.50",
    )


def _broker() -> ShadowBrokerClient:
    return ShadowBrokerClient(
        account=BrokerAccount(
            account_id="shadow-account",
            account_suffix="adow",
            status="ACTIVE",
            equity="100000",
            buying_power="200000",
            trading_blocked=False,
            pattern_day_trader=False,
            shorting_enabled=True,
            received_at=NOW,
        ),
        clock=BrokerClock(timestamp=NOW, is_open=True, next_open=NOW, next_close=NOW, received_at=NOW),
        assets=(
            BrokerAsset(
                symbol="AAPL",
                tradable=True,
                shortable=True,
                easy_to_borrow=True,
                fractionable=True,
                received_at=NOW,
            ),
        ),
        positions=(
            BrokerPosition(
                symbol="MSFT",
                quantity="2",
                market_value="800",
                average_entry_price="390",
                current_price="400",
                unrealized_pnl="20",
                received_at=NOW,
            ),
        ),
        now=lambda: NOW,
    )


def test_client_order_id_is_stable_bounded_and_contains_no_business_identity() -> None:
    first = client_order_id(_intent(), account_suffix="1234", environment=TradingEnvironment.PAPER)
    second = client_order_id(_intent(), account_suffix="1234", environment=TradingEnvironment.PAPER)

    assert first == second
    assert first.startswith("nc1p-")
    assert len(first) <= 48
    assert "1234" not in first
    assert "aapl" not in first.lower()
    assert "ema" not in first.lower()


def test_client_order_id_changes_for_every_material_effect_field() -> None:
    base = _intent()
    identifier = client_order_id(base, account_suffix="1234", environment=TradingEnvironment.PAPER)

    variants = (
        replace(base, quantity=2),
        replace(base, side="sell"),
        replace(base, decision_timestamp=datetime(2026, 8, 24, 14, 31, tzinfo=UTC)),
        replace(base, source_decisions=()),
    )
    assert all(
        client_order_id(item, account_suffix="1234", environment=TradingEnvironment.PAPER) != identifier
        for item in variants
    )
    assert client_order_id(base, account_suffix="9999", environment=TradingEnvironment.PAPER) != identifier
    assert client_order_id(base, account_suffix="1234", environment=TradingEnvironment.SHADOW) != identifier


def test_shadow_broker_implements_protocol_without_creating_fills() -> None:
    broker = _broker()
    assert isinstance(broker, BrokerClient)
    identifier = client_order_id(_intent(), account_suffix="adow", environment=TradingEnvironment.SHADOW)

    submitted = broker.submit_order(_request(identifier))

    assert submitted.environment == TradingEnvironment.SHADOW
    assert submitted.status == BrokerOrderStatus.ACCEPTED
    assert submitted.filled_quantity == 0
    assert submitted.filled_average_price is None
    assert broker.get_order_by_client_id(identifier) == submitted
    assert broker.list_orders() == (submitted,)
    assert broker.list_positions()[0].symbol == "MSFT"
    assert broker.get_asset("aapl").symbol == "AAPL"
    assert broker.get_account().account_suffix == "adow"
    assert broker.get_clock().timestamp == NOW


def test_shadow_cancel_is_idempotent_and_list_order_is_deterministic() -> None:
    broker = _broker()
    identifiers = [
        client_order_id(
            replace(_intent(), order_id=f"order-{index}", quantity=float(index)),
            account_suffix="adow",
            environment=TradingEnvironment.SHADOW,
        )
        for index in (2, 1)
    ]
    for identifier in identifiers:
        broker.submit_order(_request(identifier))

    canceled = broker.cancel_order(broker.get_order_by_client_id(identifiers[0]).broker_order_id)
    canceled_again = broker.cancel_order(canceled.broker_order_id)

    assert canceled.status == BrokerOrderStatus.CANCELED
    assert canceled_again == canceled
    assert [order.client_order_id for order in broker.list_orders(status="all")] == sorted(identifiers)
    assert broker.cancel_all_orders() == 1
    assert all(order.status == BrokerOrderStatus.CANCELED for order in broker.list_orders(status="all"))
