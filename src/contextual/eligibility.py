"""Point-in-time asset, direction, and strategy applicability gates."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import reduce
from operator import mul
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.config.settings import InstrumentConfig, ProfilePolicy
from src.contextual.types import AssetProfileName, EligibilityState, StrategyDirection
from src.ingestion.bars import INTERVAL_DURATION
from src.strategies.types import BarInterval, StrategyFamily, StrategySpec, canonical_hash

LiquidityGrade = Literal["observed", "bar_proxy", "missing"]


def _require_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is not UTC:
        raise ValueError(f"{label} must be an explicit UTC datetime")
    return value


class EligibilityInputs(BaseModel):
    """All evidence available at one eligibility decision timestamp."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    feed: str
    venue: str
    product: str
    asset_class: Literal["equity", "crypto"]
    profile: AssetProfileName
    trading_calendar: str
    symbol: str
    interval: BarInterval
    direction: StrategyDirection
    as_of: datetime
    data_through: datetime
    listing_at: datetime
    delisting_at: datetime | None = None
    trading_status: Literal["active", "inactive", "unknown"]
    halted: bool
    session_state: str
    finalized_history_bars: int = Field(ge=0)
    coverage: float = Field(ge=0, le=1)
    sequence_continuous: bool
    correction_pending: bool
    median_notional_volume: float = Field(ge=0)
    spread_bps: float | None = Field(default=None, ge=0)
    depth_notional: float | None = Field(default=None, ge=0)
    estimated_price_impact_bps: float | None = Field(default=None, ge=0)
    participation_rate: float = Field(ge=0)
    last_price: float = Field(gt=0)
    tick_size: float | None = Field(default=None, gt=0)
    lot_size_valid: bool | None = None
    realized_volatility: float = Field(ge=0)
    shortable: bool
    short_mechanism: Literal["none", "borrow", "derivative"]
    funding_applicable: bool
    funding_rate_bps: float | None = None
    borrow_applicable: bool
    borrow_fee_bps: float | None = Field(default=None, ge=0)
    research_provider: str
    research_feed: str
    liquidity_grade: LiquidityGrade
    source_event_watermark: str

    @field_validator(
        "provider",
        "feed",
        "venue",
        "product",
        "trading_calendar",
        "session_state",
        "research_provider",
        "research_feed",
        "source_event_watermark",
    )
    @classmethod
    def nonblank_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("eligibility identity fields cannot be blank")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalized_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol cannot be blank")
        return normalized

    @field_validator("as_of", "data_through", "listing_at", "delisting_at")
    @classmethod
    def utc_timestamps(cls, value: datetime | None, info: object) -> datetime | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "timestamp")
        return _require_utc(value, field_name)

    @field_validator(
        "coverage",
        "median_notional_volume",
        "spread_bps",
        "depth_notional",
        "estimated_price_impact_bps",
        "participation_rate",
        "last_price",
        "tick_size",
        "realized_volatility",
        "funding_rate_bps",
        "borrow_fee_bps",
    )
    @classmethod
    def finite_numeric_evidence(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("eligibility numeric evidence must be finite")
        return value

    @model_validator(mode="after")
    def chronology_and_short_contract(self) -> EligibilityInputs:
        if self.data_through > self.as_of:
            raise ValueError("data_through cannot follow as_of")
        if self.delisting_at is not None and self.delisting_at <= self.listing_at:
            raise ValueError("delisting_at must follow listing_at")
        if self.shortable != (self.short_mechanism != "none"):
            raise ValueError("shortability must match the short mechanism")
        if self.borrow_applicable != (self.short_mechanism == "borrow"):
            raise ValueError("borrow applicability must match the short mechanism")
        if self.funding_applicable and self.short_mechanism != "derivative":
            raise ValueError("funding is valid only for a derivative short mechanism")
        return self

    @property
    def input_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="python"))


@dataclass(frozen=True, slots=True)
class AssetEligibilityEvidence:
    evidence_id: str
    state: EligibilityState
    reasons: tuple[str, ...]
    structural_reasons: tuple[str, ...]
    data_reasons: tuple[str, ...]
    liquidity_reasons: tuple[str, ...]
    quality_score: float
    policy_hash: str
    input_hash: str
    as_of: datetime
    data_through: datetime
    provider: str
    feed: str
    venue: str
    product: str
    asset_class: str
    profile: AssetProfileName
    symbol: str
    interval: BarInterval
    direction: StrategyDirection
    liquidity_grade: LiquidityGrade
    source_event_watermark: str

    @property
    def evidence_hash(self) -> str:
        return canonical_hash(asdict(self))


def _geometric_mean(values: Sequence[float]) -> float:
    bounded = tuple(min(max(float(value), 1e-12), 1.0) for value in values)
    return reduce(mul, bounded, 1.0) ** (1.0 / len(bounded))


