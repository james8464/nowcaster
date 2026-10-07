"""Causal, exploratory candle replay; never evidence of account-specific fills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

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

    @model_validator(mode="after")
    def validate_costs(self):
        if not self.account_currency.strip():
            raise ValueError("account currency required")
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
) -> ReplayResult:
    """Enter at following historical open; if both extrema touch, fill stop first.

    Historical candles are not account quotes. The synthetic quote is scoped to
    this function and its output is permanently labelled exploratory.
    """
    if initial_equity <= 0 or session_close <= session_open:
        raise ValueError("invalid session or paper equity")
    if not bars:
        return ReplayResult(strategy_id, "historical_base_exploratory", (), 0, 0)
    instrument = bars[0].instrument
    if any(bar.instrument != instrument for bar in bars):
        raise ValueError("mixed broker instrument")
    if any(bar.price_scope != "historical_base" for bar in bars):
        raise ValueError("historical replay requires historical-base candles")
    if list(bars) != sorted(bars, key=lambda item: item.start) or len({bar.start for bar in bars}) != len(bars):
        raise ValueError("bars must be unique and chronological")
    gaps = sum(left.end != right.start for left, right in zip(bars, bars[1:], strict=False))
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
        # Stress entry against the trader; never improve on the displayed side.
        entry = plan.entry + costs.slippage_points if plan.direction == "long" else plan.entry - costs.slippage_points
        if entry <= 0:
            no_trade_count += 1
            continue
        distance = abs(entry - plan.stop)
        if distance <= 0:
            no_trade_count += 1
            continue
        risk_units = initial_equity * D("0.0025") / (distance * instrument.point_value * costs.quote_to_account)
        exposure_units = initial_equity * D("0.25") / (entry * instrument.point_value * costs.quote_to_account)
        units = min(risk_units, exposure_units)
        if units <= 0:
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
                gross = points * units * instrument.point_value * costs.quote_to_account
                expenses = (costs.commission_per_unit + costs.financing_per_unit) * units * costs.quote_to_account
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
                    gross - expenses,
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
