from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text

from src.database.engine import Database
from src.trading.repository import TradingRepository
from src.trading.types import (
    BrokerAccount,
    BrokerOrder,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerPosition,
    ExecutionObservation,
    TradeUpdate,
    TradingEnvironment,
)

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


def _database(tmp_path) -> Database:
    database = Database.from_url(f"duckdb:///{tmp_path / 'trading.duckdb'}")
    database.initialize()
    return database


def _request() -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id="nc1p-abc12345",
        symbol="AAPL",
        side="buy",
        quantity="1",
        order_type="limit",
        time_in_force="day",
        limit_price="190.50",
    )


def _order() -> BrokerOrder:
    return BrokerOrder(
        broker_order_id="broker-order-1",
        client_order_id="nc1p-abc12345",
        environment=TradingEnvironment.PAPER,
        symbol="AAPL",
        side="buy",
        quantity="1",
        filled_quantity="0.5",
        order_type="limit",
        time_in_force="day",
        limit_price="190.50",
        filled_average_price="190.40",
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        submitted_at=NOW,
        updated_at=NOW,
        received_at=NOW,
    )


def _event(*, fill_price: str = "190.40") -> TradeUpdate:
    return TradeUpdate(
        event_id="execution-1",
        event="partial_fill",
        known_event=True,
        broker_order_id="broker-order-1",
        client_order_id="nc1p-abc12345",
        status=BrokerOrderStatus.PARTIALLY_FILLED,
        symbol="AAPL",
        side="buy",
        quantity="0.5",
        fill_price=fill_price,
        cumulative_filled_quantity="0.5",
        broker_timestamp=NOW,
        received_at=NOW,
        raw_payload_hash="a" * 64,
    )


def _execution_observation(**updates) -> ExecutionObservation:
    values = dict(
        observation_id="f" * 64,
        session_id="session-1",
        cohort_hash="d" * 64,
        intent_id="intent-1",
        broker_order_id="broker-order-1",
        symbol="AAPL",
        side="buy",
        decision_at=NOW,
        submitted_at=NOW,
        first_fill_at=NOW,
        terminal_at=NOW,
        requested_quantity=Decimal("1"),
        filled_quantity=Decimal("0.5"),
        reference_price=Decimal("190.35"),
        predicted_fill_price=Decimal("190.39"),
        realized_fill_price=Decimal("190.40"),
        predicted_spread_bps=Decimal("1"),
        realized_spread_bps=Decimal("1.2"),
        predicted_slippage_bps=Decimal("1"),
        realized_slippage_bps=Decimal("1.3"),
        predicted_impact_bps=Decimal("0.1"),
        realized_impact_bps=Decimal("0.2"),
        predicted_latency_ms=Decimal("50"),
        realized_latency_ms=Decimal("100"),
        observed_at=NOW,
    )
    values.update(updates)
    return ExecutionObservation(**values)


def test_v4_database_migrates_to_v5_idempotently_without_altering_trading_tables(tmp_path) -> None:
    path = tmp_path / "v4.duckdb"
    database = Database.from_url(f"duckdb:///{path}")
    database.initialize()
    with database.engine.begin() as connection:
        connection.execute(text("DELETE FROM schema_versions WHERE version = 6"))
        connection.execute(text("INSERT OR IGNORE INTO schema_versions VALUES (4, CURRENT_TIMESTAMP)"))

    database.initialize()
    database.initialize()

    assert database.schema_version() == 12
    assert database.scalar("SELECT count(*) FROM schema_versions WHERE version = 12") == 1
    assert {"strategy_runs", "broker_sessions", "broker_order_events", "readiness_receipts"} <= set(
        database.table_names()
    )


