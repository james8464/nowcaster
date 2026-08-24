from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from src.backtest.execution import DecisionProvenance, OrderIntent
from src.database.engine import Database
from src.trading.alpaca import AlpacaError
from src.trading.repository import TradingRepository
from src.trading.risk import PreTradeRiskEngine, RiskContext
from src.trading.shadow import ShadowBrokerClient
from src.trading.supervisor import TradingSupervisor
from src.trading.types import BrokerAccount, BrokerAsset, BrokerClock, BrokerOrderRequest, BrokerPosition

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


def _account() -> BrokerAccount:
    return BrokerAccount(
        account_id="account-1234",
        account_suffix="1234",
        status="ACTIVE",
        equity="100000",
        buying_power="200000",
        trading_blocked=False,
        pattern_day_trader=False,
        shorting_enabled=True,
        received_at=NOW,
    )


def _broker(*, positions=()) -> ShadowBrokerClient:
    return ShadowBrokerClient(
        account=_account(),
        clock=BrokerClock(timestamp=NOW, is_open=True, next_open=NOW, next_close=NOW, received_at=NOW),
        assets=[
            BrokerAsset(
                symbol="AAPL", tradable=True, shortable=True, easy_to_borrow=True, fractionable=True, received_at=NOW
            )
        ],
        positions=positions,
        now=lambda: NOW,
    )


def _intent() -> OrderIntent:
    source = DecisionProvenance(
        strategy_id="ensemble-v1",
        symbol="AAPL",
        decision_hash="d" * 64,
        decision_timestamp=pd.Timestamp(NOW),
        signal=1,
        strength=0.8,
    )
    return OrderIntent(
        order_id="logical-order-1",
        strategy_id="ensemble-v1",
        symbol="AAPL",
        decision_timestamp=pd.Timestamp(NOW),
        side="buy",
        quantity=1,
        source_decisions=(source,),
    )


def _database(tmp_path) -> Database:
    database = Database.from_url(f"duckdb:///{tmp_path / 'supervisor.duckdb'}")
    database.initialize()
    return database


def _supervisor(database, broker, session_id="session-1", *, risk=False) -> TradingSupervisor:
    return TradingSupervisor(
        repository=TradingRepository(database, clock=lambda: NOW),
        broker=broker,
        session_id=session_id,
        cohort_hash="c" * 64,
        provider="alpaca",
        feed="iex",
        interval="1Min",
        strategy_version="1",
        code_hash="a" * 64,
        config_hash="b" * 64,
        risk_engine=PreTradeRiskEngine() if risk else None,
        clock=lambda: NOW,
    )


def _risk_context(**updates) -> RiskContext:
    values = {
        "environment": "shadow",
        "account_suffix": "1234",
        "expected_account_suffix": "1234",
        "cohort_hash": "c" * 64,
        "expected_cohort_hash": "c" * 64,
        "provider": "alpaca",
        "expected_provider": "alpaca",
        "feed": "iex",
        "expected_feed": "iex",
        "data_age_seconds": 1,
        "unresolved_mismatches": 0,
        "account_equity": "100000",
        "buying_power": "100000",
        "current_position_notional": "0",
        "gross_exposure": "0",
        "turnover_today": "0",
        "orders_last_minute": 0,
        "spread_bps": "5",
        "reference_price": "190.20",
        "limit_price": "190.50",
        "daily_pnl": "0",
        "drawdown_fraction": "0",
        "frozen": False,
        "duplicate_order": False,
        "conflicting_order": False,
        "asset_tradable": True,
        "asset_shortable": True,
        "asset_easy_to_borrow": True,
        "is_opening_short": False,
    }
    values.update(updates)
    return RiskContext(**values)


def test_start_reconciles_before_admission_and_submission_is_durable(tmp_path) -> None:
    database = _database(tmp_path)
    broker = _broker()
    supervisor = _supervisor(database, broker)
    started = supervisor.start()
    assert started.status == "matched" and supervisor.ready

    outcome = supervisor.submit_intent(_intent(), limit_price=Decimal("190.50"))
    assert outcome.status == "accepted"
    assert database.scalar("select count(*) from broker_order_intents") == 1
    assert database.scalar("select count(*) from broker_orders") == 1
    assert broker.get_order_by_client_id(outcome.client_order_id).filled_quantity == 0


class _AmbiguousBroker(ShadowBrokerClient):
    submit_calls = 0
    lookups: list[str]

    def __init__(self):
        base = _broker()
        super().__init__(
            account=base.get_account(), clock=base.get_clock(), assets=[base.get_asset("AAPL")], now=lambda: NOW
        )
        self.lookups = []

    def submit_order(self, request: BrokerOrderRequest):
        self.submit_calls += 1
        super().submit_order(request)
        raise AlpacaError("ambiguous submission transport failure", ambiguous=True)

    def get_order_by_client_id(self, client_order_id: str):
        self.lookups.append(client_order_id)
        return super().get_order_by_client_id(client_order_id)


def test_ambiguous_submission_queries_client_id_before_any_retry(tmp_path) -> None:
    database = _database(tmp_path)
    broker = _AmbiguousBroker()
    supervisor = _supervisor(database, broker)
    supervisor.start()
    outcome = supervisor.submit_intent(_intent(), limit_price=Decimal("190.50"))
    assert outcome.status == "accepted"
    assert broker.submit_calls == 1 and broker.lookups == [outcome.client_order_id]


def test_broker_position_mismatch_freezes_and_blocks_orders(tmp_path) -> None:
    database = _database(tmp_path)
    broker = _broker(
        positions=[
            BrokerPosition(
                symbol="AAPL",
                quantity="1",
                market_value="190.5",
                average_entry_price="190.5",
                current_price="190.5",
                unrealized_pnl="0",
                received_at=NOW,
            )
        ]
    )
    supervisor = _supervisor(database, broker)
    result = supervisor.start()
    assert result.status == "mismatch" and supervisor.frozen
    blocked = supervisor.submit_intent(_intent(), limit_price=Decimal("190.50"))
    assert blocked.status == "frozen"


def test_nonambiguous_submit_failure_freezes_without_retry(tmp_path) -> None:
    database = _database(tmp_path)

    class Broken(_AmbiguousBroker):
        def submit_order(self, request):
            self.submit_calls += 1
            raise AlpacaError("HTTP 422")

    broker = Broken()
    supervisor = _supervisor(database, broker)
    supervisor.start()
    outcome = supervisor.submit_intent(_intent(), limit_price=Decimal("190.50"))
    assert outcome.status == "broker_rejected" and supervisor.frozen and broker.submit_calls == 1


def test_risk_rejection_is_persisted_and_never_reaches_broker(tmp_path) -> None:
    database = _database(tmp_path)
    broker = _AmbiguousBroker()
    supervisor = _supervisor(database, broker, risk=True)
    supervisor.start()
    outcome = supervisor.submit_intent(
        _intent(),
        limit_price=Decimal("190.50"),
        risk_context=_risk_context(data_age_seconds=31),
    )
    assert outcome.status == "risk_rejected" and broker.submit_calls == 0
    assert database.scalar("select count(*) from risk_decisions where allowed = false") == 1


def test_risk_admission_is_durable_before_broker_submission(tmp_path) -> None:
    database = _database(tmp_path)

    class InspectingBroker(_AmbiguousBroker):
        def submit_order(self, request):
            assert database.scalar("select count(*) from risk_decisions where allowed = true") == 1
            return ShadowBrokerClient.submit_order(self, request)

    broker = InspectingBroker()
    supervisor = _supervisor(database, broker, risk=True)
    supervisor.start()
    outcome = supervisor.submit_intent(
        _intent(),
        limit_price=Decimal("190.50"),
        risk_context=_risk_context(),
    )
    assert outcome.status == "accepted"
