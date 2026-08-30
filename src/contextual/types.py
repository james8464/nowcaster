"""Immutable identities shared by contextual research components."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum

from src.strategies.types import BarInterval, StrategyMode, canonical_hash


class AssetProfileName(StrEnum):
    US_LIQUID_EQUITY = "us_liquid_equity"
    US_BROAD_ETF = "us_broad_etf"
    CRYPTO_MAJOR_SPOT = "crypto_major_spot"
    CRYPTO_LIQUID_DERIVATIVE = "crypto_liquid_derivative"


class EligibilityState(StrEnum):
    ELIGIBLE = "eligible"
    WATCH = "watch"
    BLOCKED = "blocked"


class MarketRegime(StrEnum):
    TREND_NORMAL = "trend_normal"
    TREND_ELEVATED_VOLATILITY = "trend_elevated_volatility"
    RANGE_LIQUID = "range_liquid"
    STRESSED_OR_ILLIQUID = "stressed_or_illiquid"


class StrategyDirection(StrEnum):
    LONG = "long"
    SHORT = "short"


class ContextLevel(StrEnum):
    GLOBAL = "global"
    ASSET_CLASS = "asset_class"
    PROFILE = "profile"
    ASSET = "asset"
    ASSET_REGIME = "asset_regime"


@dataclass(frozen=True, slots=True)
class StrategyContextKey:
    dataset_hash: str
    protocol_hash: str
    provider: str
    feed: str
    venue: str
    product: str
    asset_class: str
    profile: AssetProfileName
    symbol: str
    interval: BarInterval
    direction: StrategyDirection
    regime: MarketRegime | None
    mode: StrategyMode

    def __post_init__(self) -> None:
        text_fields = (
            "dataset_hash",
            "protocol_hash",
            "provider",
            "feed",
            "venue",
            "product",
            "asset_class",
            "symbol",
        )
        if any(not str(getattr(self, field)).strip() for field in text_fields):
            raise ValueError("context identity fields cannot be blank")

    @property
    def context_hash(self) -> str:
        return canonical_hash(asdict(self))
