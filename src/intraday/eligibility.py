"""Exact-product, source-backed admission for prospective paper research.

Broker inventory establishes identity only. It never supplies an executable
session, an account-currency conversion, or the full cost schedule.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.intraday.contracts import InstrumentSpec
from src.intraday.paper import FXConversion


class SessionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    weekdays: tuple[int, ...]
    opens_utc: str
    closes_utc: str

    @model_validator(mode="after")
    def valid_hours(self):
        if not self.weekdays or any(day not in range(7) for day in self.weekdays):
            raise ValueError("session weekdays must be explicit")
        for value in (self.opens_utc, self.closes_utc):
            try:
                hour, minute = map(int, value.split(":"))
            except (ValueError, TypeError) as exc:
                raise ValueError("session time must be HH:MM UTC") from exc
            if not (0 <= hour < 24 and 0 <= minute < 60) or len(value) != 5:
                raise ValueError("session time must be HH:MM UTC")
        if self.opens_utc == self.closes_utc:
            raise ValueError("session cannot be zero length")
        return self

    def is_open(self, at: datetime) -> bool:
        clock = at.strftime("%H:%M")
        if self.opens_utc < self.closes_utc:
            return at.weekday() in self.weekdays and self.opens_utc <= clock < self.closes_utc
        yesterday = (at.weekday() - 1) % 7
        return (at.weekday() in self.weekdays and clock >= self.opens_utc) or (
            yesterday in self.weekdays and clock < self.closes_utc
        )


class CostEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    broker_symbol: str
    product: Literal["cfd", "margin_fx"]
    margin_rate: Decimal = Field(gt=0, le=1)
    minimum_trade_size: Decimal = Field(gt=0)
    trade_units_precision: int = Field(ge=0, le=9)
    commission_per_unit: Decimal = Field(ge=0)
    financing_per_unit: Decimal = Field(ge=0)
    slippage_points: Decimal = Field(ge=0)
    conversion_fee_fraction: Decimal | None = Field(default=None, ge=0, le=1)
    observed_at: datetime
    source: str
    source_kind: Literal["broker_terms", "user_attested"]
    session: SessionEvidence

    @model_validator(mode="after")
    def credible_source(self):
        if not self.broker_symbol.strip() or not self.source.strip():
            raise ValueError("exact product and source required")
        if self.source_kind == "broker_terms" and not self.source.startswith("https://"):
            raise ValueError("broker terms require an HTTPS source URL")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("source observation must use UTC")
        if self.minimum_trade_size % Decimal(1).scaleb(-self.trade_units_precision):
            raise ValueError("minimum trade size must fit broker unit precision")
        return self


class EligibilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    instrument: InstrumentSpec
    evaluated_at: datetime
    product_label: str
    broker_margin_rate: Decimal | None
    minimum_trade_size: Decimal | None
    trade_units_precision: int | None
    costs: CostEvidence | None
    session_open: bool
    conversion_rate: Decimal | None
    conversion_gain_rate: Decimal | None
    conversion_loss_rate: Decimal | None
    conversion_observed_at: datetime | None
    reasons: tuple[str, ...]

    @property
    def paper_eligible(self) -> bool:
        return not self.reasons


def evaluate_product(
    instrument: InstrumentSpec,
    broker_row: dict,
    costs: CostEvidence | None,
    conversion: FXConversion | None,
    at: datetime,
    *,
    account_currency: str = "GBP",
) -> EligibilityResult:
    if at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise ValueError("evaluation time must be UTC")
    reasons: list[str] = []
    required_type = "CURRENCY" if instrument.product == "margin_fx" else "CFD"
    if broker_row.get("name") != instrument.broker_symbol or broker_row.get("type") != required_type:
        reasons.append("broker_identity_mismatch")
    label = str(broker_row.get("displayName") or instrument.broker_symbol)
    try:
        margin = Decimal(str(broker_row["marginRate"]))
        if not margin.is_finite() or not 0 < margin <= 1:
            raise ValueError
    except (KeyError, TypeError, ValueError, InvalidOperation):
        margin = None
        reasons.append("broker_margin_unavailable")
    try:
        minimum = Decimal(str(broker_row["minimumTradeSize"]))
        precision = broker_row["tradeUnitsPrecision"]
        if (
            not minimum.is_finite()
            or minimum <= 0
            or type(precision) is not int
            or not 0 <= precision <= 9
            or minimum % Decimal(1).scaleb(-precision)
        ):
            raise ValueError("invalid trade size")
    except (KeyError, TypeError, ValueError, InvalidOperation):
        minimum = None
        precision = None
        reasons.append("broker_trade_size_unavailable")
    if costs is None:
        reasons.append("cost_evidence_missing")
        session_open = False
    else:
        if costs.broker_symbol != instrument.broker_symbol or costs.product != instrument.product:
            reasons.append("cost_product_mismatch")
        if costs.observed_at > at or at - costs.observed_at > timedelta(days=30):
            reasons.append("cost_evidence_stale")
        if margin is not None and margin != costs.margin_rate:
            reasons.append("margin_rate_changed")
        if minimum is not None and (minimum != costs.minimum_trade_size or precision != costs.trade_units_precision):
            reasons.append("broker_trade_size_changed")
        session_open = costs.session.is_open(at)
        if not session_open:
            reasons.append("session_closed")
    if instrument.quote_currency == account_currency:
        conversion_rate = Decimal(1)
        conversion_gain_rate = Decimal(1)
        conversion_loss_rate = Decimal(1)
        conversion_observed_at = at
    elif (
        conversion is None
        or conversion.from_currency != instrument.quote_currency
        or conversion.to_currency != account_currency
        or conversion.observed_at > at
        or at - conversion.observed_at > timedelta(seconds=15)
    ):
        conversion_rate = None
        conversion_gain_rate = None
        conversion_loss_rate = None
        conversion_observed_at = None
        reasons.append("currency_conversion_unavailable")
    else:
        conversion_rate = conversion.position_value
        conversion_gain_rate = conversion.account_gain
        conversion_loss_rate = conversion.account_loss
        conversion_observed_at = conversion.observed_at
    if instrument.quote_currency != account_currency and (costs is None or costs.conversion_fee_fraction is None):
        reasons.append("currency_conversion_fee_unverified")
    return EligibilityResult(
        instrument=instrument,
        evaluated_at=at,
        product_label=label,
        broker_margin_rate=margin,
        minimum_trade_size=minimum,
        trade_units_precision=precision,
        costs=costs,
        session_open=session_open,
        conversion_rate=conversion_rate,
        conversion_gain_rate=conversion_gain_rate,
        conversion_loss_rate=conversion_loss_rate,
        conversion_observed_at=conversion_observed_at,
        reasons=tuple(reasons),
    )
