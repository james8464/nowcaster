from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update

from src.database.engine import Database
from src.database.schema import TABLES
from src.strategies.types import canonical_hash
from src.trading.live import LivePilotPolicy
from src.trading.readiness import ReadinessReceipt


class ArmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_suffix: str = Field(min_length=4, max_length=12)
    phrase: str = Field(min_length=12, max_length=80)


class TradingArm(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    arm_id: str
    account_suffix: str
    readiness_receipt_id: str
    readiness_receipt_hash: str
    effective_at: datetime
    expires_at: datetime
    limits: dict[str, str | int | bool]


class ArmingService:
    def __init__(self, database: Database, *, process_nonce: str, clock=None, policy=None):
        if not process_nonce:
            raise ValueError("process nonce is required")
        self.database = database
        self.process_nonce = process_nonce
        self.clock = clock or (lambda: datetime.now(UTC))
        self.policy = policy or LivePilotPolicy()
        self._active: TradingArm | None = None

    def arm(
        self,
        request: ArmRequest,
        *,
        account_suffix: str,
        receipt: ReadinessReceipt,
    ) -> TradingArm | None:
        now = self.clock()
        expected = f"ARM LIVE {account_suffix} LOSS {self.policy.max_daily_loss_dollars}"
        if request.account_suffix != account_suffix or request.phrase != expected:
            return None
        if not receipt.valid_at(now, cohort_hash=receipt.cohort_hash):
            return None
        expires_at = min(now + timedelta(minutes=self.policy.arm_minutes), receipt.expires_at)
        receipt_hash = canonical_hash(receipt.model_dump(mode="json"))
        limits = self.policy.model_dump(mode="json")
        arm = TradingArm(
            arm_id=canonical_hash(("live", account_suffix, receipt.receipt_id, receipt_hash, now, self.process_nonce)),
            account_suffix=account_suffix,
            readiness_receipt_id=receipt.receipt_id,
            readiness_receipt_hash=receipt_hash,
            effective_at=now,
            expires_at=expires_at,
            limits=limits,
        )
        self.database.insert(
            "trading_arms",
            [
                {
                    "arm_id": arm.arm_id,
                    "environment": "live",
                    "account_suffix": account_suffix,
                    "readiness_receipt_id": receipt.receipt_id,
                    "readiness_receipt_hash": receipt_hash,
                    "limits": limits,
                    "effective_at": now,
                    "expires_at": expires_at,
                    "status": "active",
                    "ended_at": None,
                    "terminal_reason": None,
                    "source": "nowcaster_trading",
                    "source_version": "1",
                    "created_at": now,
                }
            ],
        )
        self._active = arm
        return arm

    def current(self, *, account_suffix: str, at: datetime) -> TradingArm | None:
        arm = self._active
        if arm is None or arm.account_suffix != account_suffix or at >= arm.expires_at:
            return None
        return arm

    def disarm(self, reason: str) -> None:
        if self._active is None:
            return
        table = TABLES["trading_arms"]
        now = self.clock()
        with self.database.engine.begin() as connection:
            connection.execute(
                update(table)
                .where(table.c.arm_id == self._active.arm_id)
                .values(status="ended", ended_at=now, terminal_reason=reason)
            )
        self._active = None


__all__ = ["ArmRequest", "ArmingService", "TradingArm"]
