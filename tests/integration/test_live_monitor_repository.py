from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.database.engine import Database
from src.live_monitor.lifecycle import AlertLifecycle
from src.live_monitor.repository import LiveMonitorRepository
from src.live_monitor.types import (
    AlertState,
    Direction,
    LifecycleEvent,
    MarketBar,
    MarketQuote,
    MarketTrade,
    MonitorHealth,
    ProviderHealthEvent,
    TradePlan,
)

NOW = datetime(2026, 8, 26, 14, 0, tzinfo=UTC)
SETUP_ID = "a" * 64


def database(tmp_path) -> Database:
    value = Database.from_url(f"duckdb:///{tmp_path / 'monitor.duckdb'}")
    value.initialize()
    return value


def plan() -> TradePlan:
    return TradePlan(
        plan_id="b" * 64,
        provider="alpaca",
        feed="iex",
        symbol="AAPL",
        decision_interval="5m",
        direction=Direction.LONG,
        decision_time=NOW,
        expires_at=NOW + timedelta(minutes=15),
        entry_low=Decimal("100"),
        entry_high=Decimal("100.1"),
        stop=Decimal("97"),
        target_1=Decimal("103"),
        target_2=Decimal("104.5"),
        risk_per_unit=Decimal("3"),
        reward_to_risk_1=Decimal("1"),
        reward_to_risk_2=Decimal("1.5"),
        config_hash="c" * 64,
        cohort_id="d" * 64,
    )


def event(ordinal: int, state: AlertState) -> LifecycleEvent:
    return LifecycleEvent(
        event_id=f"{ordinal:064x}",
        setup_id=SETUP_ID,
        target_state=state,
        occurred_at=NOW + timedelta(seconds=ordinal),
        reason=state.value,
    )


def test_repository_recovers_lifecycle_without_redelivering_notifications(tmp_path) -> None:
    store = database(tmp_path)
    repository = LiveMonitorRepository(store, clock=lambda: NOW)
    repository.start_session("session-1", config_hash="c" * 64, cohort_hash="d" * 64)
    repository.create_setup("session-1", SETUP_ID, plan())
    lifecycle = AlertLifecycle(SETUP_ID, plan())
    for item in (event(1, AlertState.CANDIDATE), event(2, AlertState.ENTRY_ALERTED)):
        transition = lifecycle.apply(item)
        assert transition is not None
        assert repository.record_transition(transition) is True
    assert repository.record_transition(transition) is False
    assert repository.record_notification_receipt(event_id=event(2, AlertState.ENTRY_ALERTED).event_id) is True
    assert repository.record_notification_receipt(event_id=event(2, AlertState.ENTRY_ALERTED).event_id) is False

    recovered = LiveMonitorRepository(store, clock=lambda: NOW).recover_active(
        provider_feeds={("alpaca", "iex")},
        symbols={"AAPL"},
        interval="5m",
        config_hash="c" * 64,
        cohort_ids={"d" * 64},
        now=NOW,
    )

    assert len(recovered) == 1
    assert recovered[0].setup_id == SETUP_ID
    assert recovered[0].state is AlertState.ENTRY_ALERTED
    assert recovered[0].delivered_event_ids == (event(2, AlertState.ENTRY_ALERTED).event_id,)
    assert recovered[0].actual_fill is None
    assert {"monitor_sessions", "monitor_setups", "monitor_transitions", "monitor_notification_receipts"} <= set(
        store.table_names()
    )


def test_repository_rejects_conflicting_event_identity(tmp_path) -> None:
    store = database(tmp_path)
    repository = LiveMonitorRepository(store, clock=lambda: NOW)
    repository.start_session("session-1", config_hash="c" * 64, cohort_hash="d" * 64)
    repository.create_setup("session-1", SETUP_ID, plan())
    lifecycle = AlertLifecycle(SETUP_ID, plan())
    transition = lifecycle.apply(event(1, AlertState.CANDIDATE))
    assert transition is not None and repository.record_transition(transition)
    conflicting = transition.model_copy(update={"reason": "different"})

    try:
        repository.record_transition(conflicting)
    except ValueError as error:
        assert "conflicting" in str(error)
    else:
        raise AssertionError("conflicting event identity was accepted")


