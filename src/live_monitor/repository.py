from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, update

from src.database.engine import Database
from src.database.schema import TABLES
from src.live_monitor.types import AlertState, LifecycleTransition, TradePlan
from src.strategies.types import canonical_hash


@dataclass(frozen=True, slots=True)
class RecoveredSetup:
    setup_id: str
    plan: TradePlan
    state: AlertState
    delivered_event_ids: tuple[str, ...]


class LiveMonitorRepository:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] | None = None):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is not UTC:
            raise ValueError("repository clock must return an explicit UTC datetime")
        return value

    def _common(self) -> dict[str, Any]:
        return {"source": "nowcaster_live_monitor", "source_version": "1", "created_at": self._now()}

    def start_session(self, session_id: str, *, config_hash: str, cohort_hash: str) -> None:
        now = self._now()
        self.database.insert(
            "monitor_sessions",
            [
                {
                    "session_id": session_id,
                    "config_hash": config_hash,
                    "cohort_hash": cohort_hash,
                    "started_at": now,
                    "ended_at": None,
                    "status": "warming",
                    "terminal_reason": None,
                    **self._common(),
                }
            ],
        )

    def finish_session(self, session_id: str, *, reason: str) -> None:
        table = TABLES["monitor_sessions"]
        with self.database.engine.begin() as connection:
            connection.execute(
                update(table)
                .where(table.c.session_id == session_id)
                .values(ended_at=self._now(), status="stopped", terminal_reason=reason)
            )

    def create_setup(self, session_id: str, setup_id: str, plan: TradePlan) -> None:
        self.database.insert(
            "monitor_setups",
            [
                {
                    "setup_id": setup_id,
                    "session_id": session_id,
                    "provider": plan.provider,
                    "feed": plan.feed,
                    "symbol": plan.symbol,
                    "interval": plan.decision_interval,
                    "plan": plan.model_dump(mode="json"),
                    "current_state": AlertState.WATCHING.value,
                    **self._common(),
                }
            ],
        )

    def record_transition(self, transition: LifecycleTransition) -> bool:
        table = TABLES["monitor_transitions"]
        setup_table = TABLES["monitor_setups"]
        payload = transition.model_dump(mode="json")
        payload_hash = canonical_hash(payload)
        with self.database.engine.begin() as connection:
            existing = connection.execute(
                select(table.c.payload_hash).where(table.c.event_id == transition.event_id)
            ).scalar_one_or_none()
            if existing is not None:
                if existing != payload_hash:
                    raise ValueError("conflicting monitor event identity")
                return False
            connection.execute(
                insert(table).values(
                    transition_id=transition.transition_id,
                    event_id=transition.event_id,
                    setup_id=transition.setup_id,
                    from_state=transition.from_state.value,
                    to_state=transition.to_state.value,
                    occurred_at=transition.occurred_at,
                    reason=transition.reason,
                    actual_fill=transition.actual_fill,
                    payload_hash=payload_hash,
                    payload=payload,
                    **self._common(),
                )
            )
            connection.execute(
                update(setup_table)
                .where(setup_table.c.setup_id == transition.setup_id)
                .values(current_state=transition.to_state.value)
            )
        return True

    def record_notification_receipt(self, *, event_id: str, status: str = "delivered") -> bool:
        table = TABLES["monitor_notification_receipts"]
        with self.database.engine.begin() as connection:
            if connection.execute(select(table.c.event_id).where(table.c.event_id == event_id)).scalar_one_or_none():
                return False
            connection.execute(
                insert(table).values(
                    receipt_id=canonical_hash((event_id, status)),
                    event_id=event_id,
                    delivered_at=self._now(),
                    status=status,
                    **self._common(),
                )
            )
        return True

    def recover_active(self) -> tuple[RecoveredSetup, ...]:
        setup_table = TABLES["monitor_setups"]
        receipt_table = TABLES["monitor_notification_receipts"]
        terminal = {
            state.value
            for state in (
                AlertState.TARGET_2,
                AlertState.STOPPED,
                AlertState.CLOSED,
                AlertState.INVALIDATED,
                AlertState.EXPIRED,
            )
        }
        with self.database.engine.connect() as connection:
            setup_rows = connection.execute(select(setup_table).order_by(setup_table.c.created_at)).mappings().all()
            delivered = tuple(
                connection.execute(select(receipt_table.c.event_id).order_by(receipt_table.c.delivered_at)).scalars()
            )
        return tuple(
            RecoveredSetup(
                setup_id=str(row["setup_id"]),
                plan=TradePlan.model_validate(row["plan"]),
                state=AlertState(str(row["current_state"])),
                delivered_event_ids=delivered,
            )
            for row in setup_rows
            if str(row["current_state"]) not in terminal
        )


__all__ = ["LiveMonitorRepository", "RecoveredSetup"]
