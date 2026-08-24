from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import ValidationError

from src.trading.broker import OrderQueryStatus
from src.trading.types import (
    BrokerAccount,
    BrokerAsset,
    BrokerClock,
    BrokerOrder,
    BrokerOrderRequest,
    BrokerPosition,
    TradingEnvironment,
)

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
LIVE_BASE_URL = "https://api.alpaca.markets"
_RETRYABLE = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class AlpacaCredentials:
    key_id: str = field(repr=False)
    secret_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.key_id.strip() or not self.secret_key.strip():
            raise ValueError("both Alpaca paper credentials are required")


class AlpacaError(RuntimeError):
    """A bounded, credential-redacted failure at the broker boundary."""

    def __init__(self, message: str, *, ambiguous: bool = False):
        super().__init__(message)
        self.ambiguous = ambiguous


class AlpacaTradingClient:
    """Strict adapter for Alpaca's paper Trading API; it cannot select the live host."""

    def __init__(
        self,
        credentials: AlpacaCredentials,
        *,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_get_attempts: int = 3,
        _environment: TradingEnvironment = TradingEnvironment.PAPER,
    ):
        if max_get_attempts < 1 or max_get_attempts > 5:
            raise ValueError("max_get_attempts must be in [1, 5]")
        self._credentials = credentials
        self._client = client or httpx.Client(timeout=httpx.Timeout(10.0))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._max_get_attempts = max_get_attempts
        if _environment not in {TradingEnvironment.PAPER, TradingEnvironment.LIVE}:
            raise ValueError("Alpaca adapter supports only broker environments")
        self._environment = _environment
        self._base_url = PAPER_BASE_URL if _environment is TradingEnvironment.PAPER else LIVE_BASE_URL

    @property
    def environment(self) -> TradingEnvironment:
        return self._environment

    @property
    def _environment_label(self) -> str:
        return self._environment.value

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._credentials.key_id,
            "APCA-API-SECRET-KEY": self._credentials.secret_key,
            "Accept": "application/json",
        }

    def _redact(self, value: str) -> str:
        bounded = " ".join(value.split())[:240]
        for secret in (self._credentials.key_id, self._credentials.secret_key):
            if secret:
                bounded = bounded.replace(secret, "[REDACTED]")
        return bounded

    def _failure(self, response: httpx.Response) -> AlpacaError:
        request_id = response.headers.get("x-request-id", "unavailable")[:80]
        message = "broker rejected request"
        try:
            payload = response.json()
            if isinstance(payload, Mapping):
                message = str(payload.get("message", message))
        except (ValueError, TypeError):
            pass
        return AlpacaError(
            f"Alpaca {self._environment_label} HTTP {response.status_code}; "
            f"request_id={request_id}; message={self._redact(message)}"
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        ambiguous_post: bool = False,
    ) -> httpx.Response:
        attempts = self._max_get_attempts if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self._client.request(
                    method,
                    self._base_url + path,
                    headers=self._headers,
                    params=params,
                    json=json_body,
                )
            except httpx.HTTPError as exc:
                if method == "GET" and attempt + 1 < attempts:
                    self._sleep(min(0.25 * 2**attempt, 1.0))
                    continue
                label = "ambiguous submission transport failure" if ambiguous_post else "broker transport failure"
                raise AlpacaError(
                    f"Alpaca {self._environment_label} {label}: {type(exc).__name__}",
                    ambiguous=ambiguous_post,
                ) from None
            if response.status_code < 400:
                return response
            if method == "GET" and response.status_code in _RETRYABLE and attempt + 1 < attempts:
                raw_delay = response.headers.get("Retry-After", "0.25")
                try:
                    delay = min(max(float(raw_delay), 0.0), 2.0)
                except ValueError:
                    delay = 0.25
                self._sleep(delay)
                continue
            raise self._failure(response)
        raise AlpacaError(f"Alpaca {self._environment_label} request exhausted bounded retries")

    def _json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            raise AlpacaError(f"Alpaca {self._environment_label} returned malformed JSON") from None

    def _parse_order(self, payload: Mapping[str, Any]) -> BrokerOrder:
        try:
            order_type = payload.get("type", payload.get("order_type"))
            return BrokerOrder(
                broker_order_id=payload["id"],
                client_order_id=payload["client_order_id"],
                environment=self.environment,
                symbol=payload["symbol"],
                side=payload["side"],
                quantity=payload["qty"],
                filled_quantity=payload["filled_qty"],
                order_type=order_type,
                time_in_force=payload["time_in_force"],
                limit_price=payload["limit_price"],
                filled_average_price=payload.get("filled_avg_price"),
                status=payload["status"],
                submitted_at=payload["submitted_at"],
                updated_at=payload["updated_at"],
                received_at=self._clock(),
            )
        except (KeyError, TypeError, ValidationError, ValueError):
            raise AlpacaError("Alpaca paper returned an invalid order response") from None

    def get_account(self) -> BrokerAccount:
        payload = self._json(self._request("GET", "/v2/account"))
        try:
            account_id = str(payload["id"])
            suffix = re.sub(r"[^A-Za-z0-9]", "", account_id)[-4:]
            return BrokerAccount(
                account_id=account_id,
                account_suffix=suffix,
                status=payload["status"],
                equity=payload["equity"],
                buying_power=payload["buying_power"],
                trading_blocked=payload["trading_blocked"],
                pattern_day_trader=payload["pattern_day_trader"],
                shorting_enabled=payload["shorting_enabled"],
                received_at=self._clock(),
            )
        except (KeyError, TypeError, ValidationError, ValueError):
            raise AlpacaError("Alpaca paper returned an invalid account response") from None

    def get_clock(self) -> BrokerClock:
        payload = self._json(self._request("GET", "/v2/clock"))
        try:
            return BrokerClock(**payload, received_at=self._clock())
        except (TypeError, ValidationError):
            raise AlpacaError("Alpaca paper returned an invalid clock response") from None

    def get_asset(self, symbol: str) -> BrokerAsset:
        normalized = symbol.strip().upper()
        payload = self._json(self._request("GET", f"/v2/assets/{normalized}"))
        try:
            return BrokerAsset(
                symbol=payload["symbol"],
                tradable=payload["tradable"],
                shortable=payload["shortable"],
                easy_to_borrow=payload["easy_to_borrow"],
                fractionable=payload["fractionable"],
                received_at=self._clock(),
            )
        except (KeyError, TypeError, ValidationError):
            raise AlpacaError("Alpaca paper returned an invalid asset response") from None

    def list_orders(self, *, status: OrderQueryStatus = "open") -> tuple[BrokerOrder, ...]:
        payload = self._json(self._request("GET", "/v2/orders", params={"status": status, "nested": "false"}))
        if not isinstance(payload, list):
            raise AlpacaError("Alpaca paper returned an invalid orders response")
        return tuple(sorted((self._parse_order(item) for item in payload), key=lambda item: item.client_order_id))

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrder:
        payload = self._json(
            self._request(
                "GET",
                "/v2/orders:by_client_order_id",
                params={"client_order_id": client_order_id},
            )
        )
        return self._parse_order(payload)

    def list_positions(self) -> tuple[BrokerPosition, ...]:
        payload = self._json(self._request("GET", "/v2/positions"))
        if not isinstance(payload, list):
            raise AlpacaError("Alpaca paper returned an invalid positions response")
        try:
            positions = (
                BrokerPosition(
                    symbol=item["symbol"],
                    quantity=item["qty"],
                    market_value=item["market_value"],
                    average_entry_price=item["avg_entry_price"],
                    current_price=item["current_price"],
                    unrealized_pnl=item["unrealized_pl"],
                    received_at=self._clock(),
                )
                for item in payload
            )
            return tuple(sorted(positions, key=lambda item: item.symbol))
        except (KeyError, TypeError, ValidationError):
            raise AlpacaError("Alpaca paper returned an invalid positions response") from None

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        body = {
            "client_order_id": request.client_order_id,
            "symbol": request.symbol,
            "side": request.side,
            "qty": str(request.quantity),
            "type": request.order_type,
            "time_in_force": request.time_in_force,
            "limit_price": str(request.limit_price),
            "extended_hours": request.extended_hours,
        }
        payload = self._json(self._request("POST", "/v2/orders", json_body=body, ambiguous_post=True))
        return self._parse_order(payload)

    def cancel_order(self, broker_order_id: str) -> BrokerOrder:
        self._request("DELETE", f"/v2/orders/{broker_order_id}")
        payload = self._json(self._request("GET", f"/v2/orders/{broker_order_id}"))
        return self._parse_order(payload)

    def cancel_all_orders(self) -> int:
        payload = self._json(self._request("DELETE", "/v2/orders"))
        if not isinstance(payload, list):
            raise AlpacaError("Alpaca paper returned an invalid cancel-all response")
        return sum(1 for item in payload if isinstance(item, Mapping) and int(item.get("status", 500)) < 300)


__all__ = ["AlpacaCredentials", "AlpacaError", "AlpacaTradingClient", "LIVE_BASE_URL", "PAPER_BASE_URL"]
