from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.live_monitor.command import parse_control
from src.live_monitor.engine import EligibilityEvidence, LiveMonitorEngine, evaluate_alert_eligibility
from src.live_monitor.types import Direction, MarketBar, MarketQuote, MonitorHealth, ProviderHealthEvent
from src.models.drift import DEFAULT_DRIFT_POLICY_HASH
from src.strategies.types import canonical_hash

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
        "probability_lower_bound": Decimal("0.58"),
        "probability_upper_bound": Decimal("0.76"),
        "calibration_method": "oof_sigmoid_v2",
        "calibration_observations": 100,
        "calibration_effective_observations": Decimal("100"),
        "brier_score": Decimal("0.19"),
        "expected_calibration_error": Decimal("0.04"),
        "selective_threshold": Decimal("0.60"),
        "selective_coverage": Decimal("0.30"),
        "probability_definition": "target_before_stop_after_costs",
        "vote_margin": Decimal("0.40"),
        "expected_net_edge": Decimal("0.002"),
        "drift_status": "stable",
        "drift_score": Decimal("0"),
        "drift_policy_hash": DEFAULT_DRIFT_POLICY_HASH,
        "drift_evidence_hash": "9" * 64,
        "drift_confirmed_metrics": (),
        "breadth": 3,
        "data_through": NOW,
        "shortable": True,
        "easy_to_borrow": True,
        "reasons": (),
    }
    contextual = {
        "asset_profile": "us_liquid_equity",
        "contextual_eligibility_state": "eligible",
        "contextual_eligibility_hash": "1" * 64,
        "context_hash": "2" * 64,
        "contextual_policy_hash": "3" * 64,
        "regime_probabilities": {
            "trend_normal": "0.4",
            "trend_elevated_volatility": "0.2",
            "range_liquid": "0.3",
            "stressed_or_illiquid": "0.1",
        },
        "contextual_drift_status": "stable",
        "contextual_covariance_hash": "5" * 64,
        "contextual_weight_hash": "6" * 64,
        "portfolio_selection_id": "7" * 64,
        "portfolio_decision_hash": "8" * 64,
        "portfolio_selected": True,
        "contextual_effective_at": NOW - timedelta(hours=1),
        "contextual_expires_at": NOW + timedelta(hours=1),
    }
    values.update(contextual)
    values.update(updates)
    values["contextual_cohort_hash"] = canonical_hash(
        {
            "cohort_id": values.get("cohort_id", "0" * 64),
            "dataset_hash": values.get("dataset_hash", "0" * 64),
            "context_hash": values["context_hash"],
            "policy_hash": values["contextual_policy_hash"],
        }
    )
    contextual_keys = (
        "asset_profile",
        "contextual_eligibility_state",
        "contextual_eligibility_hash",
        "context_hash",
        "contextual_policy_hash",
        "contextual_cohort_hash",
        "regime_probabilities",
        "contextual_drift_status",
        "contextual_covariance_hash",
        "contextual_weight_hash",
        "portfolio_selection_id",
        "portfolio_decision_hash",
        "portfolio_selected",
        "contextual_effective_at",
        "contextual_expires_at",
    )
    authenticated = {key: values[key] for key in contextual_keys}
    authenticated["regime_probabilities"] = {
        key: str(value) for key, value in sorted(authenticated["regime_probabilities"].items())
    }
    values["contextual_evidence_hash"] = canonical_hash(authenticated)
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


def test_live_alert_requires_eligible_context_and_portfolio_selection() -> None:
    decision = evaluate_alert_eligibility(
        evidence(portfolio_selected=False),
        quote(),
        health=MonitorHealth.HEALTHY,
        now=NOW + timedelta(seconds=5),
    )
    assert decision.status == "abstain"
    assert "portfolio_selection_required" in decision.reasons


def test_live_alert_rechecks_authenticated_context_expiry_at_final_processing_time() -> None:
    expiring = evidence(
        contextual_effective_at=NOW - timedelta(hours=1),
        contextual_expires_at=NOW + timedelta(seconds=4),
    )

    decision = evaluate_alert_eligibility(
        expiring,
        quote(),
        health=MonitorHealth.HEALTHY,
        now=NOW + timedelta(seconds=5),
    )

    assert decision.status == "abstain"
    assert "contextual_evidence_expired" in decision.reasons


