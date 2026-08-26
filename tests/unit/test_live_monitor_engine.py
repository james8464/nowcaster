from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.live_monitor.engine import EligibilityEvidence, LiveMonitorEngine, evaluate_alert_eligibility
from src.live_monitor.types import Direction, MarketBar, MarketQuote, MonitorHealth

NOW = datetime(2026, 8, 26, 14, 0, tzinfo=UTC)


def evidence(**updates) -> EligibilityEvidence:
    values = {
        "provider": "alpaca",
        "feed": "iex",
        "symbol": "AAPL",
        "interval": "5m",
        "mode": "frozen",
        "promoted": True,
        "no_repaint_passed": True,
        "calibration_status": "calibrated",
        "economic_evidence_status": "authenticated",
        "direction": Direction.LONG,
        "probability": Decimal("0.68"),
        "vote_margin": Decimal("0.40"),
        "expected_net_edge": Decimal("0.002"),
        "breadth": 3,
        "data_through": NOW,
        "shortable": True,
        "easy_to_borrow": True,
        "reasons": (),
    }
    values.update(updates)
    return EligibilityEvidence(**values)


def quote(**updates) -> MarketQuote:
    values = {
        "provider": "alpaca",
        "feed": "iex",
        "symbol": "AAPL",
        "bid": Decimal("99.90"),
        "ask": Decimal("100"),
        "last": Decimal("99.95"),
        "tick_size": Decimal("0.01"),
        "provider_time": NOW,
        "received_at": NOW,
    }
    values.update(updates)
    return MarketQuote(**values)


def bar(minute: int, **updates) -> MarketBar:
    start = NOW + timedelta(minutes=minute)
    values = {
        "provider": "alpaca",
        "feed": "iex",
        "symbol": "AAPL",
        "interval": "1m",
        "start": start,
        "end": start + timedelta(minutes=1),
        "available_at": start + timedelta(minutes=1, seconds=1),
        "received_at": start + timedelta(minutes=1, seconds=1),
        "open": Decimal("100"),
        "high": Decimal("100.4"),
        "low": Decimal("99.6"),
        "close": Decimal("100"),
        "volume": Decimal("1000"),
        "finalized": True,
        "revision": 0,
    }
    values.update(updates)
    return MarketBar(**values)


def test_eligibility_passes_only_complete_matching_fresh_evidence() -> None:
    decision = evaluate_alert_eligibility(
        evidence(), quote(), health=MonitorHealth.HEALTHY, now=NOW + timedelta(seconds=5)
    )

    assert decision.status == "long"
    assert decision.reasons == ()
    assert decision.confidence == Decimal("0.68")


def test_eligibility_abstains_for_each_fail_closed_boundary() -> None:
    cases = [
        (evidence(mode="development"), quote(), MonitorHealth.HEALTHY, "qualified_mode_required"),
        (evidence(promoted=False), quote(), MonitorHealth.HEALTHY, "promotion_required"),
        (evidence(no_repaint_passed=False), quote(), MonitorHealth.HEALTHY, "no_repaint_required"),
        (evidence(feed="sip"), quote(), MonitorHealth.HEALTHY, "provider_feed_mismatch"),
        (evidence(data_through=NOW - timedelta(minutes=1)), quote(), MonitorHealth.HEALTHY, "stale_evidence"),
        (
            evidence(),
            quote(provider_time=NOW - timedelta(minutes=1)),
            MonitorHealth.HEALTHY,
            "stale_quote",
        ),
        (evidence(direction=Direction.SHORT, shortable=False), quote(), MonitorHealth.HEALTHY, "shortability_required"),
        (evidence(), quote(), MonitorHealth.RECONNECTING, "market_data_unhealthy"),
    ]
    for item, market_quote, health, reason in cases:
        decision = evaluate_alert_eligibility(item, market_quote, health=health, now=NOW + timedelta(seconds=5))
        assert decision.status == "abstain"
        assert reason in decision.reasons


