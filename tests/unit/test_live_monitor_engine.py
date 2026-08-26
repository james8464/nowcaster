from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.live_monitor.engine import EligibilityEvidence, LiveMonitorEngine, evaluate_alert_eligibility
from src.live_monitor.types import Direction, MarketQuote, MonitorHealth

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