def test_legacy_live_evidence_remains_decodable_but_abstains() -> None:
    legacy = evidence().model_copy(
        update={
            "asset_profile": None,
            "contextual_eligibility_state": None,
            "contextual_eligibility_hash": None,
            "context_hash": None,
            "contextual_policy_hash": None,
            "contextual_cohort_hash": None,
            "regime_probabilities": None,
            "contextual_drift_status": None,
            "contextual_covariance_hash": None,
            "contextual_weight_hash": None,
            "portfolio_selection_id": None,
            "portfolio_decision_hash": None,
            "portfolio_selected": None,
            "contextual_effective_at": None,
            "contextual_expires_at": None,
            "contextual_evidence_hash": None,
        }
    )
    decision = evaluate_alert_eligibility(
        legacy,
        quote(),
        health=MonitorHealth.HEALTHY,
        now=NOW + timedelta(seconds=5),
    )
    assert decision.status == "abstain"
    assert "contextual_evidence_required" in decision.reasons


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
        (
            evidence(
                provider="binance",
                feed="spot",
                symbol="BTCUSDT",
                direction=Direction.SHORT,
                shortable=False,
                easy_to_borrow=False,
            ),
            quote(provider="binance", feed="spot", symbol="BTCUSDT"),
            MonitorHealth.HEALTHY,
            "shortability_required",
        ),
        (evidence(), quote(), MonitorHealth.RECONNECTING, "market_data_unhealthy"),
        (
            evidence(calibration_effective_observations=Decimal("99")),
            quote(),
            MonitorHealth.HEALTHY,
            "minimum_effective_calibration_sample",
        ),
        (
            evidence(probability_lower_bound=Decimal("0.54")),
            quote(),
            MonitorHealth.HEALTHY,
            "probability_lower_bound",
        ),
        (
            evidence(probability=Decimal("0.59")),
            quote(),
            MonitorHealth.HEALTHY,
            "selective_threshold",
        ),
        (evidence(drift_status="confirmed"), quote(), MonitorHealth.HEALTHY, "material_model_drift"),
    ]
    for item, market_quote, health, reason in cases:
        decision = evaluate_alert_eligibility(item, market_quote, health=health, now=NOW + timedelta(seconds=5))
        assert decision.status == "abstain"
        assert reason in decision.reasons


def test_engine_calibration_policy_can_only_tighten_the_fail_closed_gate() -> None:
    with pytest.raises(ValueError, match="below 100"):
        LiveMonitorEngine(
            session_id="session-1",
            minimum_effective_calibration_observations=Decimal("99"),
        )

    engine = LiveMonitorEngine(
        session_id="session-1",
        evidence_resolver=lambda _bars, _quote: evidence(data_through=NOW + timedelta(minutes=5)),
        minimum_effective_calibration_observations=Decimal("101"),
    )
    for minute in range(5):
        engine.accept_market_event(bar(minute))
    at = NOW + timedelta(minutes=5, seconds=2)
    decision = next(
        item
        for item in engine.accept_market_event(quote(provider_time=at, received_at=at))
        if item.event_type == "decision"
    )

    assert decision.payload["status"] == "abstain"
    assert "minimum_effective_calibration_sample" in decision.payload["reasons"]


def test_engine_invalidates_readiness_once_and_abstains_on_confirmed_drift() -> None:
    invalidations: list[tuple[str, str, datetime]] = []

    def resolver(_bars: tuple[MarketBar, ...], _quote: MarketQuote) -> EligibilityEvidence:
        return evidence(
            data_through=NOW + timedelta(minutes=5),
            drift_status="confirmed",
            drift_score=Decimal("4.2"),
            drift_confirmed_metrics=("prediction_distribution",),
        )

    engine = LiveMonitorEngine(
        session_id="session-1",
        evidence_resolver=resolver,
        readiness_cohort_hash="c" * 64,
        readiness_invalidator=lambda cohort_hash, evidence_hash, at: invalidations.append(
            (cohort_hash, evidence_hash, at)
        ),
    )
    for minute in range(5):
        engine.accept_market_event(bar(minute))
    at = NOW + timedelta(minutes=5, seconds=2)
    emitted = engine.accept_market_event(quote(received_at=at, provider_time=at))

    assert invalidations == [("c" * 64, "9" * 64, at)]
    assert any(item.event_type == "model_drift" for item in emitted)
    decision = next(item for item in emitted if item.event_type == "decision")
    assert decision.payload["status"] == "abstain"
    assert "material_model_drift" in decision.payload["reasons"]
    assert not [item for item in emitted if item.event_type == "notification_request"]


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
    emitted = []
    for minute in range(5):
        emitted.extend(engine.accept_market_event(bar(minute)))
    emitted.extend(
        engine.accept_market_event(
            quote(
                received_at=NOW + timedelta(minutes=5, seconds=2),
                provider_time=NOW + timedelta(minutes=5, seconds=2),
            )
        )
    )

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


