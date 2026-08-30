from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select, update

from src.database.engine import Database
from src.database.schema import TABLES
from src.live_monitor.types import (
    AlertState,
    GranularMarketEvent,
    LifecycleTransition,
    MarketBar,
    MarketDepth,
    MarketQuote,
    MarketStatusEvent,
    MarketTrade,
    ProviderHealthEvent,
    TradePlan,
)
from src.strategies.types import canonical_hash


@dataclass(frozen=True, slots=True)
class RecoveredSetup:
    setup_id: str
    plan: TradePlan
    state: AlertState
    delivered_event_ids: tuple[str, ...]
    actual_fill: Decimal | None


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

    def record_finalized_bar(self, session_id: str, bar: MarketBar) -> bool:
        identity = canonical_hash((session_id, bar.bar_id))
        table = TABLES["monitor_finalized_bars"]
        with self.database.engine.begin() as connection:
            if connection.execute(select(table.c.bar_id).where(table.c.bar_id == identity)).scalar_one_or_none():
                return False
            connection.execute(
                insert(table).values(
                    bar_id=identity,
                    session_id=session_id,
                    provider=bar.provider,
                    feed=bar.feed,
                    symbol=bar.symbol,
                    interval=bar.interval,
                    start_at=bar.start,
                    end_at=bar.end,
                    revision=bar.revision,
                    payload={**bar.model_dump(mode="json"), "source_bar_id": bar.bar_id},
                    **self._common(),
                )
            )
        return True

    def record_market_event(self, session_id: str, event: GranularMarketEvent) -> bool:
        payload = event.model_dump(mode="json")
        payload_hash = canonical_hash(payload)
        source_event_id = event.event_id
        identity = canonical_hash((session_id, source_event_id))
        event_type = {
            MarketQuote: "quote",
            MarketTrade: "trade",
            MarketDepth: "depth",
            MarketStatusEvent: event.kind if isinstance(event, MarketStatusEvent) else "status",
        }[type(event)]
        table = TABLES["live_market_events"]
        with self.database.engine.begin() as connection:
            existing = connection.execute(
                select(table.c.payload_hash).where(table.c.event_id == identity)
            ).scalar_one_or_none()
            if existing is not None:
                if existing != payload_hash:
                    raise ValueError("conflicting live market event identity")
                return False
            connection.execute(
                insert(table).values(
                    event_id=identity,
                    session_id=session_id,
                    source_event_id=source_event_id,
                    provider=event.provider,
                    feed=event.feed,
                    symbol=event.symbol,
                    event_type=event_type,
                    provider_time=event.provider_time,
                    received_at=event.received_at,
                    processed_at=event.processed_at,
                    sequence=event.sequence,
                    payload_hash=payload_hash,
                    payload=payload,
                    **self._common(),
                )
            )
        return True

    def record_decision(self, session_id: str, payload: dict[str, Any]) -> bool:
        status = str(payload.get("status", ""))
        contextual_hash = payload.get("contextual_evidence_hash")
        contextual_payload = payload.get("contextual_evidence")
        if status in {"long", "short"} and (
            not isinstance(contextual_hash, str)
            or not isinstance(contextual_payload, dict)
            or canonical_hash(contextual_payload) != contextual_hash
        ):
            raise ValueError("actionable monitor decisions require authenticated contextual evidence")
        if isinstance(contextual_hash, str) and (
            not isinstance(contextual_payload, dict) or canonical_hash(contextual_payload) != contextual_hash
        ):
            raise ValueError("monitor contextual evidence hash mismatch")
        identity = canonical_hash((session_id, payload))
        table = TABLES["monitor_decisions"]
        with self.database.engine.begin() as connection:
            if connection.execute(
                select(table.c.decision_id).where(table.c.decision_id == identity)
            ).scalar_one_or_none():
                return False
            connection.execute(
                insert(table).values(
                    decision_id=identity,
                    session_id=session_id,
                    provider=str(payload["provider"]),
                    feed=str(payload["feed"]),
                    symbol=str(payload["symbol"]),
                    interval=str(payload["interval"]),
                    decision_time=datetime.fromisoformat(str(payload["decision_time"]).replace("Z", "+00:00")),
                    status=str(payload["status"]),
                    reasons=list(payload.get("reasons", [])),
                    evidence=payload,
                    **self._common(),
                )
            )
        return True

    def record_health_event(self, session_id: str, event: ProviderHealthEvent) -> bool:
        payload = event.model_dump(mode="json")
        identity = canonical_hash((session_id, payload))
        table = TABLES["monitor_health_events"]
        with self.database.engine.begin() as connection:
            if connection.execute(
                select(table.c.health_event_id).where(table.c.health_event_id == identity)
            ).scalar_one_or_none():
                return False
            connection.execute(
                insert(table).values(
                    health_event_id=identity,
                    session_id=session_id,
                    provider=event.provider,
                    feed=event.feed,
                    status=event.status.value,
                    reason=event.reason,
                    occurred_at=event.occurred_at,
                    details=payload,
                    **self._common(),
                )
            )
        return True

    def latest_finalized_ends(self, scopes: set[tuple[str, str, str]]) -> dict[tuple[str, str, str], datetime]:
        if not scopes:
            return {}
        frame = self.database.frame(
            "select provider, feed, symbol, max(end_at) as end_at from monitor_finalized_bars "
            "where interval = '1m' group by provider, feed, symbol"
        )
        result: dict[tuple[str, str, str], datetime] = {}
        for row in frame.itertuples(index=False):
            scope = (str(row.provider), str(row.feed), str(row.symbol))
            if scope not in scopes:
                continue
            value = row.end_at.to_pydatetime() if hasattr(row.end_at, "to_pydatetime") else row.end_at
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            result[scope] = value.astimezone(UTC)
        return result

    def recover_active(
        self,
        *,
        provider_feeds: set[tuple[str, str]],
        symbols: set[str],
        interval: str,
        config_hash: str,
        cohort_ids: set[str],
        now: datetime,
    ) -> tuple[RecoveredSetup, ...]:
        if now.tzinfo is not UTC:
            raise ValueError("recovery time must be explicit UTC")
        if not cohort_ids:
            return ()
        setup_table = TABLES["monitor_setups"]
        transition_table = TABLES["monitor_transitions"]
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
            transition_rows = connection.execute(
                select(
                    transition_table.c.setup_id,
                    transition_table.c.actual_fill,
                    transition_table.c.occurred_at,
                )
                .where(transition_table.c.actual_fill.is_not(None))
                .order_by(transition_table.c.occurred_at)
            ).all()
        fills = {str(row.setup_id): Decimal(str(row.actual_fill)) for row in transition_rows}
        recovered: list[RecoveredSetup] = []
        for row in setup_rows:
            plan = TradePlan.model_validate(row["plan"])
            state = AlertState(str(row["current_state"]))
            if (
                state.value in terminal
                or (plan.provider, plan.feed) not in provider_feeds
                or plan.symbol not in symbols
                or plan.decision_interval != interval
                or plan.config_hash != config_hash
                or plan.cohort_id not in cohort_ids
                or plan.expires_at <= now
            ):
                continue
            recovered.append(
                RecoveredSetup(
                    setup_id=str(row["setup_id"]),
                    plan=plan,
                    state=state,
                    delivered_event_ids=delivered,
                    actual_fill=fills.get(str(row["setup_id"])),
                )
            )
        return tuple(recovered)


__all__ = ["LiveMonitorRepository", "RecoveredSetup"]