def test_engine_deduplicates_wire_events_and_never_exposes_order_methods() -> None:
    engine = LiveMonitorEngine(session_id="session-1")
    first = engine.emit("heartbeat", {"health": "healthy"}, emitted_at=NOW)
    duplicate = engine.emit("heartbeat", {"health": "healthy"}, emitted_at=NOW)

    assert first.event_id == duplicate.event_id
    assert first.sequence == duplicate.sequence == 0
    assert not hasattr(engine, "submit_order")
    assert not hasattr(engine, "cancel_order")


def test_engine_emits_entry_plan_then_conservative_stop_lifecycle() -> None:
    def resolver(_bars: tuple[MarketBar, ...], _quote: MarketQuote) -> EligibilityEvidence:
        return evidence(data_through=NOW + timedelta(minutes=5))

    engine = LiveMonitorEngine(session_id="session-1", evidence_resolver=resolver)
    engine.accept_market_event(quote(received_at=NOW + timedelta(minutes=5), provider_time=NOW + timedelta(minutes=5)))
    emitted = []
    for minute in range(5):
        emitted.extend(engine.accept_market_event(bar(minute)))

    entry = [item for item in emitted if item.event_type == "notification_request"]
    assert len(entry) == 1
    assert entry[0].payload["category"] == "entry"
    assert entry[0].payload["direction"] == "long"
    assert Decimal(entry[0].payload["stop"]) < Decimal(entry[0].payload["entry_low"])
    assert Decimal(entry[0].payload["target_2"]) > Decimal(entry[0].payload["target_1"])

    stop = Decimal(entry[0].payload["stop"])
    risk_events = engine.accept_market_event(
        bar(5, low=stop - Decimal("0.1"), high=Decimal(entry[0].payload["target_2"]) + Decimal("0.1"))
    )
    notifications = [item for item in risk_events if item.event_type == "notification_request"]
    assert len(notifications) == 1
    assert notifications[0].payload["category"] == "stop"
    assert notifications[0].payload["reason"] == "protective_stop_touched"


def test_engine_abstains_without_quote_evidence_or_feasible_levels() -> None:
    engine = LiveMonitorEngine(session_id="session-1", evidence_resolver=lambda _bars, _quote: None)
    emitted = []
    for minute in range(5):
        emitted.extend(engine.accept_market_event(bar(minute)))

    decisions = [item for item in emitted if item.event_type == "decision"]
    assert decisions[-1].payload["status"] == "abstain"
    assert decisions[-1].payload["reasons"] == ["live_quote_unavailable"]
    assert not [item for item in emitted if item.event_type == "notification_request"]


def test_active_setup_closes_before_an_evidence_gate_failure_can_be_ignored() -> None:
    latest = {"minute": 5, "promoted": True}

    def resolver(_bars: tuple[MarketBar, ...], _quote: MarketQuote) -> EligibilityEvidence:
        return evidence(
            data_through=NOW + timedelta(minutes=latest["minute"]),
            promoted=latest["promoted"],
        )

    engine = LiveMonitorEngine(session_id="session-1", evidence_resolver=resolver)
    engine.accept_market_event(quote(received_at=NOW + timedelta(minutes=5), provider_time=NOW + timedelta(minutes=5)))
    for minute in range(5):
        engine.accept_market_event(bar(minute))

    latest.update(minute=10, promoted=False)
    engine.accept_market_event(
        quote(received_at=NOW + timedelta(minutes=10), provider_time=NOW + timedelta(minutes=10))
    )
    emitted = []
    for minute in range(5, 10):
        emitted.extend(engine.accept_market_event(bar(minute)))

    close = [
        item for item in emitted if item.event_type == "notification_request" and item.payload["category"] == "close"
    ]
    assert len(close) == 1
    assert close[0].payload["reason"] == "evidence_gate_failed"
