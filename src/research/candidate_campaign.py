"""Immutable, research-only candidate-market campaign contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.strategies.types import BarInterval, canonical_hash


class CampaignAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    asset_class: Literal["commodity_future", "crypto_spot", "equity"]
    contract_identity: str | None = None
    session_calendar: str | None = None
    roll_policy: str | None = None

    @field_validator("symbol")
    @classmethod
    def normalized_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("asset symbol must not be empty")
        return normalized

    @model_validator(mode="after")
    def require_future_identity_for_wti(self) -> CampaignAsset:
        if self.symbol in {"CL", "WTI"}:
            if not self.contract_identity or not self.session_calendar or not self.roll_policy:
                raise ValueError("WTI requires a futures contract identity, session calendar, and roll policy")
        return self


class CampaignSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    feed: str
    csv_path: str | None = None

    @field_validator("provider", "feed")
    @classmethod
    def non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("campaign source fields must not be empty")
        return normalized


class CandidateCampaignDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_id: str
    asset: CampaignAsset
    strategy_ids: tuple[str, ...] = Field(min_length=1)
    interval: BarInterval
    source: CampaignSource

    @field_validator("campaign_id")
    @classmethod
    def non_empty_campaign_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("campaign ID must not be empty")
        return normalized

    @field_validator("strategy_ids")
    @classmethod
    def unique_strategy_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value if item.strip())
        if not normalized or len(normalized) != len(set(normalized)):
            raise ValueError("campaign strategy IDs must be non-empty and unique")
        return normalized

    @model_validator(mode="after")
    def require_intraday_interval(self) -> CandidateCampaignDefinition:
        if self.interval is BarInterval.ONE_DAY:
            raise ValueError("candidate research requires an intraday interval")
        return self

    @property
    def identity_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))

    @property
    def state_label(self) -> str:
        return "Research only"


class CampaignReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_id: str
    campaign_hash: str
    status: Literal["available", "unavailable", "rejected"]
    reason: str
    receipt_hash: str


__all__ = [
    "CampaignAsset",
    "CampaignReceipt",
    "CampaignSource",
    "CandidateCampaignDefinition",
]