def test_a_quote_that_became_stale_in_the_processing_queue_cannot_authorize_an_entry() -> None:
    engine = LiveMonitorEngine(
        session_id="delayed-quote",
        evidence_resolver=lambda _bars, _quote: evidence(data_through=NOW + timedelta(minutes=5)),
    )
    for minute in range(5):
        engine.accept_market_event(bar(minute))
    emitted = engine.accept_market_event(
        quote(
            provider_time=NOW + timedelta(minutes=5, seconds=2),
            received_at=NOW + timedelta(minutes=5, seconds=2),
            processed_at=NOW + timedelta(minutes=5, seconds=42),
        )
    )
    decisions = [item for item in emitted if item.event_type == "decision"]
    assert decisions[0].payload["status"] == "abstain"
    assert "stale_quote" in decisions[0].payload["reasons"]
    assert decisions[0].emitted_at == NOW + timedelta(minutes=5, seconds=42)
    assert not any(item.event_type == "notification_request" for item in emitted)


def test_a_stop_delayed_in_processing_is_reported_as_a_delayed_observation() -> None:
    engine = LiveMonitorEngine(
        session_id="delayed-stop",
        evidence_resolver=lambda _bars, _quote: evidence(data_through=NOW + timedelta(minutes=5)),
    )
    for minute in range(5):
        engine.accept_market_event(bar(minute))
    at = NOW + timedelta(minutes=5, seconds=2)
    entry = next(
        item
        for item in engine.accept_market_event(quote(provider_time=at, received_at=at))
        if item.event_type == "notification_request"
    )
    delayed = bar(
        5, low=Decimal(entry.payload["stop"]) - Decimal("0.1"), processed_at=NOW + timedelta(minutes=6, seconds=42)
    )
    notification = next(
        item for item in engine.accept_market_event(delayed) if item.event_type == "notification_request"
    )
    assert notification.payload["reason"] == "protective_stop_touched_delayed_observation"
    assert notification.emitted_at == NOW + timedelta(minutes=6, seconds=42)


def test_live_clock_rechecks_freshness_after_slow_evidence_calculation() -> None:
    current = NOW + timedelta(minutes=5, seconds=2)

    def slow_resolver(_bars, _quote):
        nonlocal current
        current += timedelta(seconds=40)
        return evidence(data_through=NOW + timedelta(minutes=5))

    engine = LiveMonitorEngine(
        session_id="slow-evidence", evidence_resolver=slow_resolver, processing_clock=lambda: current
    )
    for minute in range(5):
        engine.accept_market_event(bar(minute))
    emitted = engine.accept_market_event(quote(provider_time=current, received_at=current))
    decision = next(item for item in emitted if item.event_type == "decision")
    assert decision.payload["status"] == "abstain"
    assert "stale_quote" in decision.payload["reasons"]
    assert not any(item.event_type == "notification_request" for item in emitted)


def test_live_clock_rechecks_context_expiry_after_slow_evidence_calculation() -> None:
    current = NOW + timedelta(minutes=5, seconds=2)
    expires_at = current + timedelta(seconds=2)

    def slow_resolver(_bars, _quote):
        nonlocal current
        current += timedelta(seconds=3)
        return evidence(
            data_through=NOW + timedelta(minutes=5),
            contextual_effective_at=NOW,
            contextual_expires_at=expires_at,
        )

    engine = LiveMonitorEngine(
        session_id="expiring-context",
        evidence_resolver=slow_resolver,
        processing_clock=lambda: current,
    )
    for minute in range(5):
        engine.accept_market_event(bar(minute))
    emitted = engine.accept_market_event(quote(provider_time=current, received_at=current))
    decision = next(item for item in emitted if item.event_type == "decision")
    assert decision.payload["status"] == "abstain"
    assert "contextual_evidence_expired" in decision.payload["reasons"]
    assert "stale_quote" not in decision.payload["reasons"]
    assert not any(item.event_type == "notification_request" for item in emitted)


def test_engine_abstains_without_quote_evidence_or_feasible_levels() -> None:
    engine = LiveMonitorEngine(session_id="session-1", evidence_resolver=lambda _bars, _quote: None)
    emitted = []
    for minute in range(5):
        emitted.extend(engine.accept_market_event(bar(minute)))

    decisions = [item for item in emitted if item.event_type == "decision"]
    assert decisions[-1].payload["status"] == "abstain"
    assert decisions[-1].payload["reasons"] == ["awaiting_post_finalization_quote"]
    assert not [item for item in emitted if item.event_type == "notification_request"]


