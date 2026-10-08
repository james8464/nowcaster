"""Causal, exploratory candle replay; never evidence of account-specific fills."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.intraday.contracts import ConfirmedBar, MarketQuote
from src.intraday.strategies import evaluate_setup

D = Decimal
DEFAULT_INITIAL_EQUITY = D("10000")


class ReplayCosts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    account_currency: str
    quote_to_account: Decimal = Field(gt=0)
    slippage_points: Decimal = Field(ge=0)
    commission_per_unit: Decimal = Field(ge=0)
    financing_per_unit: Decimal = Field(ge=0)
    conversion_fee_fraction: Decimal | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_costs(self):
        if not self.account_currency.strip():
            raise ValueError("account currency required")
        return self


class HistoricalFXRate(BaseModel):
    """Exploratory quote-to-account factors known at a historical bar open.

    They are derived from historical FX bid/ask candles, not OANDA's
    account-specific home-conversion factors or executable account quotes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    gain_factor: Decimal = Field(gt=0)
    loss_factor: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def conservative_sides(self):
        if self.gain_factor > self.loss_factor:
            raise ValueError("historical FX gain factor exceeds loss factor")
        return self


@dataclass(frozen=True)
class ReplayTrade:
    strategy_id: str
    direction: str
    decided_at: datetime
    entered_at: datetime
    exited_at: datetime
    entry: Decimal
    exit: Decimal
    units: Decimal
    exit_reason: str
    net_pnl: Decimal
    decision_hash: str


@dataclass(frozen=True)
class ReplayResult:
    strategy_id: str
    price_scope: str
    trades: tuple[ReplayTrade, ...]
    no_trade_count: int
    gaps: int

    @property
    def total_net_pnl(self) -> Decimal:
        return sum((trade.net_pnl for trade in self.trades), D(0))


