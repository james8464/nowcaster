from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.trading.alpaca import AlpacaCredentials, AlpacaTradingClient
from src.trading.readiness import ReadinessReceipt
from src.trading.types import TradingEnvironment


class LivePilotPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_position_dollars: Decimal = Field(default=Decimal("100"), gt=0)
    max_position_fraction: Decimal = Field(default=Decimal("0.001"), gt=0)
    max_gross_dollars: Decimal = Field(default=Decimal("500"), gt=0)
    max_gross_fraction: Decimal = Field(default=Decimal("0.005"), gt=0)
    max_daily_loss_dollars: Decimal = Field(default=Decimal("25"), gt=0)
    max_daily_loss_fraction: Decimal = Field(default=Decimal("0.0005"), gt=0)
    arm_minutes: int = Field(default=30, ge=1)
    extended_hours: bool = False
    overnight_equities: bool = False
    entry_order_type: str = "marketable_limit"

    @model_validator(mode="after")
    def cannot_raise_hard_caps(self) -> LivePilotPolicy:
        ceilings = {
            "max_position_dollars": Decimal("100"),
            "max_position_fraction": Decimal("0.001"),
            "max_gross_dollars": Decimal("500"),
            "max_gross_fraction": Decimal("0.005"),
            "max_daily_loss_dollars": Decimal("25"),
            "max_daily_loss_fraction": Decimal("0.0005"),
        }
        if any(getattr(self, name) > ceiling for name, ceiling in ceilings.items()):
            raise ValueError("live policy cannot exceed immutable pilot ceilings")
        if self.arm_minutes > 30:
            raise ValueError("live arm cannot exceed 30 minutes")
        if self.extended_hours or self.overnight_equities or self.entry_order_type != "marketable_limit":
            raise ValueError("live pilot execution posture cannot be loosened")
        return self


class LiveLockContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: str
    credential_environment: str
    account_suffix: str
    expected_account_suffix: str
    cohort_hash: str
    readiness_receipt: ReadinessReceipt | None
    engine_identity_verified: bool
    signature_posture: str
    arm_expires_at: datetime | None
    reconciliation_mismatches: int
    health_breakers: int
    now: datetime


class LiveLockedError(RuntimeError):
    pass


class LiveBrokerFactory:
    def __init__(self, policy: LivePilotPolicy | None = None):
        self.policy = policy or LivePilotPolicy()

    def _reasons(self, context: LiveLockContext) -> tuple[str, ...]:
        reasons: list[str] = []
        if context.environment != "live":
            reasons.append("live_environment_required")
        if context.credential_environment != "live":
            reasons.append("live_credentials_required")
        if context.account_suffix != context.expected_account_suffix:
            reasons.append("account_mismatch")
        receipt = context.readiness_receipt
        if receipt is None:
            reasons.append("readiness_receipt_required")
        elif not receipt.valid_at(context.now, cohort_hash=context.cohort_hash):
            reasons.append("readiness_receipt_invalid")
        if not context.engine_identity_verified:
            reasons.append("engine_identity_required")
        if context.signature_posture != "production":
            reasons.append("production_signature_required")
        if context.arm_expires_at is None or context.arm_expires_at <= context.now:
            reasons.append("manual_arm_required")
        elif context.arm_expires_at > context.now + timedelta(minutes=self.policy.arm_minutes):
            reasons.append("manual_arm_duration_invalid")
        if context.reconciliation_mismatches:
            reasons.append("reconciliation_unresolved")
        if context.health_breakers:
            reasons.append("health_breaker_active")
        return tuple(sorted(reasons))

    def create(
        self,
        context: LiveLockContext,
        credentials: AlpacaCredentials,
        *,
        client: httpx.Client | None = None,
    ) -> AlpacaTradingClient:
        reasons = self._reasons(context)
        if reasons:
            raise LiveLockedError("live locked: " + ",".join(reasons))
        return AlpacaTradingClient(
            credentials,
            client=client,
            _environment=TradingEnvironment.LIVE,
        )


__all__ = ["LiveBrokerFactory", "LiveLockContext", "LiveLockedError", "LivePilotPolicy"]