def test_active_setup_remains_open_when_qualified_evidence_becomes_unavailable() -> None:
    latest = {"minute": 5, "promoted": True, "direction": Direction.LONG}

    def resolver(_bars: tuple[MarketBar, ...], _quote: MarketQuote) -> EligibilityEvidence:
        return evidence(
            data_through=NOW + timedelta(minutes=latest["minute"]),
            promoted=latest["promoted"],
            direction=latest["direction"],
        )

    engine = LiveMonitorEngine(session_id="session-1", evidence_resolver=resolver)
    for minute in range(5):
        engine.accept_market_event(bar(minute))
    engine.accept_market_event(
        quote(
            received_at=NOW + timedelta(minutes=5, seconds=2),
            provider_time=NOW + timedelta(minutes=5, seconds=2),
        )
    )

    latest.update(minute=10, promoted=False, direction=Direction.SHORT)
    emitted = []
    for minute in range(5, 10):
        emitted.extend(engine.accept_market_event(bar(minute)))
    emitted.extend(
        engine.accept_market_event(
            quote(
                received_at=NOW + timedelta(minutes=10, seconds=2),
                provider_time=NOW + timedelta(minutes=10, seconds=2),
            )
        )
    )

    close = [
        item for item in emitted if item.event_type == "notification_request" and item.payload["category"] == "close"
    ]
    assert close == []
    assert any(
        item.event_type == "provider_health" and item.payload["reason"] == "monitoring_unavailable" for item in emitted
    )


def test_pre_close_quote_is_never_used_and_late_revision_is_not_redecided() -> None:
    calls = []

    def resolver(_bars: tuple[MarketBar, ...], used_quote: MarketQuote) -> EligibilityEvidence:
        calls.append(used_quote.provider_time)
        return evidence(data_through=NOW + timedelta(minutes=5))

    engine = LiveMonitorEngine(session_id="session-1", evidence_resolver=resolver)
    engine.accept_market_event(
        quote(received_at=NOW + timedelta(minutes=4, seconds=59), provider_time=NOW + timedelta(minutes=4, seconds=59))
    )
    emitted = []
    for minute in range(5):
        emitted.extend(engine.accept_market_event(bar(minute)))
    assert calls == []
    assert emitted[-1].payload["reasons"] == ["awaiting_post_finalization_quote"]

    post = NOW + timedelta(minutes=5, seconds=2)
    engine.accept_market_event(quote(received_at=post, provider_time=post))
    assert calls == [post]
    revised = bar(4, revision=1, close=Decimal("100.1"))
    engine.accept_market_event(revised)
    assert calls == [post]


def test_one_bar_crossing_both_targets_emits_ordered_tp1_then_tp2() -> None:
    engine = LiveMonitorEngine(
        session_id="session-1",
        evidence_resolver=lambda _bars, _quote: evidence(data_through=NOW + timedelta(minutes=5)),
    )
    for minute in range(5):
        engine.accept_market_event(bar(minute))
    post = NOW + timedelta(minutes=5, seconds=2)
    entry_events = engine.accept_market_event(quote(received_at=post, provider_time=post))
    entry = next(item for item in entry_events if item.event_type == "notification_request")
    target_2 = Decimal(entry.payload["target_2"])

    emitted = engine.accept_market_event(bar(5, high=target_2 + Decimal("0.1")))
    target_reasons = [
        item.payload["reason"]
        for item in emitted
        if item.event_type == "notification_request" and item.payload["category"] == "target"
    ]

    assert target_reasons == ["target_1_touched", "target_2_touched"]


def test_eligible_reversal_notifies_close_before_the_new_entry() -> None:
    latest = {"minute": 5, "direction": Direction.LONG}

    def resolver(_bars: tuple[MarketBar, ...], _quote: MarketQuote) -> EligibilityEvidence:
        return evidence(
            data_through=NOW + timedelta(minutes=latest["minute"]),
            direction=latest["direction"],
        )

    engine = LiveMonitorEngine(session_id="session-1", evidence_resolver=resolver)
    for minute in range(5):
        engine.accept_market_event(bar(minute))
    at = NOW + timedelta(minutes=5, seconds=2)
    engine.accept_market_event(quote(received_at=at, provider_time=at))

    latest.update(minute=10, direction=Direction.SHORT)
    for minute in range(5, 10):
        engine.accept_market_event(bar(minute))
    at = NOW + timedelta(minutes=10, seconds=2)
    emitted = engine.accept_market_event(quote(received_at=at, provider_time=at))

    categories = [item.payload["category"] for item in emitted if item.event_type == "notification_request"]
    assert categories == ["close", "entry"]