def replay_session(
    strategy_id: str,
    bars: list[ConfirmedBar] | tuple[ConfirmedBar, ...],
    *,
    session_open: datetime,
    session_close: datetime,
    costs: ReplayCosts,
    initial_equity: Decimal = DEFAULT_INITIAL_EQUITY,
    direction_filter: str | None = None,
    fx_at_open: Mapping[datetime, HistoricalFXRate] | None = None,
    minimum_trade_size: Decimal | None = None,
    trade_units_precision: int | None = None,
) -> ReplayResult:
    """Enter at following historical open; if both extrema touch, fill stop first.

    Historical candles are not account quotes. The synthetic quote is scoped to
    this function and its output is permanently labelled exploratory.
    """
    if initial_equity <= 0 or session_close <= session_open:
        raise ValueError("invalid session or paper equity")
    if direction_filter not in (None, "long", "short"):
        raise ValueError("direction filter must be long or short")
    if (minimum_trade_size is None) != (trade_units_precision is None):
        raise ValueError("broker minimum and precision must be supplied together")
    if minimum_trade_size is not None and (
        not minimum_trade_size.is_finite()
        or minimum_trade_size <= 0
        or type(trade_units_precision) is not int
        or not 0 <= trade_units_precision <= 9
        or minimum_trade_size % D(1).scaleb(-trade_units_precision)
    ):
        raise ValueError("invalid broker trade size")
    if not bars:
        return ReplayResult(strategy_id, "historical_base_exploratory", (), 0, 0)
    instrument = bars[0].instrument
    if any(bar.instrument != instrument for bar in bars):
        raise ValueError("mixed broker instrument")
    if any(bar.price_scope != "historical_base" for bar in bars):
        raise ValueError("historical replay requires historical-base candles")
    if instrument.quote_currency != costs.account_currency and costs.conversion_fee_fraction is None:
        raise ValueError("foreign-currency conversion fee must be explicit")
    if list(bars) != sorted(bars, key=lambda item: item.start) or len({bar.start for bar in bars}) != len(bars):
        raise ValueError("bars must be unique and chronological")
    gaps = sum(left.end != right.start for left, right in zip(bars, bars[1:], strict=False))
    if fx_at_open is not None and instrument.quote_currency != costs.account_currency:
        gaps += sum(bar.start not in fx_at_open for bar in bars)
    if gaps:
        return ReplayResult(strategy_id, "historical_base_exploratory", (), 0, gaps)
    trades: list[ReplayTrade] = []
    no_trade_count = 0
    # The source adapter records the real download time. For a *counterfactual*
    # replay only, each completed candle is assumed visible at its end. This
    # assumption is not forwarded to the prospective evidence gate.
    as_if_confirmed = [bar.model_copy(update={"available_at": bar.end}) for bar in bars]
    for index in range(1, len(bars)):
        latest = bars[index - 1]
        following = bars[index]
        if not session_open <= latest.start < latest.end <= following.start < session_close:
            continue
        at = following.start + timedelta(microseconds=1)
        synthetic = MarketQuote(
            instrument=instrument,
            account_feed_hash="0" * 64,
            observed_at=at,
            received_at=at,
            bid=following.bid_open,
            ask=following.ask_open,
            status="tradeable",
            source_key=f"historical-synthetic:{following.source_key}",
        )
        plan = evaluate_setup(
            strategy_id,
            as_if_confirmed[:index],
            synthetic,
            session_open=session_open,
            session_close=session_close,
        )
        if plan.status != "ready":
            no_trade_count += 1
            continue
        if direction_filter is not None and plan.direction != direction_filter:
            no_trade_count += 1
            continue
        # Stress entry against the trader; never improve on the displayed side.
        entry = plan.entry + costs.slippage_points if plan.direction == "long" else plan.entry - costs.slippage_points
        if entry <= 0:
            no_trade_count += 1
            continue
        distance = abs(entry - plan.stop)
        if distance <= 0:
            no_trade_count += 1
            continue
        entry_factor = (
            fx_at_open[following.start].loss_factor
            if fx_at_open is not None and instrument.quote_currency != costs.account_currency
            else costs.quote_to_account
        )
        risk_per_unit = (
            (
                (distance + costs.slippage_points) * instrument.point_value
                + costs.commission_per_unit
                + costs.financing_per_unit
            )
            * entry_factor
            * (D(1) + (costs.conversion_fee_fraction or D(0)))
        )
        risk_units = initial_equity * D("0.0025") / risk_per_unit
        exposure_units = initial_equity * D("0.25") / (entry * instrument.point_value * entry_factor)
        units = min(risk_units, exposure_units)
        if trade_units_precision is not None:
            units = units.quantize(D(1).scaleb(-trade_units_precision), rounding=ROUND_DOWN)
        if units <= 0 or (minimum_trade_size is not None and units < minimum_trade_size):
            no_trade_count += 1
            continue
        closed: ReplayTrade | None = None
        for future in bars[index:]:
            if future.start >= session_close:
                break
            if plan.direction == "long":
                stop_hit = future.bid_low <= plan.stop
                target_hit = future.bid_high >= plan.target
                if stop_hit:
                    exit_price = min(plan.stop, future.bid_open) - costs.slippage_points
                elif target_hit:
                    exit_price = plan.target - costs.slippage_points
                else:
                    exit_price = future.bid_close - costs.slippage_points
                points = exit_price - entry
            else:
                stop_hit = future.ask_high >= plan.stop
                target_hit = future.ask_low <= plan.target
                if stop_hit:
                    exit_price = max(plan.stop, future.ask_open) + costs.slippage_points
                elif target_hit:
                    exit_price = plan.target + costs.slippage_points
                else:
                    exit_price = future.ask_close + costs.slippage_points
                points = entry - exit_price
            # Synthetic entry is one microsecond after the next bar opens to
            # preserve causality. Do not let that artificial offset expose a
            # whole extra five-minute bar after the one-hour deadline.
            timed_out = future.end >= plan.exit_by - timedelta(microseconds=1) or future.end >= session_close
            if stop_hit or target_hit or timed_out:
                reason = "stop" if stop_hit else "target" if target_hit else "time_limit"
                fx = (
                    fx_at_open[future.start]
                    if fx_at_open is not None and instrument.quote_currency != costs.account_currency
                    else None
                )
                gross_quote = points * units * instrument.point_value
                gross = gross_quote * (
                    (fx.gain_factor if gross_quote >= 0 else fx.loss_factor)
                    if fx is not None
                    else costs.quote_to_account
                )
                expenses = (
                    (costs.commission_per_unit + costs.financing_per_unit)
                    * units
                    * (fx.loss_factor if fx is not None else costs.quote_to_account)
                )
                conversion_fee = (abs(gross) + expenses) * (costs.conversion_fee_fraction or D(0))
                closed = ReplayTrade(
                    strategy_id,
                    plan.direction,
                    plan.decision_at,
                    following.start,
                    min(future.end, session_close),
                    entry,
                    exit_price,
                    units,
                    reason,
                    gross - expenses - conversion_fee,
                    plan.evidence_hash,
                )
                break
        if closed is None:
            # Unknown post-session fill: never silently credit a trade.
            no_trade_count += 1
            continue
        trades.append(closed)
        break  # at most one historical position per instrument/session
    return ReplayResult(strategy_id, "historical_base_exploratory", tuple(trades), no_trade_count, gaps)
