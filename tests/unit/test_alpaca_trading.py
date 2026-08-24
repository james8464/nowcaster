from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.trading.alpaca import AlpacaCredentials, AlpacaError, AlpacaTradingClient
from src.trading.types import BrokerOrderRequest, BrokerOrderStatus, TradingEnvironment

FIXTURES = Path(__file__).parents[1] / "fixtures" / "trading"
NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def _request() -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id="nc1p-abcdef1234",
        symbol="AAPL",
        side="buy",
        quantity="1.25",
        order_type="limit",
        time_in_force="day",
        limit_price="190.50",
    )


def _client(handler, *, sleep=lambda _: None) -> AlpacaTradingClient:
    transport = httpx.MockTransport(handler)
    return AlpacaTradingClient(
        AlpacaCredentials(key_id="paper-key", secret_key="super-secret"),
        client=httpx.Client(transport=transport),
        clock=lambda: NOW,
        sleep=sleep,
    )


def test_submit_uses_fixed_paper_host_headers_and_exact_decimal_strings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://paper-api.alpaca.markets/v2/orders"
        assert request.headers["APCA-API-KEY-ID"] == "paper-key"
        assert request.headers["APCA-API-SECRET-KEY"] == "super-secret"
        body = json.loads(request.content)
        assert body == {
            "client_order_id": "nc1p-abcdef1234",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "1.25",
            "type": "limit",
            "time_in_force": "day",
            "limit_price": "190.50",
            "extended_hours": False,
        }
        return httpx.Response(200, json=_fixture("alpaca_order.json"))

    order = _client(handler).submit_order(_request())
    assert order.environment is TradingEnvironment.PAPER
    assert order.status is BrokerOrderStatus.ACCEPTED
    assert str(order.limit_price) == "190.50"


def test_complete_read_and_cancel_surface_parses_official_shapes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/account":
            return httpx.Response(200, json=_fixture("alpaca_account.json"))
        if path == "/v2/clock":
            return httpx.Response(200, json=_fixture("alpaca_clock.json"))
        if path == "/v2/assets/AAPL":
            return httpx.Response(200, json=_fixture("alpaca_asset.json"))
        if path == "/v2/positions":
            return httpx.Response(200, json=_fixture("alpaca_positions.json"))
        if path == "/v2/orders:by_client_order_id":
            assert request.url.params["client_order_id"] == "nc1p-abcdef1234"
            return httpx.Response(200, json=_fixture("alpaca_order.json"))
        if path == "/v2/orders" and request.method == "DELETE":
            return httpx.Response(207, json=[{"id": "order-uuid", "status": 200}])
        if path == "/v2/orders":
            assert request.url.params["status"] == "all"
            return httpx.Response(200, json=[_fixture("alpaca_order.json")])
        if path == "/v2/orders/order-uuid" and request.method == "DELETE":
            return httpx.Response(204)
        if path == "/v2/orders/order-uuid":
            return httpx.Response(200, json={**_fixture("alpaca_order.json"), "status": "canceled"})
        raise AssertionError((request.method, path))

    client = _client(handler)
    assert client.get_account().account_suffix == "3456"
    assert client.get_clock().is_open
    assert client.get_asset("aapl").easy_to_borrow
    assert client.list_positions()[0].symbol == "AAPL"
    assert len(client.list_orders(status="all")) == 1
    assert client.get_order_by_client_id("nc1p-abcdef1234").broker_order_id == "order-uuid"
    assert client.cancel_order("order-uuid").status is BrokerOrderStatus.CANCELED
    assert client.cancel_all_orders() == 1


@pytest.mark.parametrize("status", [403, 422])
def test_broker_errors_are_bounded_and_redact_credentials(status: int) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            headers={"x-request-id": "req-123"},
            json={"message": "super-secret rejected " + "x" * 1000},
        )

    with pytest.raises(AlpacaError) as captured:
        _client(handler).get_account()
    rendered = str(captured.value)
    assert f"HTTP {status}" in rendered and "req-123" in rendered
    assert "super-secret" not in rendered and len(rendered) < 500


def test_get_retries_rate_limit_but_submit_never_retries_ambiguous_transport() -> None:
    calls = 0
    sleeps: list[float] = []

    def rate_limited(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0.25"}, json={"message": "slow down"})
        return httpx.Response(200, json=_fixture("alpaca_account.json"))

    assert _client(rate_limited, sleep=sleeps.append).get_account().status == "ACTIVE"
    assert calls == 2 and sleeps == [0.25]

    submit_calls = 0

    def timeout(_: httpx.Request) -> httpx.Response:
        nonlocal submit_calls
        submit_calls += 1
        raise httpx.ReadTimeout("opaque timeout")

    with pytest.raises(AlpacaError, match="ambiguous"):
        _client(timeout).submit_order(_request())
    assert submit_calls == 1


def test_malformed_response_fails_closed_without_payload_dump() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "super-secret"})

    with pytest.raises(AlpacaError) as captured:
        _client(handler).get_account()
    assert "super-secret" not in str(captured.value)