def _quality_score(inputs: EligibilityInputs, policy: ProfilePolicy) -> float:
    age_seconds = max((inputs.as_of - inputs.data_through).total_seconds(), 0.0)
    coverage = min(inputs.coverage / max(policy.minimum_coverage, 1e-12), 1.0)
    freshness = max(1.0 - age_seconds / policy.maximum_data_age_seconds, 0.0)
    volume = min(inputs.median_notional_volume / policy.minimum_median_notional_volume, 1.0)
    spread = (
        max(1.0 - inputs.spread_bps / policy.maximum_spread_bps, 0.0)
        if inputs.spread_bps is not None
        else 0.5
    )
    depth = (
        min(inputs.depth_notional / policy.minimum_depth_notional, 1.0)
        if inputs.depth_notional is not None
        else 0.5
    )
    impact = (
        max(1.0 - inputs.estimated_price_impact_bps / policy.maximum_price_impact_bps, 0.0)
        if inputs.estimated_price_impact_bps is not None
        else 0.5
    )
    participation = max(1.0 - inputs.participation_rate / policy.maximum_participation_rate, 0.0)
    return _geometric_mean((coverage, freshness, volume, spread, depth, impact, participation))


def evaluate_asset_eligibility(
    inputs: EligibilityInputs,
    policy: ProfilePolicy,
    policy_hash: str,
) -> AssetEligibilityEvidence:
    """Apply non-overridable gates in a stable order."""

    if not policy_hash.strip():
        raise ValueError("policy_hash cannot be blank")

    structural: list[str] = []
    data: list[str] = []
    hard_liquidity: list[str] = []
    evidence_limits: list[str] = []

    if inputs.asset_class not in policy.asset_classes:
        structural.append("asset_class_not_supported")
    if inputs.product not in policy.products:
        structural.append("product_not_supported")
    if inputs.trading_calendar not in policy.trading_calendars:
        structural.append("calendar_not_supported")
    if inputs.direction not in policy.allowed_directions:
        structural.append("direction_not_supported")
    if inputs.listing_at > inputs.as_of or (
        inputs.delisting_at is not None and inputs.as_of >= inputs.delisting_at
    ):
        structural.append("outside_listing_interval")
    if inputs.trading_status != "active":
        structural.append("instrument_not_active")
    if inputs.halted:
        structural.append("instrument_halted")
    if inputs.session_state in {"closed", "unknown"}:
        structural.append("session_not_executable")
    if (inputs.provider, inputs.feed) != (inputs.research_provider, inputs.research_feed):
        structural.append("research_live_feed_mismatch")
    if inputs.tick_size is None or inputs.last_price <= inputs.tick_size:
        structural.append("price_tick_evidence")
    if inputs.lot_size_valid is not True:
        structural.append("lot_size_evidence")

    if inputs.direction is StrategyDirection.SHORT:
        if not inputs.shortable or inputs.short_mechanism == "none":
            structural.append("short_mechanism_unavailable")
        elif inputs.short_mechanism == "borrow" and (
            not inputs.borrow_applicable or inputs.borrow_fee_bps is None
        ):
            structural.append("borrow_evidence_required")
        elif inputs.short_mechanism == "derivative" and (
            not inputs.funding_applicable or inputs.funding_rate_bps is None
        ):
            structural.append("funding_evidence_required")

    age_seconds = (inputs.as_of - inputs.data_through).total_seconds()
    if inputs.finalized_history_bars < policy.minimum_history_bars:
        data.append("history_minimum")
    if inputs.coverage < policy.minimum_coverage:
        data.append("coverage_minimum")
    if age_seconds > policy.maximum_data_age_seconds:
        data.append("data_stale")
    if not inputs.sequence_continuous:
        data.append("sequence_discontinuity")
    if inputs.correction_pending:
        data.append("correction_pending")

    if inputs.median_notional_volume < policy.minimum_median_notional_volume:
        hard_liquidity.append("notional_volume_minimum")
    if inputs.spread_bps is not None and inputs.spread_bps > policy.maximum_spread_bps:
        hard_liquidity.append("spread_limit")
    if inputs.depth_notional is not None and inputs.depth_notional < policy.minimum_depth_notional:
        hard_liquidity.append("depth_minimum")
    if (
        inputs.estimated_price_impact_bps is not None
        and inputs.estimated_price_impact_bps > policy.maximum_price_impact_bps
    ):
        hard_liquidity.append("price_impact_limit")
    if inputs.participation_rate > policy.maximum_participation_rate:
        hard_liquidity.append("participation_limit")
    if not (
        policy.minimum_realized_volatility
        <= inputs.realized_volatility
        <= policy.maximum_realized_volatility
    ):
        hard_liquidity.append("realized_volatility_range")

    if policy.require_observed_spread and inputs.spread_bps is None:
        evidence_limits.append("observed_spread_required")
    if policy.require_observed_depth and inputs.depth_notional is None:
        evidence_limits.append("observed_depth_required")
    if policy.require_observed_impact and inputs.estimated_price_impact_bps is None:
        evidence_limits.append("observed_impact_required")
    if inputs.liquidity_grade != "observed":
        evidence_limits.append("observed_liquidity_required")

    structural_reasons = tuple(dict.fromkeys(structural))
    data_reasons = tuple(dict.fromkeys(data))
    liquidity_reasons = tuple(dict.fromkeys((*hard_liquidity, *evidence_limits)))
    reasons = tuple(dict.fromkeys((*structural_reasons, *data_reasons, *liquidity_reasons)))
    if structural_reasons or data_reasons or hard_liquidity:
        state = EligibilityState.BLOCKED
    elif evidence_limits:
        state = EligibilityState.WATCH
    else:
        state = EligibilityState.ELIGIBLE
    quality = 0.0 if state is EligibilityState.BLOCKED else _quality_score(inputs, policy)
    identity = {
        "input_hash": inputs.input_hash,
        "policy_hash": policy_hash,
        "state": state,
        "reasons": reasons,
    }
    return AssetEligibilityEvidence(
        evidence_id=canonical_hash(identity),
        state=state,
        reasons=reasons,
        structural_reasons=structural_reasons,
        data_reasons=data_reasons,
        liquidity_reasons=liquidity_reasons,
        quality_score=quality,
        policy_hash=policy_hash,
        input_hash=inputs.input_hash,
        as_of=inputs.as_of,
        data_through=inputs.data_through,
        provider=inputs.provider,
        feed=inputs.feed,
        venue=inputs.venue,
        product=inputs.product,
        asset_class=inputs.asset_class,
        profile=inputs.profile,
        symbol=inputs.symbol,
        interval=inputs.interval,
        direction=inputs.direction,
        liquidity_grade=inputs.liquidity_grade,
        source_event_watermark=inputs.source_event_watermark,
    )


