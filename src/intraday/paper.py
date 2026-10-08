"""Account-specific, paper-only position sizing and lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.intraday.contracts import InstrumentSpec, MarketQuote
from src.intraday.strategies import SetupDecision

D = Decimal


class PaperRiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    risk_fraction: Decimal = Field(default=D("0.0025"), gt=0, le=D("0.0025"))
    daily_loss_fraction: Decimal = Field(default=D("0.01"), gt=0, le=D("0.01"))
    maximum_drawdown: Decimal = Field(default=D("0.05"), gt=0, le=D("0.05"))
    maximum_exposure_fraction: Decimal = Field(default=D("0.25"), gt=0, le=D("0.25"))
    maximum_daily_entries: int = Field(default=3, gt=0, le=3)


class PaperExecutionCosts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    verified: bool = False
    slippage_points: Decimal = Field(default=D(0), ge=0)
    commission_per_unit: Decimal = Field(default=D(0), ge=0)
    financing_per_unit: Decimal = Field(default=D(0), ge=0)


class FXConversion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    from_currency: str
    to_currency: str
    position_value: Decimal = Field(gt=0)
    account_gain: Decimal = Field(gt=0)
    account_loss: Decimal = Field(gt=0)
    observed_at: datetime

    @model_validator(mode="after")
    def valid_identity(self):
        if not self.from_currency or not self.to_currency:
            raise ValueError("conversion currencies required")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("conversion must have UTC time")
        if not self.account_gain <= self.position_value <= self.account_loss:
            raise ValueError("account conversion gain/value/loss sides inconsistent")
        return self


@dataclass(frozen=True)
class PaperPosition:
    instrument: InstrumentSpec
    strategy_id: str
    direction: Literal["long", "short"]
    decided_at: datetime
    opened_at: datetime
    account_feed_hash: str
    entry: Decimal
    stop: Decimal
    target: Decimal
    exit_by: datetime
    units: Decimal
    evidence_hash: str


@dataclass(frozen=True)
class ClosedPaperTrade:
    position: PaperPosition
    closed_at: datetime
    exit_price: Decimal
    exit_reason: Literal["stop", "target", "time_limit"]
    net_pnl: Decimal


class PaperAccount:
    """Pure in-memory state; a separate append-only journal persists every action."""

    def __init__(
        self,
        policy: PaperRiskPolicy,
        initial_cash: Decimal,
        *,
        account_currency: str,
        costs: PaperExecutionCosts | None = None,
    ):
        if initial_cash <= 0:
            raise ValueError("paper cash must be positive")
        if not account_currency.strip():
            raise ValueError("paper account currency required")
        self.policy = policy
        self.account_currency = account_currency
        self.costs = costs or PaperExecutionCosts()
        self.initial_cash = initial_cash
        self.equity = initial_cash
        self.high_water = initial_cash
        self.daily_realized_pnl = D(0)
        self.daily_entries = 0
        self.positions: dict[str, PaperPosition] = {}
        self.last_rejection: str | None = None
        self._day: date | None = None

    def _roll_day(self, at: datetime) -> None:
        if self._day != at.date():
            self.daily_realized_pnl = D(0)
            self.daily_entries = 0
            self._day = at.date()

    def _conversion_factors(
        self, instrument: InstrumentSpec, quote: MarketQuote, conversion: FXConversion | None
    ) -> tuple[Decimal, Decimal, Decimal] | None:
        if instrument.quote_currency == self.account_currency:
            return (D(1), D(1), D(1))
        if (
            conversion is None
            or conversion.from_currency != instrument.quote_currency
            or conversion.to_currency != self.account_currency
            or conversion.observed_at > quote.received_at
            or quote.received_at - conversion.observed_at > timedelta(seconds=15)
        ):
            return None
        return conversion.position_value, conversion.account_gain, conversion.account_loss

    def open(
        self,
        plan: SetupDecision,
        instrument: InstrumentSpec,
        quote: MarketQuote,
        *,
        unit_step: Decimal,
        conversion: FXConversion | None = None,
    ) -> PaperPosition | None:
        self._roll_day(quote.received_at)
        reason = self._opening_rejection(plan, instrument, quote, unit_step)
        if reason:
            self.last_rejection = reason
            return None
        factors = self._conversion_factors(instrument, quote, conversion)
        if factors is None:
            self.last_rejection = "currency_conversion_unavailable"
            return None
        position_rate, _, loss_rate = factors
        entry = (
            quote.ask + self.costs.slippage_points
            if plan.direction == "long"
            else quote.bid - self.costs.slippage_points
        )
        risk_per_unit = (
            (abs(entry - plan.stop) + self.costs.slippage_points) * instrument.point_value
            + self.costs.commission_per_unit
            + self.costs.financing_per_unit
        ) * loss_rate
        if (
            risk_per_unit <= 0
            or (plan.direction == "long" and entry <= plan.stop)
            or (plan.direction == "short" and entry >= plan.stop)
        ):
            self.last_rejection = "invalid_stop_distance"
            return None
        risk_units = self.equity * self.policy.risk_fraction / risk_per_unit
        exposure_units = (
            self.equity * self.policy.maximum_exposure_fraction / (entry * instrument.point_value * position_rate)
        )
        units = (min(risk_units, exposure_units) / unit_step).to_integral_value(rounding=ROUND_DOWN) * unit_step
        if units <= 0:
            self.last_rejection = "below_minimum_paper_size"
            return None
        position = PaperPosition(
            instrument=instrument,
            strategy_id=plan.strategy_id,
            direction=plan.direction,
            decided_at=plan.decision_at,
            opened_at=quote.received_at,
            account_feed_hash=quote.account_feed_hash,
            entry=entry,
            stop=plan.stop,
            target=plan.target,
            exit_by=plan.exit_by,
            units=units,
            evidence_hash=plan.evidence_hash,
        )
        self.positions[instrument.broker_symbol] = position
        self.daily_entries += 1
        self.last_rejection = None
        return position

    def _opening_rejection(
        self, plan: SetupDecision, instrument: InstrumentSpec, quote: MarketQuote, unit_step: Decimal
    ) -> str | None:
        if plan.status != "ready":
            return "no_trade_plan"
        if not self.costs.verified:
            return "execution_costs_unverified"
        if unit_step <= 0 or quote.instrument != instrument or quote.status != "tradeable":
            return "instrument_or_quote_unavailable"
        if quote.received_at < plan.entry_at or quote.received_at - plan.decision_at > timedelta(seconds=15):
            return "entry_quote_stale"
        if quote.received_at >= plan.exit_by:
            return "session_closing"
        if instrument.broker_symbol in self.positions:
            return "position_already_open"
        if self.daily_entries >= self.policy.maximum_daily_entries:
            return "daily_entry_limit"
        if self.daily_realized_pnl <= -self.initial_cash * self.policy.daily_loss_fraction:
            return "daily_loss_limit"
        if self.equity <= self.high_water * (1 - self.policy.maximum_drawdown):
            return "drawdown_halt"
        return None

    def update(self, quote: MarketQuote, *, conversion: FXConversion | None = None) -> ClosedPaperTrade | None:
        self._roll_day(quote.received_at)
        position = self.positions.get(quote.instrument.broker_symbol)
        if position is None or quote.status != "tradeable" or quote.received_at <= position.opened_at:
            return None
        if quote.account_feed_hash != position.account_feed_hash or quote.observed_at <= position.opened_at:
            self.last_rejection = "exit_quote_identity_or_time_invalid"
            return None
        factors = self._conversion_factors(position.instrument, quote, conversion)
        if factors is None:
            self.last_rejection = "currency_conversion_unavailable"
            return None
        _, gain_rate, loss_rate = factors
        executable = quote.bid if position.direction == "long" else quote.ask
        if position.direction == "long":
            reason = "stop" if executable <= position.stop else "target" if executable >= position.target else None
        else:
            reason = "stop" if executable >= position.stop else "target" if executable <= position.target else None
        if reason is None and quote.received_at >= position.exit_by:
            reason = "time_limit"
        if reason is None:
            return None
        executable = (
            executable - self.costs.slippage_points
            if position.direction == "long"
            else executable + self.costs.slippage_points
        )
        signed_points = executable - position.entry if position.direction == "long" else position.entry - executable
        gross_quote = signed_points * position.instrument.point_value * position.units
        expenses_quote = (self.costs.commission_per_unit + self.costs.financing_per_unit) * position.units
        pnl = gross_quote * (gain_rate if gross_quote >= 0 else loss_rate) - expenses_quote * loss_rate
        closed = ClosedPaperTrade(position, quote.received_at, executable, reason, pnl)
        del self.positions[position.instrument.broker_symbol]
        self.equity += pnl
        self.daily_realized_pnl += pnl
        self.high_water = max(self.high_water, self.equity)
        return closed
