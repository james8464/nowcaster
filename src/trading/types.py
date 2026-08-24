from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TradingEnvironment(StrEnum):
    SHADOW = "shadow"
    PAPER = "paper"
    LIVE = "live"


class BrokerOrderStatus(StrEnum):
    ACCEPTED = "accepted"
    PENDING_NEW = "pending_new"
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    DONE_FOR_DAY = "done_for_day"
    PENDING_CANCEL = "pending_cancel"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REPLACED = "replaced"
    PENDING_REPLACE = "pending_replace"
    STOPPED = "stopped"
    REJECTED = "rejected"
    SUSPENDED = "suspended"
    CALCULATED = "calculated"


class TradingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def require_finite_values_and_utc_instants(self) -> TradingModel:
        def validate(value: Any, path: str) -> None:
            if isinstance(value, datetime):
                if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
                    raise ValueError(f"{path} must be an explicit UTC datetime")
            elif isinstance(value, Decimal):
                if not value.is_finite():
                    raise ValueError(f"{path} must be finite")
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{path} must be finite")
            elif isinstance(value, dict):
                for key, item in value.items():
                    validate(item, f"{path}.{key}")
            elif isinstance(value, (list, tuple)):
                for index, item in enumerate(value):
                    validate(item, f"{path}[{index}]")

        for name in type(self).model_fields:
            validate(getattr(self, name), name)
        return self


class BrokerAccount(TradingModel):
    account_id: str = Field(min_length=1, max_length=128)
    account_suffix: str = Field(min_length=4, max_length=12)
    status: str = Field(min_length=1, max_length=64)
    equity: Decimal = Field(ge=0)
    buying_power: Decimal = Field(ge=0)
    trading_blocked: bool
    pattern_day_trader: bool
    shorting_enabled: bool
    received_at: datetime

    @field_validator("account_id", "account_suffix", "status")
    @classmethod
    def strip_identity(cls, value: str) -> str:
        return value.strip()


class BrokerClock(TradingModel):
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime
    received_at: datetime


class BrokerAsset(TradingModel):
    symbol: str = Field(min_length=1, max_length=32)
    tradable: bool
    shortable: bool
    easy_to_borrow: bool
    fractionable: bool
    received_at: datetime

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class BrokerOrderRequest(TradingModel):
    client_order_id: str = Field(min_length=8, max_length=48)
    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["buy", "sell"]
    quantity: Decimal = Field(gt=0)
    order_type: Literal["limit"]
    time_in_force: Literal["day", "gtc", "ioc"]
    limit_price: Decimal = Field(gt=0)
    extended_hours: bool = False

    @field_validator("client_order_id")
    @classmethod
    def strip_client_order_id(cls, value: str) -> str:
        return value.strip()

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class BrokerOrder(TradingModel):
    broker_order_id: str = Field(min_length=1, max_length=128)
    client_order_id: str = Field(min_length=8, max_length=48)
    environment: TradingEnvironment
    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["buy", "sell"]
    quantity: Decimal = Field(gt=0)
    filled_quantity: Decimal = Field(ge=0)
    order_type: Literal["limit"]
    time_in_force: Literal["day", "gtc", "ioc"]
    limit_price: Decimal = Field(gt=0)
    filled_average_price: Decimal | None = Field(default=None, gt=0)
    status: BrokerOrderStatus
    submitted_at: datetime
    updated_at: datetime
    received_at: datetime

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def filled_quantity_not_above_requested(self) -> BrokerOrder:
        if self.filled_quantity > self.quantity:
            raise ValueError("filled quantity cannot exceed requested quantity")
        return self


class BrokerPosition(TradingModel):
    symbol: str = Field(min_length=1, max_length=32)
    quantity: Decimal
    market_value: Decimal
    average_entry_price: Decimal = Field(gt=0)
    current_price: Decimal = Field(gt=0)
    unrealized_pnl: Decimal
    received_at: datetime

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class TradeUpdate(TradingModel):
    event_id: str | None = Field(default=None, max_length=128)
    event: str = Field(min_length=1, max_length=64)
    known_event: bool
    broker_order_id: str = Field(min_length=1, max_length=128)
    client_order_id: str = Field(min_length=8, max_length=48)
    status: BrokerOrderStatus
    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["buy", "sell"]
    quantity: Decimal = Field(ge=0)
    fill_price: Decimal | None = Field(default=None, gt=0)
    cumulative_filled_quantity: Decimal = Field(ge=0)
    broker_timestamp: datetime
    received_at: datetime
    raw_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()