def strategy_is_applicable(
    spec: StrategySpec,
    instrument: InstrumentConfig,
    profile: ProfilePolicy,
    direction: StrategyDirection,
    session_phase: str,
    *,
    interval: BarInterval | str | None = None,
    peer_count: int = 0,
) -> bool:
    """Return structural applicability without consulting realized returns."""

    if instrument.profile is None:
        return False
    if instrument.asset_class not in profile.asset_classes or instrument.product not in profile.products:
        return False
    if instrument.trading_calendar not in profile.trading_calendars:
        return False
    if direction not in profile.allowed_directions or spec.family not in profile.allowed_families:
        return False
    if interval is not None and BarInterval(interval) not in spec.intervals:
        return False
    if direction is StrategyDirection.SHORT and (
        not instrument.shortable or instrument.short_mechanism == "none"
    ):
        return False
    if spec.family is StrategyFamily.SESSION:
        if spec.strategy_id not in profile.session_strategy_ids:
            return False
        if session_phase == "continuous" and spec.strategy_id in {
            "opening_range_breakout",
            "etf_last_half_hour_momentum",
        }:
            return False
    if spec.family is StrategyFamily.RELATIVE_VALUE and (
        "cross_sectional" in spec.strategy_id or "pairs" in spec.strategy_id
    ):
        return peer_count >= profile.minimum_cross_sectional_peers
    return bool(session_phase.strip())


def _strict_utc_series(values: pd.Series, label: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="raise")
    timezone = getattr(parsed.dtype, "tz", None)
    if timezone is None or str(timezone) != "UTC":
        raise ValueError(f"{label} must contain explicit UTC timestamps")
    return parsed