def test_stop_takes_precedence_when_the_expiry_bar_crosses_it() -> None:
    engine = LiveMonitorEngine(
        session_id="session-1",
        evidence_resolver=lambda _bars, _quote: evidence(data_through=NOW + timedelta(minutes=5)),
    )
    for minute in range(5):
        engine.accept_market_event(bar(minute))
    at = NOW + timedelta(minutes=5, seconds=2)
    entry_events = engine.accept_market_event(quote(received_at=at, provider_time=at))
    entry = next(item for item in entry_events if item.event_type == "notification_request")
    stop = Decimal(entry.payload["stop"])

    emitted = []
    for minute in range(5, 19):
        emitted.extend(engine.accept_market_event(bar(minute)))
    emitted.extend(engine.accept_market_event(bar(19, low=stop - Decimal("0.1"))))

    terminal = [item for item in emitted if item.event_type == "notification_request"]
    assert terminal[-1].payload["category"] == "stop"
    assert terminal[-1].payload["reason"] == "protective_stop_touched"


def test_verified_repair_replays_protective_stop_without_reopening_entry_inference() -> None:
    engine = LiveMonitorEngine(
        session_id="session-1",
        evidence_resolver=lambda _bars, _quote: evidence(data_through=NOW + timedelta(minutes=5)),
    )
    for minute in range(5):
        engine.accept_market_event(bar(minute))
    at = NOW + timedelta(minutes=5, seconds=2)
    entry_events = engine.accept_market_event(quote(received_at=at, provider_time=at))
    entry = next(item for item in entry_events if item.event_type == "notification_request")
    stop = Decimal(entry.payload["stop"])

    engine.accept_market_event(
        ProviderHealthEvent(
            provider="alpaca",
            feed="iex",
            status=MonitorHealth.RECONNECTING,
            reason="stream_disconnected",
            occurred_at=at + timedelta(seconds=1),
        )
    )
    repair_at = NOW + timedelta(minutes=10)
    repaired = engine.accept_market_event(
        bar(
            5,
            low=stop - Decimal("0.1"),
            available_at=repair_at,
            received_at=repair_at,
            repair_verified=True,
        )
    )
    notification = next(item for item in repaired if item.event_type == "notification_request")
    assert notification.payload["category"] == "stop"
    assert notification.payload["reason"] == "protective_stop_touched_delayed_observation"


def test_verified_repair_buckets_cannot_create_retrospective_entry() -> None:
    engine = LiveMonitorEngine(
        session_id="session-1",
        evidence_resolver=lambda _bars, _quote: evidence(data_through=NOW + timedelta(minutes=5)),
    )
    engine.accept_market_event(
        ProviderHealthEvent(
            provider="alpaca",
            feed="iex",
            status=MonitorHealth.RECONNECTING,
            reason="gap_repair_started",
            occurred_at=NOW,
        )
    )
    repaired_at = NOW + timedelta(minutes=10)
    for minute in range(5):
        engine.accept_market_event(bar(minute, available_at=repaired_at, received_at=repaired_at, repair_verified=True))
    engine.accept_market_event(
        ProviderHealthEvent(
            provider="alpaca",
            feed="iex",
            status=MonitorHealth.HEALTHY,
            reason="gap_repair_complete_delayed_observation",
            occurred_at=repaired_at,
        )
    )

    emitted = engine.accept_market_event(quote(provider_time=repaired_at, received_at=repaired_at))

    assert not [item for item in emitted if item.event_type == "notification_request"]


def test_typed_track_fill_control_reaches_the_active_engine_lifecycle() -> None:
    engine = LiveMonitorEngine(
        session_id="session-1",
        evidence_resolver=lambda _bars, _quote: evidence(data_through=NOW + timedelta(minutes=5)),
    )
    for minute in range(5):
        engine.accept_market_event(bar(minute))
    at = NOW + timedelta(minutes=5, seconds=2)
    entry = next(
        item
        for item in engine.accept_market_event(quote(received_at=at, provider_time=at))
        if item.event_type == "notification_request"
    )
    control = parse_control(
        '{"schema_version":1,"command":"track_fill","setup_id":"'
        + entry.payload["plan_id"]
        + '","actual_fill":"100.04"}'
    )

    tracked = engine.track_setup(control.setup_id, actual_fill=control.actual_fill, at=at + timedelta(seconds=1))

    assert tracked[0].event_type == "lifecycle_transition"
    assert tracked[0].payload["to_state"] == "tracked"
    assert tracked[0].payload["actual_fill"] == "100.04"
