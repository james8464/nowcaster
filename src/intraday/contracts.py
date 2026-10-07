"""Contracts for broker-specific intraday observations."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def utc_timestamps(cls, value):
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("timestamps must be explicit UTC")
        return value


class InstrumentSpec(FrozenModel):
    provider: Literal["oanda_practice"]
    broker_symbol: str
    market: Literal["germany40", "us500", "eurusd", "wti"]
    product: Literal["cfd", "margin_fx"]
    quote_currency: str
    point_value: Decimal = Field(gt=0)

    @field_validator("broker_symbol", "quote_currency")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("instrument identity cannot be empty")
        return value.strip()

    @model_validator(mode="after")
    def matching_product(self):
        if (self.market == "eurusd") != (self.product == "margin_fx"):
            raise ValueError("EUR/USD must be identified as margin FX; other candidates are CFDs")
        return self


class ConfirmedBar(FrozenModel):
    instrument: InstrumentSpec
    start: datetime
    end: datetime
    available_at: datetime
    bid_open: Decimal = Field(gt=0)
    bid_high: Decimal = Field(gt=0)
    bid_low: Decimal = Field(gt=0)
    bid_close: Decimal = Field(gt=0)
    ask_open: Decimal = Field(gt=0)
    ask_high: Decimal = Field(gt=0)
    ask_low: Decimal = Field(gt=0)
    ask_close: Decimal = Field(gt=0)
    source_key: str
    price_scope: Literal["historical_base", "account_stream"] = "historical_base"

    @model_validator(mode="after")
    def completed_five_minute_bar(self):
        if self.end - self.start != timedelta(minutes=5) or self.start.minute % 5 or self.start.second:
            raise ValueError("bar must cover one aligned five-minute interval")
        if self.available_at < self.end:
            raise ValueError("bar available before close")
        for side in ("bid", "ask"):
            low, high = getattr(self, f"{side}_low"), getattr(self, f"{side}_high")
            if not low <= getattr(self, f"{side}_open") <= high or not low <= getattr(self, f"{side}_close") <= high:
                raise ValueError("invalid OHLC range")
        for part in ("open", "high", "low", "close"):
            if getattr(self, f"bid_{part}") > getattr(self, f"ask_{part}"):
                raise ValueError("bid exceeds ask in OHLC")
        if not self.source_key.strip():
            raise ValueError("bar source key required")
        return self


class MarketQuote(FrozenModel):
    instrument: InstrumentSpec
    account_feed_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    received_at: datetime
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    status: Literal["tradeable", "non_tradeable"]
    source_key: str

    @model_validator(mode="after")
    def executable_quote(self):
        if self.received_at < self.observed_at:
            raise ValueError("quote received before provider time")
        if self.bid >= self.ask:
            raise ValueError("bid must be below ask")
        if not self.source_key.strip():
            raise ValueError("quote source key required")
        return self

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid
