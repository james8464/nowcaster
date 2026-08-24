from __future__ import annotations

from datetime import UTC, datetime

from src.database.engine import Database
from src.trading.emergency import EmergencyController, FlattenConfirmation
from src.trading.shadow import ShadowBrokerClient
from src.trading.types import BrokerAccount, BrokerClock, BrokerPosition

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


def _database(tmp_path):
    database = Database.from_url(f"duckdb:///{tmp_path / 'emergency.duckdb'}")
    database.initialize()
    return database


def _broker(position=True):
    positions = []
    if position:
        positions.append(
            BrokerPosition(
                symbol="AAPL",
                quantity="1",
                market_value="190",
                average_entry_price="190",
                current_price="190",
                unrealized_pnl="0",
                received_at=NOW,
            )
        )
    return ShadowBrokerClient(
        account=BrokerAccount(
            account_id="account-1234",
            account_suffix="1234",
            status="ACTIVE",
            equity="100000",
            buying_power="100000",
            trading_blocked=False,
            pattern_day_trader=False,
            shorting_enabled=True,
            received_at=NOW,
        ),
        clock=BrokerClock(timestamp=NOW, is_open=True, next_open=NOW, next_close=NOW, received_at=NOW),
        positions=positions,
        now=lambda: NOW,
    )


def test_freeze_is_immediate_idempotent_and_persisted(tmp_path) -> None:
    database = _database(tmp_path)
    controller = EmergencyController(database=database, broker=_broker(False), session_id=None, account_suffix="1234")
    first = controller.freeze("operator")
    second = controller.freeze("operator")
    assert first.status == "frozen" and second.status == "already_frozen"
    assert database.scalar("select count(*) from trading_health_events") == 1


def test_flatten_requires_exact_suffix_and_phrase(tmp_path) -> None:
    controller = EmergencyController(
        database=_database(tmp_path), broker=_broker(), session_id=None, account_suffix="1234"
    )
    invalid = controller.flatten(FlattenConfirmation(account_suffix="9999", phrase="FLATTEN 9999"))
    assert invalid.status == "confirmation_rejected"
    assert len(controller.broker.list_orders(status="all")) == 0


def test_flatten_never_reports_success_from_order_acceptance(tmp_path) -> None:
    controller = EmergencyController(
        database=_database(tmp_path), broker=_broker(), session_id=None, account_suffix="1234"
    )
    outcome = controller.flatten(FlattenConfirmation(account_suffix="1234", phrase="FLATTEN 1234"))
    assert outcome.status == "unresolved" and outcome.remaining_positions == 1
    assert len(controller.broker.list_orders(status="all")) == 1


def test_flatten_succeeds_only_after_broker_reports_zero_positions(tmp_path) -> None:
    class ClosingBroker(ShadowBrokerClient):
        def submit_order(self, request):
            order = super().submit_order(request)
            self._positions.clear()
            return order

    base = _broker()
    broker = ClosingBroker(
        account=base.get_account(),
        clock=base.get_clock(),
        positions=base.list_positions(),
        now=lambda: NOW,
    )
    controller = EmergencyController(
        database=_database(tmp_path), broker=broker, session_id=None, account_suffix="1234"
    )
    outcome = controller.flatten(FlattenConfirmation(account_suffix="1234", phrase="FLATTEN 1234"))
    assert outcome.status == "flattened" and outcome.remaining_positions == 0
