"""Small, read-only contract between intraday research and the native Trade Desk."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DeskModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class MarketStatus(DeskModel):
    market: Literal["germany40", "us500", "eurusd", "wti"]
    broker_symbol: str | None = None
    product: Literal["cfd", "margin_fx"] | None = None
    eligibility: Literal["unverified", "diagnostic", "paper_eligible", "rejected"]
    reason: str | None = None


class OpportunityStatus(DeskModel):
    market: str
    broker_symbol: str
    strategy_id: str
    direction: Literal["long", "short"]
    decided_at: datetime
    entry_at: datetime
    entry: Decimal = Field(gt=0)
    stop: Decimal = Field(gt=0)
    target: Decimal = Field(gt=0)
    exit_by: datetime
    estimated_roundtrip_cost: Decimal = Field(ge=0)
    explanation: str
    evidence_hash: str
    paper_only: Literal[True] = True

    @model_validator(mode="after")
    def causal_plan(self):
        if not self.decided_at < self.entry_at < self.exit_by:
            raise ValueError("paper opportunity timing is not causal")
        if self.direction == "long" and not self.stop < self.entry < self.target:
            raise ValueError("paper long levels are reversed")
        if self.direction == "short" and not self.target < self.entry < self.stop:
            raise ValueError("paper short levels are reversed")
        if any(
            value.tzinfo is None or value.utcoffset().total_seconds() != 0
            for value in (self.decided_at, self.entry_at, self.exit_by)
        ):
            raise ValueError("paper opportunity timing needs explicit UTC")
        return self


class PositionStatus(DeskModel):
    market: str
    broker_symbol: str
    direction: Literal["long", "short"]
    entry: Decimal = Field(gt=0)
    stop: Decimal = Field(gt=0)
    target: Decimal = Field(gt=0)
    opened_at: datetime
    exit_by: datetime
    paper_only: Literal[True] = True


class DeskStatus(DeskModel):
    schema_version: Literal[1] = 1
    paper_only: Literal[True] = True
    generated_at: datetime
    feed_health: Literal["not_configured", "inventory_verified", "healthy", "stale", "error"]
    evidence_status: Literal["not_supported", "under_observation", "prospectively_supported"]
    markets: tuple[MarketStatus, ...]
    opportunities: tuple[OpportunityStatus, ...]
    paper_positions: tuple[PositionStatus, ...]
    no_trade_reason: str

    @model_validator(mode="after")
    def fail_closed(self):
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset().total_seconds() != 0:
            raise ValueError("status needs UTC time")
        if self.opportunities and self.feed_health != "healthy":
            raise ValueError("cannot publish paper opportunities from an unhealthy feed")
        if self.opportunities and not all(
            any(
                market.market == idea.market and market.eligibility in ("diagnostic", "paper_eligible")
                for market in self.markets
            )
            for idea in self.opportunities
        ):
            raise ValueError("opportunity market is not paper eligible")
        if not self.opportunities and not self.no_trade_reason.strip():
            raise ValueError("no-trade reason required")
        return self

    @classmethod
    def unconfigured(cls, now: datetime) -> DeskStatus:
        return cls(
            generated_at=now,
            feed_health="not_configured",
            evidence_status="not_supported",
            markets=tuple(
                MarketStatus(market=market, eligibility="unverified")
                for market in ("germany40", "us500", "eurusd", "wti")
            ),
            opportunities=(),
            paper_positions=(),
            no_trade_reason="No OANDA practice demo feed has been verified. Stand aside.",
        )
