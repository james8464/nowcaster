from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.config.settings import TradingConfig
from src.trading.types import (
    BrokerAccount,
    BrokerAsset,
    BrokerClock,
    BrokerOrder,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerPosition,
    TradeUpdate,
    TradingEnvironment,
)

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


def test_order_request_normalizes_identity_and_serializes_exact_decimals() -> None:
    request = BrokerOrderRequest(
        client_order_id="nc1p-abc12345",
        symbol=" aapl ",
        side="buy",
        quantity="1.2500",
        order_type="limit",
        time_in_force="day",
        limit_price="190.500",
        extended_hours=False,
    )

    assert request.symbol == "AAPL"
    assert request.quantity == Decimal("1.2500")
    assert request.model_dump(mode="json") == {
        "client_order_id": "nc1p-abc12345",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": "1.2500",
        "order_type": "limit",
        "time_in_force": "day",
        "limit_price": "190.500",
        "extended_hours": False,
    }


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "0", "-1"])
def test_order_request_rejects_nonfinite_or_nonpositive_quantity(value: str) -> None:
    with pytest.raises(ValidationError):
        BrokerOrderRequest(
            client_order_id="nc1p-abc12345",
            symbol="AAPL",
            side="buy",
            quantity=value,
            order_type="limit",
            time_in_force="day",
            limit_price="190.50",
        )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "0", "-1"])
def test_order_request_rejects_nonfinite_or_nonpositive_price(value: str) -> None:
    with pytest.raises(ValidationError):
        BrokerOrderRequest(
            client_order_id="nc1p-abc12345",
            symbol="AAPL",
            side="buy",
            quantity="1",
            order_type="limit",
            time_in_force="day",
            limit_price=value,
        )


def test_broker_models_require_explicit_utc_and_reject_extra_secret_fields() -> None:
    with pytest.raises(ValidationError, match="UTC"):
        BrokerClock(
            timestamp=datetime(2026, 8, 24, 14, 30),
            is_open=True,
            next_open=NOW,
            next_close=NOW,
            received_at=NOW,
        )

    with pytest.raises(ValidationError):
        BrokerAccount(
            account_id="account-uuid",
            account_suffix="uuid",
            status="ACTIVE",
            equity="100000",
            buying_power="200000",
            trading_blocked=False,
            pattern_day_trader=False,
            shorting_enabled=True,
            received_at=NOW,
            api_secret="must-not-be-serializable",
        )


def test_complete_broker_lifecycle_dtos_are_frozen_and_secret_free() -> None:
    account = BrokerAccount(
        account_id="account-uuid",
        account_suffix="uuid",
        status="ACTIVE",
        equity="100000.00",
        buying_power="200000.00",
        trading_blocked=False,
        pattern_day_trader=False,
        shorting_enabled=True,
        received_at=NOW,
    )
    clock = BrokerClock(timestamp=NOW, is_open=True, next_open=NOW, next_close=NOW, received_at=NOW)
    asset = BrokerAsset(
        symbol="AAPL",
        tradable=True,
        shortable=True,
        easy_to_borrow=True,
        fractionable=True,
        received_at=NOW,
    )
    order = BrokerOrder(
        broker_order_id="broker-order",
        client_order_id="nc1p-abc12345",
        environment=TradingEnvironment.PAPER,
        symbol="AAPL",
        side="buy",
        quantity="1",
        filled_quantity="0.5",
        order_type="limit",
        time_in_force="day",
        limit_price="190.50",
        filled_average_price="190.40",
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        submitted_at=NOW,
        updated_at=NOW,
        received_at=NOW,
    )
    position = BrokerPosition(
        symbol="AAPL",
        quantity="1",
        market_value="190.40",
        average_entry_price="190.40",
        current_price="190.40",
        unrealized_pnl="0",
        received_at=NOW,
    )
    update = TradeUpdate(
        event_id="execution-1",
        event="partial_fill",
        known_event=True,
        broker_order_id=order.broker_order_id,
        client_order_id=order.client_order_id,
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        symbol="AAPL",
        side="buy",
        quantity="0.5",
        fill_price="190.40",
        cumulative_filled_quantity="0.5",
        broker_timestamp=NOW,
        received_at=NOW,
        raw_payload_hash="a" * 64,
    )

    payload = "".join(model.model_dump_json() for model in (account, clock, asset, order, position, update)).lower()
    assert "secret" not in payload
    with pytest.raises(ValidationError):
        BrokerOrder.model_validate({**order.model_dump(), "status": "invented"})
    with pytest.raises(ValidationError):
        BrokerPosition.model_validate({**position.model_dump(), "quantity": "NaN"})


def test_trading_configuration_defaults_to_paper_only_and_rejects_live_or_custom_hosts() -> None:
    config = TradingConfig()

    assert config.paper_enabled is True
    assert config.live_enabled is False
    assert config.paper_base_url == "https://paper-api.alpaca.markets"
    assert config.paper_stream_url == "wss://paper-api.alpaca.markets/stream"
    assert config.reconciliation_interval_seconds == 30
    assert config.market_data_stale_after_seconds == 30

    with pytest.raises(ValidationError):
        TradingConfig(live_enabled=True)
    with pytest.raises(ValidationError):
        TradingConfig(paper_base_url="https://example.invalid")
