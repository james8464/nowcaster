from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select, update

from src.database.engine import Database
from src.database.schema import TABLES
from src.strategies.types import canonical_hash
from src.trading.risk import RiskDecision
from src.trading.types import (
    BrokerAccount,
    BrokerOrder,
    BrokerOrderRequest,
    BrokerPosition,
    ExecutionObservation,
    TradeUpdate,
    TradingEnvironment,
)


class TradingRepository:
    """Transactional persistence for broker effects and their audit evidence."""

    def __init__(self, database: Database, *, clock: Callable[[], datetime] | None = None):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is not UTC:
            raise ValueError("repository clock must return an explicit UTC datetime")
        return value

    def _common(self) -> dict[str, Any]:
        return {
            "source": "nowcaster_trading",
            "source_version": "1",
            "created_at": self._now(),
        }

    def _session_environment(self, session_id: str) -> TradingEnvironment:
        table = TABLES["broker_sessions"]
        with self.database.engine.connect() as connection:
            value = connection.execute(
                select(table.c.environment).where(table.c.session_id == session_id)
            ).scalar_one_or_none()
        if value is None:
            raise ValueError(f"unknown broker session: {session_id}")
        return TradingEnvironment(value)

    def start_session(
        self,
        *,
        session_id: str,
        environment: TradingEnvironment,
        account_suffix: str,
        code_hash: str,
        config_hash: str,
    ) -> None:
        now = self._now()
        self.database.insert(
            "broker_sessions",
            [
                {
                    "session_id": session_id,
                    "environment": environment.value,
                    "account_suffix": account_suffix,
                    "code_hash": code_hash,
                    "config_hash": config_hash,
                    "started_at": now,
                    "ended_at": None,
                    "last_heartbeat_at": now,
                    "status": "starting",
                    "terminal_reason": None,
                    **self._common(),
                }
            ],
        )

    def finish_session(self, session_id: str, *, status: str, terminal_reason: str) -> None:
        table = TABLES["broker_sessions"]
        now = self._now()
        with self.database.engine.begin() as connection:
            exists = connection.execute(
                select(table.c.session_id).where(table.c.session_id == session_id)
            ).scalar_one_or_none()
            if exists is None:
                raise ValueError(f"unknown broker session: {session_id}")
            connection.execute(
                update(table)
                .where(table.c.session_id == session_id)
                .values(
                    ended_at=now,
                    last_heartbeat_at=now,
                    status=status,
                    terminal_reason=terminal_reason,
                )
            )

    def record_intent(
        self,
        *,
        intent_id: str,
        session_id: str,
        account_suffix: str,
        cohort_hash: str,
        decision_hash: str,
        provider: str,
        feed: str,
        interval: str,
        strategy_id: str,
        strategy_version: str,
        decision_timestamp: datetime,
        request: BrokerOrderRequest,
    ) -> None:
        environment = self._session_environment(session_id)
        self.database.insert(
            "broker_order_intents",
            [
                {
                    "intent_id": intent_id,
                    "session_id": session_id,
                    "environment": environment.value,
                    "account_suffix": account_suffix,
                    "cohort_hash": cohort_hash,
                    "decision_hash": decision_hash,
                    "provider": provider,
                    "feed": feed,
                    "symbol": request.symbol,
                    "interval": interval,
                    "strategy_id": strategy_id,
                    "strategy_version": strategy_version,
                    "decision_timestamp": decision_timestamp,
                    "client_order_id": request.client_order_id,
                    "request": request.model_dump(mode="json"),
                    "status": "recorded",
                    **self._common(),
                }
            ],
        )

    def record_submission(
        self,
        *,
        session_id: str,
        intent_id: str,
        account_suffix: str,
        order: BrokerOrder,
    ) -> None:
        environment = self._session_environment(session_id)
        if order.environment != environment:
            raise ValueError("broker order environment does not match its session")
        order_record_id = canonical_hash(
            {
                "environment": environment,
                "account_suffix": account_suffix,
                "broker_order_id": order.broker_order_id,
            }
        )
        request = {
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "side": order.side,
            "quantity": str(order.quantity),
            "order_type": order.order_type,
            "time_in_force": order.time_in_force,
            "limit_price": str(order.limit_price),
        }
        self.database.insert(
            "broker_orders",
            [
                {
                    "order_record_id": order_record_id,
                    "session_id": session_id,
                    "intent_id": intent_id,
                    "environment": environment.value,
                    "account_suffix": account_suffix,
                    "broker_order_id": order.broker_order_id,
                    "client_order_id": order.client_order_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": order.quantity,
                    "filled_quantity": order.filled_quantity,
                    "limit_price": order.limit_price,
                    "filled_average_price": order.filled_average_price,
                    "status": order.status.value,
                    "submitted_at": order.submitted_at,
                    "updated_at": order.updated_at,
                    "received_at": order.received_at,
                    "request": request,
                    **self._common(),
                }
            ],
        )

    def record_event(self, *, session_id: str, account_suffix: str, event: TradeUpdate) -> bool:
        environment = self._session_environment(session_id)
        payload = event.model_dump(mode="json")
        event_hash = canonical_hash(payload)
        event_identity = event.event_id or canonical_hash(
            {
                "broker_order_id": event.broker_order_id,
                "event": event.event,
                "broker_timestamp": event.broker_timestamp,
                "raw_payload_hash": event.raw_payload_hash,
            }
        )
        table = TABLES["broker_order_events"]
        with self.database.engine.begin() as connection:
            existing = connection.execute(
                select(table.c.event_hash).where(table.c.event_identity == event_identity)
            ).scalar_one_or_none()
            if existing is not None:
                if existing != event_hash:
                    raise ValueError("conflicting broker event for one event identity")
                return False
            connection.execute(
                insert(table).values(
                    order_event_id=canonical_hash((environment, account_suffix, event_identity)),
                    event_identity=event_identity,
                    event_hash=event_hash,
                    session_id=session_id,
                    environment=environment.value,
                    account_suffix=account_suffix,
                    broker_order_id=event.broker_order_id,
                    client_order_id=event.client_order_id,
                    event=event.event,
                    known_event=event.known_event,
                    status=event.status.value,
                    symbol=event.symbol,
                    side=event.side,
                    quantity=event.quantity,
                    fill_price=event.fill_price,
                    cumulative_filled_quantity=event.cumulative_filled_quantity,
                    broker_timestamp=event.broker_timestamp,
                    received_at=event.received_at,
                    raw_payload_hash=event.raw_payload_hash,
                    payload=payload,
                    **self._common(),
                )
            )
        return True

    def record_execution_observation(self, observation: ExecutionObservation) -> bool:
        """Append one immutable simulator-versus-broker comparison."""
        self._session_environment(observation.session_id)
        intent_table = TABLES["broker_order_intents"]
        order_table = TABLES["broker_orders"]
        table = TABLES["execution_observations"]
        payload = observation.model_dump(mode="json")
        payload_hash = canonical_hash(payload)
        with self.database.engine.begin() as connection:
            intent = connection.execute(
                select(intent_table.c.cohort_hash).where(
                    intent_table.c.intent_id == observation.intent_id,
                    intent_table.c.session_id == observation.session_id,
                )
            ).scalar_one_or_none()
            if intent is None:
                raise ValueError("execution observation requires a recorded order intent")
            if intent != observation.cohort_hash:
                raise ValueError("execution observation cohort does not match its order intent")
            order_exists = connection.execute(
                select(order_table.c.order_record_id).where(
                    order_table.c.intent_id == observation.intent_id,
                    order_table.c.broker_order_id == observation.broker_order_id,
                )
            ).scalar_one_or_none()
            if order_exists is None:
                raise ValueError("execution observation requires a recorded broker order")
            existing = connection.execute(
                select(table.c.payload_hash).where(table.c.observation_id == observation.observation_id)
            ).scalar_one_or_none()
            if existing is not None:
                if existing != payload_hash:
                    raise ValueError("conflicting execution observation for one observation identity")
                return False
            connection.execute(
                insert(table).values(
                    observation_id=observation.observation_id,
                    payload_hash=payload_hash,
                    session_id=observation.session_id,
                    cohort_hash=observation.cohort_hash,
                    intent_id=observation.intent_id,
                    broker_order_id=observation.broker_order_id,
                    symbol=observation.symbol,
                    side=observation.side,
                    decision_at=observation.decision_at,
                    submitted_at=observation.submitted_at,
                    first_fill_at=observation.first_fill_at,
                    terminal_at=observation.terminal_at,
                    requested_quantity=observation.requested_quantity,
                    filled_quantity=observation.filled_quantity,
                    predicted_total_cost_bps=observation.predicted_execution_cost_bps,
                    realized_total_cost_bps=observation.realized_execution_cost_bps,
                    predicted_latency_ms=observation.predicted_latency_ms,
                    realized_latency_ms=observation.realized_latency_ms,
                    missed_fill=observation.missed_fill,
                    observed_at=observation.observed_at,
                    observation=payload,
                    **self._common(),
                )
            )
        return True

    def record_account_snapshot(self, *, session_id: str, account: BrokerAccount) -> None:
        environment = self._session_environment(session_id)
        snapshot_id = canonical_hash(
            {
                "session_id": session_id,
                "account_suffix": account.account_suffix,
                "received_at": account.received_at,
            }
        )
        self.database.insert(
            "broker_account_snapshots",
            [
                {
                    "account_snapshot_id": snapshot_id,
                    "session_id": session_id,
                    "environment": environment.value,
                    "account_suffix": account.account_suffix,
                    "status": account.status,
                    "equity": account.equity,
                    "buying_power": account.buying_power,
                    "trading_blocked": account.trading_blocked,
                    "pattern_day_trader": account.pattern_day_trader,
                    "shorting_enabled": account.shorting_enabled,
                    "received_at": account.received_at,
                    **self._common(),
                }
            ],
        )

    def record_risk_decision(
        self,
        *,
        session_id: str,
        intent_id: str,
        account_suffix: str,
        decision: RiskDecision,
    ) -> None:
        environment = self._session_environment(session_id)
        decided_at = self._now()
        self.database.insert(
            "risk_decisions",
            [
                {
                    "risk_decision_id": canonical_hash((intent_id, decision.input_hash, decision.policy_hash)),
                    "session_id": session_id,
                    "intent_id": intent_id,
                    "environment": environment.value,
                    "account_suffix": account_suffix,
                    "input_hash": decision.input_hash,
                    "policy_hash": decision.policy_hash,
                    "allowed": decision.allowed,
                    "reasons": list(decision.reasons),
                    "limits": decision.limits,
                    "utilization": decision.utilization,
                    "decided_at": decided_at,
                    **self._common(),
                }
            ],
        )

    def record_position_snapshot(
        self,
        *,
        session_id: str,
        account_suffix: str,
        reconciliation_id: str,
        position: BrokerPosition,
        local_quantity: Decimal,
        local_market_value: Decimal,
    ) -> None:
        environment = self._session_environment(session_id)
        mismatch_state = (
            "matched"
            if position.quantity == local_quantity and position.market_value == local_market_value
            else "mismatch"
        )
        self.database.insert(
            "broker_positions",
            [
                {
                    "position_snapshot_id": canonical_hash((reconciliation_id, position.symbol)),
                    "reconciliation_id": reconciliation_id,
                    "session_id": session_id,
                    "environment": environment.value,
                    "account_suffix": account_suffix,
                    "symbol": position.symbol,
                    "broker_quantity": position.quantity,
                    "local_quantity": local_quantity,
                    "broker_market_value": position.market_value,
                    "local_market_value": local_market_value,
                    "average_entry_price": position.average_entry_price,
                    "current_price": position.current_price,
                    "unrealized_pnl": position.unrealized_pnl,
                    "mismatch_state": mismatch_state,
                    "received_at": position.received_at,
                    **self._common(),
                }
            ],
        )

    def record_reconciliation(
        self,
        *,
        reconciliation_id: str,
        session_id: str,
        environment: TradingEnvironment,
        account_suffix: str,
        compared_at: datetime,
        open_order_mismatches: int,
        position_mismatches: int,
        account_mismatches: int,
        status: str,
        details: Mapping[str, Any],
    ) -> None:
        session_environment = self._session_environment(session_id)
        if environment != session_environment:
            raise ValueError("reconciliation environment does not match its session")
        unresolved = open_order_mismatches + position_mismatches + account_mismatches
        self.database.insert(
            "reconciliation_runs",
            [
                {
                    "reconciliation_id": reconciliation_id,
                    "session_id": session_id,
                    "environment": environment.value,
                    "account_suffix": account_suffix,
                    "compared_at": compared_at,
                    "open_order_mismatches": open_order_mismatches,
                    "position_mismatches": position_mismatches,
                    "account_mismatches": account_mismatches,
                    "unresolved_mismatch_count": unresolved,
                    "status": status,
                    "details": dict(details),
                    **self._common(),
                }
            ],
        )


__all__ = ["TradingRepository"]
