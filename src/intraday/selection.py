"""Pre-registered historical comparison. Output is exploratory, never live proof."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.intraday.contracts import ConfirmedBar, InstrumentSpec
from src.intraday.eligibility import CostEvidence
from src.intraday.historical import ReplayCosts, replay_session
from src.intraday.research import _daily_block_lower
from src.strategies.types import canonical_hash

D = Decimal
RULES = ("opening_range_15", "opening_range_30", "trend_pullback", "range_reversion")
DIRECTIONS = ("long", "short")


class SelectionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    round_id: str
    instruments: tuple[InstrumentSpec, ...]
    development_end: datetime
    validation_end: datetime
    sealed_end: datetime
    costs: dict[str, ReplayCosts]
    cost_evidence: dict[str, CostEvidence]
    stress_multiplier: Decimal = Field(default=D(2), ge=2)
    minimum_stage_sessions: int = Field(default=30, ge=2)
    minimum_stage_trades: int = Field(default=30, ge=2)

    @model_validator(mode="after")
    def valid_round(self):
        if not self.round_id.strip() or not self.instruments:
            raise ValueError("selection needs round ID and instruments")
        if not self.development_end < self.validation_end < self.sealed_end:
            raise ValueError("chronological boundaries required")
        if any(
            item.tzinfo is None or item.utcoffset() != timedelta(0)
            for item in (self.development_end, self.validation_end, self.sealed_end)
        ):
            raise ValueError("boundaries must be UTC")
        symbols = [item.broker_symbol for item in self.instruments]
        if len(set(symbols)) != len(symbols):
            raise ValueError("duplicate broker product")
        if not set(self.costs) <= set(symbols) or not set(self.cost_evidence) <= set(symbols):
            raise ValueError("costs for unknown product")
        return self

    @property
    def identity_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


@dataclass(frozen=True)
class SelectionAttempt:
    stage: Literal["development", "validation", "sealed"]
    broker_symbol: str
    rule: str
    direction: Literal["long", "short"]
    closed_trades: int
    net_pnl: Decimal
    stressed_net_pnl: Decimal
    daily_lower_bound: Decimal
    no_trade_count: int
    gap_count: int
    rejection_reason: str | None
    input_hash: str


@dataclass(frozen=True)
class SelectedRule:
    broker_symbol: str
    rule: str
    direction: Literal["long", "short"]
    identity_hash: str


@dataclass(frozen=True)
class SelectionReport:
    manifest_hash: str
    attempts: tuple[SelectionAttempt, ...]
    selected: tuple[SelectedRule, ...]
    cash_baseline: Decimal = D(0)
    price_scope: str = "historical_base_exploratory"

    @property
    def multiple_comparisons(self) -> int:
        return len({(item.broker_symbol, item.rule, item.direction) for item in self.attempts})


def _sessions(bars: tuple[ConfirmedBar, ...]) -> tuple[tuple[ConfirmedBar, ...], ...]:
    if not bars:
        return ()
    groups: list[list[ConfirmedBar]] = []
    for bar in bars:
        if not groups or bar.start.date() != groups[-1][-1].start.date():
            groups.append([])
        groups[-1].append(bar)
    return tuple(tuple(group) for group in groups)


def _missing_declared_sessions(bars: tuple[ConfirmedBar, ...], weekdays: tuple[int, ...]) -> int:
    """Fail closed on a wholly absent declared weekday between observed sessions.

    A broker holiday may explain a missing day, but without an explicit calendar
    the historical screen cannot distinguish that from missing market data.
    """
    if len(bars) < 2:
        return 0
    present = {bar.start.date() for bar in bars}
    day = min(present) + timedelta(days=1)
    last = max(present)
    missing = 0
    while day < last:
        missing += day.weekday() in weekdays and day not in present
        day += timedelta(days=1)
    return missing


def _assess(
    manifest: SelectionManifest,
    instrument: InstrumentSpec,
    bars: tuple[ConfirmedBar, ...],
    stage: Literal["development", "validation", "sealed"],
    rule: str,
    direction: Literal["long", "short"],
) -> SelectionAttempt:
    evidence = manifest.cost_evidence.get(instrument.broker_symbol)
    costs = manifest.costs.get(instrument.broker_symbol)
    reason = None
    if (
        evidence is None
        or costs is None
        or evidence.broker_symbol != instrument.broker_symbol
        or evidence.product != instrument.product
        or evidence.slippage_points != costs.slippage_points
        or evidence.commission_per_unit != costs.commission_per_unit
        or evidence.financing_per_unit != costs.financing_per_unit
        or (
            instrument.quote_currency != costs.account_currency
            and (
                evidence.conversion_fee_fraction is None
                or costs.conversion_fee_fraction != evidence.conversion_fee_fraction
            )
        )
    ):
        reason = "costs_unverified"
    elif not bars:
        reason = "no_historical_data"
    if reason:
        return SelectionAttempt(
            stage,
            instrument.broker_symbol,
            rule,
            direction,
            0,
            D(0),
            D(0),
            D(0),
            0,
            0,
            reason,
            canonical_hash(
                {
                    "manifest": manifest.identity_hash,
                    "stage": stage,
                    "symbol": instrument.broker_symbol,
                    "rule": rule,
                    "direction": direction,
                }
            ),
        )
    assert costs is not None and evidence is not None
    spread = max((bar.ask_close - bar.bid_close for bar in bars), default=D(0))
    stressed = costs.model_copy(
        update={
            "slippage_points": costs.slippage_points * manifest.stress_multiplier
            + spread * (manifest.stress_multiplier - 1) / 2,
            "commission_per_unit": costs.commission_per_unit * manifest.stress_multiplier,
            "financing_per_unit": costs.financing_per_unit * manifest.stress_multiplier,
            "conversion_fee_fraction": (
                costs.conversion_fee_fraction * manifest.stress_multiplier
                if costs.conversion_fee_fraction is not None
                else None
            ),
        }
    )
    base_results = []
    stressed_results = []
    for group in _sessions(bars):
        kwargs = {
            "session_open": group[0].start,
            "session_close": group[-1].end,
            "direction_filter": direction,
            "minimum_trade_size": evidence.minimum_trade_size,
            "trade_units_precision": evidence.trade_units_precision,
        }
        base_results.append(replay_session(rule, group, costs=costs, **kwargs))
        stressed_results.append(replay_session(rule, group, costs=stressed, **kwargs))
    net = sum((item.total_net_pnl for item in base_results), D(0))
    stressed_net = sum((item.total_net_pnl for item in stressed_results), D(0))
    count = sum(len(item.trades) for item in base_results)
    gaps = sum(item.gaps for item in base_results)
    if evidence is not None:
        gaps += _missing_declared_sessions(bars, evidence.session.weekdays)
    lower = _daily_block_lower(tuple(item.total_net_pnl for item in base_results))
    reason = (
        "historical_gap"
        if gaps
        else "insufficient_net_evidence"
        if count == 0 or net <= 0 or stressed_net <= 0 or lower <= 0
        else "insufficient_stage_sample"
        if len(base_results) < manifest.minimum_stage_sessions or count < manifest.minimum_stage_trades
        else None
    )
    return SelectionAttempt(
        stage,
        instrument.broker_symbol,
        rule,
        direction,
        count,
        net,
        stressed_net,
        lower,
        sum(item.no_trade_count for item in base_results),
        gaps,
        reason,
        canonical_hash(
            {
                "manifest": manifest.identity_hash,
                "bars": [item.model_dump(mode="json") for item in bars],
                "stage": stage,
                "rule": rule,
                "direction": direction,
            }
        ),
    )


def run_selection(manifest: SelectionManifest, bars_by_product: dict[str, tuple[ConfirmedBar, ...]]) -> SelectionReport:
    """Select on chronological development/validation, then inspect sealed once.

    This does not adapt a running prospective rule; the returned identity is
    tied to every declared cost, product, boundary and input bar.
    """
    symbols = {item.broker_symbol for item in manifest.instruments}
    if not set(bars_by_product) <= symbols:
        raise ValueError("unknown product in historical data")
    attempts: list[SelectionAttempt] = []
    chosen: list[SelectedRule] = []
    for instrument in manifest.instruments:
        bars = bars_by_product.get(instrument.broker_symbol, ())
        if tuple(sorted(bars, key=lambda item: item.start)) != bars or len({item.start for item in bars}) != len(bars):
            raise ValueError("historical bars must be unique and chronological")
        if any(item.instrument != instrument or item.price_scope != "historical_base" for item in bars):
            raise ValueError("historical product or price scope mismatch")
        if any(item.end > manifest.sealed_end for item in bars):
            raise ValueError("bar outside sealed period")
        if any(
            item.start < boundary < item.end
            for item in bars
            for boundary in (manifest.development_end, manifest.validation_end)
        ):
            raise ValueError("bar crosses a sealed stage boundary")
        evidence = manifest.cost_evidence.get(instrument.broker_symbol)
        if evidence is not None and evidence.broker_symbol == instrument.broker_symbol:
            bars = tuple(
                item
                for item in bars
                if evidence.session.is_open(item.start)
                and evidence.session.is_open(item.end - timedelta(microseconds=1))
            )
        stages = {
            "development": tuple(item for item in bars if item.start < manifest.development_end),
            "validation": tuple(
                item for item in bars if manifest.development_end <= item.start < manifest.validation_end
            ),
            "sealed": tuple(item for item in bars if manifest.validation_end <= item.start < manifest.sealed_end),
        }
        candidates = []
        for rule in RULES:
            for direction in DIRECTIONS:
                development = _assess(manifest, instrument, stages["development"], "development", rule, direction)
                validation = _assess(manifest, instrument, stages["validation"], "validation", rule, direction)
                attempts.extend((development, validation))
                if development.rejection_reason is None and validation.rejection_reason is None:
                    candidates.append((validation.stressed_net_pnl, rule, direction))
        if candidates:
            _, rule, direction = max(candidates)
            development = next(
                item
                for item in attempts
                if item.broker_symbol == instrument.broker_symbol
                and item.stage == "development"
                and item.rule == rule
                and item.direction == direction
            )
            validation = next(
                item
                for item in attempts
                if item.broker_symbol == instrument.broker_symbol
                and item.stage == "validation"
                and item.rule == rule
                and item.direction == direction
            )
            sealed = _assess(manifest, instrument, stages["sealed"], "sealed", rule, direction)
            attempts.append(sealed)
            if sealed.rejection_reason is None:
                chosen.append(
                    SelectedRule(
                        instrument.broker_symbol,
                        rule,
                        direction,
                        canonical_hash(
                            {
                                "manifest": manifest.identity_hash,
                                "development": development.input_hash,
                                "validation": validation.input_hash,
                                "sealed": sealed.input_hash,
                            }
                        ),
                    )
                )
    return SelectionReport(manifest.identity_hash, tuple(attempts), tuple(chosen))