def test_repository_recovers_tracked_fill_only_for_exact_unexpired_context(tmp_path) -> None:
    store = database(tmp_path)
    repository = LiveMonitorRepository(store, clock=lambda: NOW)
    repository.start_session("session-1", config_hash="c" * 64, cohort_hash="d" * 64)
    repository.create_setup("session-1", SETUP_ID, plan())
    lifecycle = AlertLifecycle(SETUP_ID, plan())
    events = (
        event(1, AlertState.CANDIDATE),
        event(2, AlertState.ENTRY_ALERTED),
        event(3, AlertState.UNTRACKED),
        LifecycleEvent(
            event_id=f"{4:064x}",
            setup_id=SETUP_ID,
            target_state=AlertState.TRACKED,
            occurred_at=NOW + timedelta(seconds=4),
            reason="operator_fill_tracked",
            actual_fill=Decimal("100.04"),
        ),
    )
    for item in events:
        transition = lifecycle.apply(item)
        assert transition is not None and repository.record_transition(transition)

    exact = repository.recover_active(
        provider_feeds={("alpaca", "iex")},
        symbols={"AAPL"},
        interval="5m",
        config_hash="c" * 64,
        cohort_ids={"d" * 64},
        now=NOW,
    )

    assert len(exact) == 1
    assert exact[0].state is AlertState.TRACKED
    assert exact[0].actual_fill == Decimal("100.04")
    assert (
        repository.recover_active(
            provider_feeds={("alpaca", "iex")},
            symbols={"AAPL"},
            interval="5m",
            config_hash="e" * 64,
            cohort_ids={"d" * 64},
            now=NOW,
        )
        == ()
    )
    assert (
        repository.recover_active(
            provider_feeds={("alpaca", "iex")},
            symbols={"AAPL"},
            interval="5m",
            config_hash="c" * 64,
            cohort_ids={"d" * 64},
            now=NOW + timedelta(minutes=16),
        )
        == ()
    )


def test_repository_records_complete_live_audit_ledger(tmp_path) -> None:
    store = database(tmp_path)
    repository = LiveMonitorRepository(store, clock=lambda: NOW)
    repository.start_session("session-1", config_hash="c" * 64, cohort_hash="d" * 64)
    finalized = MarketBar(
        provider="alpaca",
        feed="iex",
        symbol="AAPL",
        interval="1m",
        start=NOW,
        end=NOW + timedelta(minutes=1),
        available_at=NOW + timedelta(minutes=1),
        received_at=NOW + timedelta(minutes=1),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("10"),
        finalized=True,
        revision=0,
    )
    decision = {
        "provider": "alpaca",
        "feed": "iex",
        "symbol": "AAPL",
        "interval": "5m",
        "decision_time": "2026-08-26T14:05:00Z",
        "status": "abstain",
        "reasons": ["warming"],
    }
    health = ProviderHealthEvent(
        provider="alpaca",
        feed="iex",
        status=MonitorHealth.WARMING,
        reason="warming",
        occurred_at=NOW,
    )

    assert repository.record_finalized_bar("session-1", finalized)
    assert not repository.record_finalized_bar("session-1", finalized)
    assert repository.latest_finalized_ends({("alpaca", "iex", "AAPL")}) == {("alpaca", "iex", "AAPL"): finalized.end}
    assert repository.record_decision("session-1", decision)
    assert repository.record_health_event("session-1", health)
    assert store.scalar("select count(*) from monitor_finalized_bars") == 1
    assert store.scalar("select count(*) from monitor_decisions") == 1
    assert store.scalar("select count(*) from monitor_health_events") == 1


def test_repository_persists_normalized_market_events_idempotently(tmp_path) -> None:
    store = database(tmp_path)
    repository = LiveMonitorRepository(store, clock=lambda: NOW)
    repository.start_session("session-1", config_hash="c" * 64, cohort_hash="d" * 64)
    quote = MarketQuote(
        provider="binance",
        feed="spot",
        symbol="BTCUSDT",
        bid=Decimal("64000.10"),
        ask=Decimal("64000.20"),
        bid_size=Decimal("1.2"),
        ask_size=Decimal("0.8"),
        last=Decimal("64000.15"),
        tick_size=Decimal("0.01"),
        sequence=42,
        provider_time=NOW,
        received_at=NOW,
    )
    trade = MarketTrade(
        provider="binance",
        feed="spot",
        symbol="BTCUSDT",
        trade_id="17",
        price=Decimal("64000.15"),
        size=Decimal("0.25"),
        aggressor="sell",
        sequence=17,
        provider_time=NOW,
        received_at=NOW,
    )

    assert repository.record_market_event("session-1", quote)
    assert not repository.record_market_event("session-1", quote)
    assert repository.record_market_event("session-1", trade)
    rows = store.frame("select event_type, sequence from live_market_events order by event_type")
    assert rows.to_dict("records") == [
        {"event_type": "quote", "sequence": 42},
        {"event_type": "trade", "sequence": 17},
    ]
