from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from src.backtest.execution import OrderIntent
from src.strategies.types import canonical_hash
from src.trading.alpaca import AlpacaError
from src.trading.broker import BrokerClient
from src.trading.idempotency import client_order_id
from src.trading.reconciliation import ReconciliationResult, reconcile_state
from src.trading.repository import TradingRepository
from src.trading.risk import PreTradeRiskEngine, RiskContext
from src.trading.types import BrokerOrder, BrokerOrderRequest, TradeUpdate


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    status: str
    client_order_id: str
    broker_order: BrokerOrder | None = None
    reason: str | None = None


class TradingSupervisor:
    """Serializes broker effects and freezes whenever broker truth is uncertain."""

    def __init__(
        self,
        *,
        repository: TradingRepository,
        broker: BrokerClient,
        session_id: str,
        cohort_hash: str,
        provider: str,
        feed: str,
        interval: str,
        strategy_version: str,
        code_hash: str,
        config_hash: str,
        risk_engine: PreTradeRiskEngine | None = None,
        clock=None,
    ):
        self.repository = repository
        self.broker = broker
        self.session_id = session_id
        self.cohort_hash = cohort_hash
        self.provider = provider
        self.feed = feed
        self.interval = interval
        self.strategy_version = strategy_version
        self.code_hash = code_hash
        self.config_hash = config_hash
        self.risk_engine = risk_engine
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._orders: dict[str, BrokerOrder] = {}
        self._positions: dict[str, Decimal] = {}
        self._account_suffix: str | None = None
        self._started = False
        self._frozen = False
        self._reconciliation_sequence = 0

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def ready(self) -> bool:
        return self._started and not self._frozen

    def start(self) -> ReconciliationResult:
        with self._lock:
            if self._started:
                return self.reconcile()
            account = self.broker.get_account()
            self._account_suffix = account.account_suffix
            self.repository.start_session(
                session_id=self.session_id,
                environment=self.broker.environment,
                account_suffix=account.account_suffix,
                code_hash=self.code_hash,
                config_hash=self.config_hash,
            )
            self.repository.record_account_snapshot(session_id=self.session_id, account=account)
            self._started = True
            return self._reconcile_with(account)

    def _reconcile_with(self, account) -> ReconciliationResult:
        broker_orders = self.broker.list_orders(status="open")
        broker_positions = self.broker.list_positions()
        result = reconcile_state(
            local_orders=tuple(self._orders.values()),
            broker_orders=broker_orders,
            local_positions=self._positions,
            broker_positions=broker_positions,
            account=account,
        )
        self._reconciliation_sequence += 1
        compared_at = self._clock()
        reconciliation_id = canonical_hash((self.session_id, compared_at, self._reconciliation_sequence, result.status))
        details = {
            "order_mismatches": result.order_mismatches,
            "position_mismatches": result.position_mismatches,
            "account_mismatches": result.account_mismatches,
        }
        self.repository.record_reconciliation(
            reconciliation_id=reconciliation_id,
            session_id=self.session_id,
            environment=self.broker.environment,
            account_suffix=account.account_suffix,
            compared_at=compared_at,
            open_order_mismatches=len(result.order_mismatches),
            position_mismatches=len(result.position_mismatches),
            account_mismatches=len(result.account_mismatches),
            status=result.status,
            details=details,
        )
        for position in broker_positions:
            self.repository.record_position_snapshot(
                session_id=self.session_id,
                account_suffix=account.account_suffix,
                reconciliation_id=reconciliation_id,
                position=position,
                local_quantity=self._positions.get(position.symbol, Decimal(0)),
                local_market_value=Decimal(0),
            )
        if result.unresolved_count:
            self.freeze("reconciliation_mismatch")
        return result

    def reconcile(self) -> ReconciliationResult:
        with self._lock:
            if not self._started:
                raise RuntimeError("supervisor must start before reconciliation")
            return self._reconcile_with(self.broker.get_account())

    def submit_intent(
        self,
        intent: OrderIntent,
        *,
        limit_price: Decimal,
        time_in_force: str = "day",
        extended_hours: bool = False,
        risk_context: RiskContext | None = None,
    ) -> SubmissionOutcome:
        with self._lock:
            if not self._started:
                raise RuntimeError("supervisor must start before order admission")
            assert self._account_suffix is not None
            identity = client_order_id(
                intent,
                account_suffix=self._account_suffix,
                environment=self.broker.environment,
            )
            if self._frozen:
                return SubmissionOutcome("frozen", identity, reason="supervisor_frozen")
            request = BrokerOrderRequest(
                client_order_id=identity,
                symbol=intent.symbol,
                side=intent.side,
                quantity=Decimal(str(intent.quantity)),
                order_type="limit",
                time_in_force=time_in_force,
                limit_price=limit_price,
                extended_hours=extended_hours,
            )
            intent_id = canonical_hash((self.session_id, identity))
            self.repository.record_intent(
                intent_id=intent_id,
                session_id=self.session_id,
                account_suffix=self._account_suffix,
                cohort_hash=self.cohort_hash,
                decision_hash=intent.decision_hash or canonical_hash(intent),
                provider=self.provider,
                feed=self.feed,
                interval=self.interval,
                strategy_id=intent.strategy_id,
                strategy_version=self.strategy_version,
                decision_timestamp=intent.decision_timestamp.to_pydatetime(),
                request=request,
            )
            if self.risk_engine is not None:
                if risk_context is None:
                    self.freeze("missing_risk_context")
                    return SubmissionOutcome("risk_rejected", identity, reason="invalid_risk_input")
                risk_decision = self.risk_engine.evaluate(intent, risk_context)
                self.repository.record_risk_decision(
                    session_id=self.session_id,
                    intent_id=intent_id,
                    account_suffix=self._account_suffix,
                    decision=risk_decision,
                )
                if not risk_decision.allowed:
                    return SubmissionOutcome(
                        "risk_rejected",
                        identity,
                        reason=",".join(risk_decision.reasons),
                    )
            try:
                order = self.broker.submit_order(request)
            except AlpacaError as exc:
                if exc.ambiguous:
                    try:
                        order = self.broker.get_order_by_client_id(identity)
                    except Exception:
                        self.freeze("ambiguous_submission_unresolved")
                        return SubmissionOutcome("ambiguous", identity, reason="lookup_failed")
                else:
                    self.freeze("broker_submission_failed")
                    return SubmissionOutcome("broker_rejected", identity, reason=str(exc))
            self.repository.record_submission(
                session_id=self.session_id,
                intent_id=intent_id,
                account_suffix=self._account_suffix,
                order=order,
            )
            self._orders[identity] = order
            return SubmissionOutcome("accepted", identity, broker_order=order)

    def consume_update(self, update: TradeUpdate) -> bool:
        with self._lock:
            assert self._account_suffix is not None
            inserted = self.repository.record_event(
                session_id=self.session_id,
                account_suffix=self._account_suffix,
                event=update,
            )
            current = self._orders.get(update.client_order_id)
            if current is not None and update.broker_timestamp >= current.updated_at:
                self._orders[update.client_order_id] = current.model_copy(
                    update={
                        "status": update.status,
                        "filled_quantity": update.cumulative_filled_quantity,
                        "filled_average_price": update.fill_price or current.filled_average_price,
                        "updated_at": update.broker_timestamp,
                        "received_at": update.received_at,
                    }
                )
            if not update.known_event:
                self.freeze("unknown_broker_event")
            return inserted

    def freeze(self, reason: str) -> None:
        self._frozen = True
        if self._started:
            self.broker.cancel_all_orders()

    def shutdown(self, reason: str = "operator_stop") -> None:
        with self._lock:
            if self._started:
                self.repository.finish_session(self.session_id, status="stopped", terminal_reason=reason)
                self._started = False
                self._frozen = True


__all__ = ["SubmissionOutcome", "TradingSupervisor"]