def eligibility_inputs_from_bars(
    bars: pd.DataFrame,
    *,
    as_of: datetime,
    instrument: InstrumentConfig,
    interval: BarInterval,
    direction: StrategyDirection,
    research_size_notional: float = 0.0,
) -> EligibilityInputs:
    """Build conservative bar-proxy inputs from finalized rows visible at ``as_of`` only."""

    _require_utc(as_of, "as_of")
    if instrument.profile is None:
        raise ValueError("bar-derived eligibility requires an explicit instrument profile")
    if not math.isfinite(research_size_notional) or research_size_notional < 0:
        raise ValueError("research_size_notional must be finite and nonnegative")
    required = {
        "provider",
        "feed",
        "symbol",
        "interval",
        "available_at",
        "open_timestamp",
        "close_timestamp",
        "finalized",
        "revision",
        "close",
        "volume",
    }
    missing = sorted(required - set(bars.columns))
    if missing:
        raise ValueError(f"bars missing eligibility columns: {', '.join(missing)}")

    frame = bars.copy()
    for column in ("available_at", "open_timestamp", "close_timestamp"):
        frame[column] = _strict_utc_series(frame[column], column)
    visible = frame.loc[(frame["available_at"] <= pd.Timestamp(as_of)) & frame["finalized"].astype(bool)].copy()
    if visible.empty:
        raise ValueError("no finalized bars were available by as_of")
    expected_identity = (
        instrument.provider.lower(),
        instrument.feed.lower(),
        instrument.symbol.upper(),
        interval.value,
    )
    observed_identities = {
        (str(row.provider).lower(), str(row.feed).lower(), str(row.symbol).upper(), str(row.interval))
        for row in visible[["provider", "feed", "symbol", "interval"]].itertuples(index=False)
    }
    if observed_identities != {expected_identity}:
        raise ValueError("bars must match one exact instrument identity")
    visible = visible.sort_values(["open_timestamp", "available_at", "revision"], kind="stable")
    visible = visible.drop_duplicates("open_timestamp", keep="last").sort_values("open_timestamp", kind="stable")

    numeric = visible[["close", "volume"]].apply(pd.to_numeric, errors="coerce")
    if (
        not np.isfinite(numeric.to_numpy(dtype=float)).all()
        or (numeric["close"] <= 0).any()
        or (numeric["volume"] < 0).any()
    ):
        raise ValueError("bar prices must be positive and volume must be nonnegative")
    close = numeric["close"].astype(float)
    if "quote_volume" in visible and visible["quote_volume"].notna().all():
        notional = pd.to_numeric(visible["quote_volume"], errors="coerce").astype(float)
    else:
        notional = close * numeric["volume"].astype(float)
    if not np.isfinite(notional.to_numpy()).all() or (notional < 0).any():
        raise ValueError("bar notional volume must be finite and nonnegative")

    trailing = min(len(visible), 100)
    median_notional = float(notional.iloc[-trailing:].median())
    returns = np.log(close).diff().dropna().iloc[-trailing:]
    realized_volatility = float(returns.std(ddof=1)) if len(returns) >= 2 else 0.0
    if not math.isfinite(realized_volatility):
        realized_volatility = 0.0

    first_open = pd.Timestamp(visible.iloc[0]["open_timestamp"])
    last_open = pd.Timestamp(visible.iloc[-1]["open_timestamp"])
    expected = int((last_open - first_open) / INTERVAL_DURATION[interval]) + 1
    coverage = min(len(visible) / max(expected, 1), 1.0)
    sequence_continuous = len(visible) == expected
    watermark_payload = {
        "rows": len(visible),
        "last_open": last_open.isoformat(),
        "last_available": pd.Timestamp(visible.iloc[-1]["available_at"]).isoformat(),
        "last_revision": int(visible.iloc[-1]["revision"]),
        "last_payload_hash": (
            str(visible.iloc[-1]["payload_hash"]) if "payload_hash" in visible else None
        ),
    }
    participation = research_size_notional / median_notional if median_notional > 0 else math.inf
    return EligibilityInputs(
        provider=instrument.provider,
        feed=instrument.feed,
        venue=instrument.venue,
        product=instrument.product,
        asset_class=instrument.asset_class,
        profile=instrument.profile,
        trading_calendar=instrument.trading_calendar,
        symbol=instrument.symbol,
        interval=interval,
        direction=direction,
        as_of=as_of,
        data_through=pd.Timestamp(visible["available_at"].max()).to_pydatetime(),
        listing_at=first_open.to_pydatetime(),
        delisting_at=None,
        trading_status="active",
        halted=False,
        session_state="continuous" if instrument.trading_calendar == "24x7" else "open",
        finalized_history_bars=len(visible),
        coverage=coverage,
        sequence_continuous=sequence_continuous,
        correction_pending=False,
        median_notional_volume=median_notional,
        spread_bps=None,
        depth_notional=None,
        estimated_price_impact_bps=None,
        participation_rate=participation,
        last_price=float(close.iloc[-1]),
        tick_size=None,
        lot_size_valid=None,
        realized_volatility=realized_volatility,
        shortable=instrument.shortable,
        short_mechanism=instrument.short_mechanism,
        funding_applicable=instrument.funding_applicable,
        funding_rate_bps=None,
        borrow_applicable=instrument.borrow_applicable,
        borrow_fee_bps=None,
        research_provider=instrument.provider,
        research_feed=instrument.feed,
        liquidity_grade="bar_proxy",
        source_event_watermark=canonical_hash(watermark_payload),
    )


__all__ = [
    "AssetEligibilityEvidence",
    "EligibilityInputs",
    "eligibility_inputs_from_bars",
    "evaluate_asset_eligibility",
    "strategy_is_applicable",
]