def test_repository_persists_complete_paper_lifecycle_without_secret_columns(tmp_path) -> None:
    database = _database(tmp_path)
    repository = TradingRepository(database, clock=lambda: NOW)
    repository.start_session(
        session_id="session-1",
        environment=TradingEnvironment.PAPER,
        account_suffix="1234",
        code_hash="b" * 64,
        config_hash="c" * 64,
    )
    repository.record_intent(
        intent_id="intent-1",
        session_id="session-1",
        account_suffix="1234",
        cohort_hash="d" * 64,
        decision_hash="e" * 64,
        provider="alpaca",
        feed="iex",
        interval="5m",
        strategy_id="ema_adx_trend",
        strategy_version="1.0.0",
        decision_timestamp=NOW,
        request=_request(),
    )
    repository.record_submission(session_id="session-1", intent_id="intent-1", account_suffix="1234", order=_order())
    assert repository.record_event(session_id="session-1", account_suffix="1234", event=_event()) is True
    repository.record_account_snapshot(
        session_id="session-1",
        account=BrokerAccount(
            account_id="account-uuid",
            account_suffix="1234",
            status="ACTIVE",
            equity="100000",
            buying_power="200000",
            trading_blocked=False,
            pattern_day_trader=False,
            shorting_enabled=True,
            received_at=NOW,
        ),
    )
    repository.record_position_snapshot(
        session_id="session-1",
        account_suffix="1234",
        reconciliation_id="reconciliation-1",
        position=BrokerPosition(
            symbol="AAPL",
            quantity="0.5",
            market_value="95.20",
            average_entry_price="190.40",
            current_price="190.40",
            unrealized_pnl="0",
            received_at=NOW,
        ),
        local_quantity=Decimal("0.5"),
        local_market_value=Decimal("95.20"),
    )
    repository.record_reconciliation(
        reconciliation_id="reconciliation-1",
        session_id="session-1",
        environment=TradingEnvironment.PAPER,
        account_suffix="1234",
        compared_at=NOW,
        open_order_mismatches=0,
        position_mismatches=0,
        account_mismatches=0,
        status="matched",
        details={},
    )
    repository.finish_session("session-1", status="stopped", terminal_reason="operator_stop")

    assert database.scalar("select count(*) from broker_sessions") == 1
    assert database.scalar("select count(*) from broker_order_intents") == 1
    assert database.scalar("select count(*) from broker_orders") == 1
    assert database.scalar("select count(*) from broker_order_events") == 1
    assert database.scalar("select count(*) from broker_account_snapshots") == 1
    assert database.scalar("select count(*) from broker_positions") == 1
    assert database.scalar("select count(*) from reconciliation_runs") == 1
    forbidden = {"secret", "api_key", "credential", "authorization", "token", "account_id"}
    inspector = inspect(database.engine)
    for table_name in database.table_names():
        if table_name.startswith("broker_") or table_name in {
            "risk_decisions",
            "reconciliation_runs",
            "trading_health_events",
            "forward_evidence_daily",
            "readiness_receipts",
            "trading_arms",
        }:
            columns = {column["name"].lower() for column in inspector.get_columns(table_name)}
            assert columns.isdisjoint(forbidden)


def test_trade_event_is_idempotent_but_conflicting_payload_fails(tmp_path) -> None:
    database = _database(tmp_path)
    repository = TradingRepository(database, clock=lambda: NOW)
    repository.start_session(
        session_id="session-1",
        environment=TradingEnvironment.PAPER,
        account_suffix="1234",
        code_hash="b" * 64,
        config_hash="c" * 64,
    )

    assert repository.record_event(session_id="session-1", account_suffix="1234", event=_event()) is True
    assert repository.record_event(session_id="session-1", account_suffix="1234", event=_event()) is False
    with pytest.raises(ValueError, match="conflicting broker event"):
        repository.record_event(
            session_id="session-1",
            account_suffix="1234",
            event=_event(fill_price="191.00"),
        )


def test_execution_observation_ledger_is_idempotent_and_conflicts_fail(tmp_path) -> None:
    database = _database(tmp_path)
    repository = TradingRepository(database, clock=lambda: NOW)
    repository.start_session(
        session_id="session-1",
        environment=TradingEnvironment.PAPER,
        account_suffix="1234",
        code_hash="b" * 64,
        config_hash="c" * 64,
    )
    repository.record_intent(
        intent_id="intent-1",
        session_id="session-1",
        account_suffix="1234",
        cohort_hash="d" * 64,
        decision_hash="e" * 64,
        provider="alpaca",
        feed="iex",
        interval="5m",
        strategy_id="ema_adx_trend",
        strategy_version="1.0.0",
        decision_timestamp=NOW,
        request=_request(),
    )
    repository.record_submission(
        session_id="session-1", intent_id="intent-1", account_suffix="1234", order=_order()
    )
    observation = _execution_observation()

    assert repository.record_execution_observation(observation) is True
    assert repository.record_execution_observation(observation) is False
    assert database.scalar("select count(*) from execution_observations") == 1
    with pytest.raises(ValueError, match="conflicting execution observation"):
        repository.record_execution_observation(
            observation.model_copy(update={"realized_slippage_bps": Decimal("9")})
        )
