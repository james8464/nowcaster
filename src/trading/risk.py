from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.backtest.execution import OrderIntent
from src.strategies.types import canonical_hash
from src.trading.types import TradingEnvironment


class RiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_data_age_seconds: int = Field(default=30, ge=1, le=300)
    max_position_fraction: Decimal = Field(default=Decimal("0.02"), gt=0, le=1)
    max_gross_fraction: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)
    max_daily_loss_fraction: Decimal = Field(default=Decimal("0.01"), gt=0, le=1)
    max_drawdown_fraction: Decimal = Field(default=Decimal("0.05"), gt=0, le=1)
    max_turnover_fraction: Decimal = Field(default=Decimal("0.25"), gt=0)
    max_orders_per_minute: int = Field(default=10, ge=1, le=100)
    max_spread_bps: Decimal = Field(default=Decimal("30"), gt=0)
    max_price_collar_bps: Decimal = Field(default=Decimal("20"), gt=0)


class RiskContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=True)

    environment: TradingEnvironment
    account_suffix: str
    expected_account_suffix: str
    cohort_hash: str
    expected_cohort_hash: str
    provider: str
    expected_provider: str
    feed: str
    expected_feed: str
    data_age_seconds: float | None
    unresolved_mismatches: int | None
    account_equity: Decimal | None
    buying_power: Decimal | None
    current_position_notional: Decimal | None
    gross_exposure: Decimal | None
    turnover_today: Decimal | None
    orders_last_minute: int | None
    spread_bps: Decimal | None
    reference_price: Decimal | None
    limit_price: Decimal | None
    daily_pnl: Decimal | None
    drawdown_fraction: Decimal | None
    frozen: bool
    duplicate_order: bool
    conflicting_order: bool
    asset_tradable: bool
    asset_shortable: bool
    asset_easy_to_borrow: bool
    is_opening_short: bool


class RiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reasons: tuple[str, ...]
    input_hash: str
    policy_hash: str
    limits: dict[str, str | int]
    utilization: dict[str, str | int]


def _finite(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return Decimal(str(value)).is_finite()
    except (InvalidOperation, ValueError):
        return False


class PreTradeRiskEngine:
    def __init__(self, policy: RiskPolicy | None = None):
        self.policy = policy or RiskPolicy()

    def evaluate(self, intent: OrderIntent, context: RiskContext) -> RiskDecision:
        reasons: set[str] = set()
        required = (
            context.data_age_seconds,
            context.unresolved_mismatches,
            context.account_equity,
            context.buying_power,
            context.current_position_notional,
            context.gross_exposure,
            context.turnover_today,
            context.orders_last_minute,
            context.spread_bps,
            context.reference_price,
            context.limit_price,
            context.daily_pnl,
            context.drawdown_fraction,
        )
        if not all(_finite(value) for value in required):
            reasons.add("invalid_risk_input")
            return self._decision(context, reasons, proposed_notional=None)

        equity = Decimal(context.account_equity)
        buying_power = Decimal(context.buying_power)
        reference_price = Decimal(context.reference_price)
        limit_price = Decimal(context.limit_price)
        proposed_notional = Decimal(str(intent.quantity)) * limit_price
        if equity <= 0 or reference_price <= 0 or limit_price <= 0:
            reasons.add("invalid_risk_input")
            return self._decision(context, reasons, proposed_notional=proposed_notional)

        if context.environment is TradingEnvironment.LIVE:
            reasons.add("live_environment_locked")
        if context.account_suffix != context.expected_account_suffix:
            reasons.add("account_mismatch")
        if context.cohort_hash != context.expected_cohort_hash:
            reasons.add("cohort_mismatch")
        if context.provider != context.expected_provider:
            reasons.add("evidence_venue_mismatch")
        if context.feed != context.expected_feed:
            reasons.add("evidence_feed_mismatch")
        if context.frozen:
            reasons.add("global_freeze")
        if context.duplicate_order:
            reasons.add("duplicate_order")
        if context.conflicting_order:
            reasons.add("conflicting_order")
        if not context.asset_tradable:
            reasons.add("asset_not_tradable")
        if context.is_opening_short and not context.asset_shortable:
            reasons.add("asset_not_shortable")
        if context.is_opening_short and not context.asset_easy_to_borrow:
            reasons.add("asset_not_easy_to_borrow")
        if Decimal(str(context.data_age_seconds)) > self.policy.max_data_age_seconds:
            reasons.add("market_data_stale")
        if int(context.unresolved_mismatches) != 0:
            reasons.add("reconciliation_unresolved")
        if proposed_notional > buying_power:
            reasons.add("buying_power_limit")
        if (
            abs(Decimal(context.current_position_notional)) + proposed_notional
            > equity * self.policy.max_position_fraction
        ):
            reasons.add("position_limit")
        if Decimal(context.gross_exposure) + proposed_notional > equity * self.policy.max_gross_fraction:
            reasons.add("gross_exposure_limit")
        if Decimal(context.turnover_today) + proposed_notional > equity * self.policy.max_turnover_fraction:
            reasons.add("turnover_limit")
        if int(context.orders_last_minute) >= self.policy.max_orders_per_minute:
            reasons.add("order_rate_limit")
        if Decimal(context.spread_bps) > self.policy.max_spread_bps:
            reasons.add("spread_limit")
        collar_bps = abs(limit_price - reference_price) / reference_price * Decimal(10_000)
        if collar_bps > self.policy.max_price_collar_bps:
            reasons.add("price_collar")
        if Decimal(context.daily_pnl) < -(equity * self.policy.max_daily_loss_fraction):
            reasons.add("daily_loss_limit")
        if Decimal(context.drawdown_fraction) > self.policy.max_drawdown_fraction:
            reasons.add("drawdown_limit")
        return self._decision(context, reasons, proposed_notional=proposed_notional)

    def _decision(
        self,
        context: RiskContext,
        reasons: set[str],
        *,
        proposed_notional: Decimal | None,
    ) -> RiskDecision:
        limits = {
            "max_data_age_seconds": self.policy.max_data_age_seconds,
            "max_position_fraction": str(self.policy.max_position_fraction),
            "max_gross_fraction": str(self.policy.max_gross_fraction),
            "max_daily_loss_fraction": str(self.policy.max_daily_loss_fraction),
            "max_drawdown_fraction": str(self.policy.max_drawdown_fraction),
            "max_turnover_fraction": str(self.policy.max_turnover_fraction),
            "max_orders_per_minute": self.policy.max_orders_per_minute,
            "max_spread_bps": str(self.policy.max_spread_bps),
            "max_price_collar_bps": str(self.policy.max_price_collar_bps),
        }
        utilization = {
            "proposed_notional": "unavailable" if proposed_notional is None else str(proposed_notional),
            "gross_exposure": str(context.gross_exposure),
            "turnover_today": str(context.turnover_today),
            "daily_pnl": str(context.daily_pnl),
        }
        return RiskDecision(
            allowed=not reasons,
            reasons=tuple(sorted(reasons)),
            input_hash=canonical_hash(context.model_dump(mode="json")),
            policy_hash=canonical_hash(self.policy.model_dump(mode="json")),
            limits=limits,
            utilization=utilization,
        )


__all__ = ["PreTradeRiskEngine", "RiskContext", "RiskDecision", "RiskPolicy"]
