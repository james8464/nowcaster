from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from src.database.engine import Database
from src.strategies.types import canonical_hash
from src.trading.broker import BrokerClient
from src.trading.types import BrokerOrderRequest


class FlattenConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_suffix: str = Field(min_length=4, max_length=12)
    phrase: str = Field(min_length=8, max_length=64)


@dataclass(frozen=True, slots=True)
class EmergencyOutcome:
    status: str
    canceled_orders: int
    close_orders: int
    remaining_positions: int
    reason: str | None = None


class EmergencyController:
    """Immediate freeze and separately confirmed, broker-reconciled flattening."""

    def __init__(
        self,
        *,
        database: Database,
        broker: BrokerClient,
        session_id: str | None,
        account_suffix: str,
        clock=None,
        reconciliation_attempts: int = 3,
    ):
        self.database = database
        self.broker = broker
        self.session_id = session_id or "offline-emergency"
        self.account_suffix = account_suffix
        self.clock = clock or (lambda: datetime.now(UTC))
        self.reconciliation_attempts = reconciliation_attempts
        self._frozen = False
        self._sequence = 0

    def _record(self, event: str, severity: str, details: dict[str, object]) -> None:
        self._sequence += 1
        observed_at = self.clock()
        self.database.insert(
            "trading_health_events",
            [
                {
                    "health_event_id": canonical_hash((self.session_id, event, observed_at, self._sequence)),
                    "session_id": self.session_id,
                    "environment": self.broker.environment.value,
                    "account_suffix": self.account_suffix,
                    "event": event,
                    "severity": severity,
                    "details": details,
                    "observed_at": observed_at,
                    "source": "nowcaster_trading",
                    "source_version": "1",
                    "created_at": observed_at,
                }
            ],
        )

    def freeze(self, reason: str) -> EmergencyOutcome:
        if self._frozen:
            return EmergencyOutcome("already_frozen", 0, 0, len(self.broker.list_positions()), reason)
        self._frozen = True
        try:
            canceled = self.broker.cancel_all_orders()
            status = "frozen"
        except Exception:
            canceled = 0
            status = "frozen_unresolved"
        self._record("freeze", "critical", {"reason": reason, "cancel_status": status})
        return EmergencyOutcome(status, canceled, 0, len(self.broker.list_positions()), reason)

    def flatten(self, confirmation: FlattenConfirmation) -> EmergencyOutcome:
        expected_phrase = f"FLATTEN {self.account_suffix}"
        if confirmation.account_suffix != self.account_suffix or confirmation.phrase != expected_phrase:
            return EmergencyOutcome(
                "confirmation_rejected",
                0,
                0,
                len(self.broker.list_positions()),
                "exact_account_suffix_and_phrase_required",
            )
        frozen = self.freeze("flatten_requested")
        close_orders = 0
        positions = self.broker.list_positions()
        for position in positions:
            if position.quantity == 0:
                continue
            identity = (
                "nce-"
                + canonical_hash(
                    (self.broker.environment, self.account_suffix, position.symbol, str(position.quantity))
                )[:40]
            )
            request = BrokerOrderRequest(
                client_order_id=identity,
                symbol=position.symbol,
                side="sell" if position.quantity > 0 else "buy",
                quantity=abs(position.quantity),
                order_type="limit",
                time_in_force="day",
                limit_price=position.current_price,
                extended_hours=False,
            )
            try:
                self.broker.submit_order(request)
                close_orders += 1
            except Exception:
                self._record("flatten_close_failed", "critical", {"symbol": position.symbol})
        remaining = positions
        for _ in range(self.reconciliation_attempts):
            remaining = self.broker.list_positions()
            if not remaining:
                self._record("flatten_complete", "critical", {"close_orders": close_orders})
                return EmergencyOutcome("flattened", frozen.canceled_orders, close_orders, 0)
        self._record(
            "flatten_unresolved",
            "critical",
            {"close_orders": close_orders, "remaining_positions": len(remaining)},
        )
        return EmergencyOutcome(
            "unresolved",
            frozen.canceled_orders,
            close_orders,
            len(remaining),
            "broker_positions_remain",
        )


__all__ = ["EmergencyController", "EmergencyOutcome", "FlattenConfirmation"]
