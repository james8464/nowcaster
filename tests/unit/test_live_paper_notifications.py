from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.research.broker_execution_adapter import DisabledBrokerExecutionAdapter
from src.research.live_paper_notifications import PaperResearchNotification, build_notification
from src.research.live_paper_signals import LiveSignalState
from src.research.trend_advisor import TrendAdvisorSuggestion

NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)
PROTOCOL = "a" * 64


def published(*, at=NOW, symbol="BTCUSDT", target="103"):
    suggestion = TrendAdvisorSuggestion(
        symbol=symbol,
        strategy_id="research-trend",
        round_id="round-2",
        protocol_hash=PROTOCOL,
        source_hash="b" * 64,
        candidate_hash="c" * 64,
        evidence_hash="d" * 64,
        policy_hash="e" * 64,
        posture="long_research",
        decision_at=at,
        available_at=at,
        expires_at=at + timedelta(seconds=10),
        entry_low=Decimal("100"),
        entry_high=Decimal("101"),
        invalidation=Decimal("99"),
        target=Decimal(target),
        reasons=("trend_aligned", "candidate_confirmed"),
    )
    return LiveSignalState(
        kind="published",
        protocol_hash=PROTOCOL,
        updated_at=at,
        evaluated_at=at,
        suggestion=suggestion,
    )


def test_notification_requires_explicit_opt_in_and_contains_no_levels_or_actions():
    state = published()
    assert build_notification(state, enabled=False, now=NOW) is None
    assert build_notification(state, enabled=1, now=NOW) is None
    result = build_notification(state, enabled=True, now=NOW)
    assert result is not None
    assert "Paper-only research posture — not a trade instruction" in result.body
    assert "BTCUSDT" in result.body and "2026-09-21T12:00:10Z" in result.body
    assert result.destination == "strategy_lab_evidence"
    assert result.paper_only is True
    assert result.cooldown_key == f"{PROTOCOL}:BTCUSDT"
    assert (
        not {"entry_low", "entry_high", "invalidation", "target", "size", "order_id"} & type(result).model_fields.keys()
    )
    assert not any(word in result.body.lower() for word in ("buy", "sell", "guaranteed", "profit", "order", "size"))


@pytest.mark.parametrize("kind", ["stopped", "warming", "abstaining", "stale", "failed"])
def test_only_published_service_state_can_create_a_notification(kind):
    state = LiveSignalState(kind=kind, protocol_hash=PROTOCOL, updated_at=NOW)
    assert build_notification(state, enabled=True, now=NOW) is None


def test_expiry_boundary_future_update_and_missing_evaluation_fail_closed():
    state = published()
    assert build_notification(state, enabled=True, now=NOW + timedelta(seconds=9)) is not None
    assert build_notification(state, enabled=True, now=NOW + timedelta(seconds=10)) is None
    assert build_notification(state, enabled=True, now=NOW - timedelta(seconds=1)) is None
    assert build_notification(state.model_copy(update={"evaluated_at": None}), enabled=True, now=NOW) is None
    assert (
        build_notification(state.model_copy(update={"reasons": ("continuity_warmup",)}), enabled=True, now=NOW) is None
    )


def test_unchanged_suggestion_suppressed_and_material_change_accepted():
    state = published()
    assert build_notification(state, previous=state.suggestion, enabled=True, now=NOW) is None
    changed = published(target="104")
    assert build_notification(changed, previous=state.suggestion, enabled=True, now=NOW) is not None
    original = build_notification(state, enabled=True, now=NOW)
    changed_notice = build_notification(changed, enabled=True, now=NOW)
    assert original.material_key != changed_notice.material_key
    later_delivery = build_notification(state, enabled=True, now=NOW + timedelta(seconds=1))
    assert later_delivery.material_key == original.material_key


def test_cooldown_survives_record_round_trip_and_is_scoped_by_symbol():
    first = build_notification(published(), enabled=True, now=NOW)
    retained = PaperResearchNotification.model_validate_json(first.model_dump_json())
    within = NOW + timedelta(seconds=299)
    assert build_notification(published(at=within), enabled=True, now=within, last_notification=retained) is None
    assert (
        build_notification(
            published(at=within, symbol="ETHUSDT"),
            enabled=True,
            now=within,
            last_notification=retained,
        )
        is not None
    )
    elapsed = NOW + timedelta(seconds=300)
    assert build_notification(published(at=elapsed), enabled=True, now=elapsed, last_notification=retained) is not None


def test_identical_material_and_future_notification_history_fail_closed():
    state = published()
    notice = build_notification(state, enabled=True, now=NOW)
    assert build_notification(state, enabled=True, now=NOW, last_notification=notice) is None
    future = build_notification(published(at=NOW + timedelta(seconds=1)), enabled=True, now=NOW + timedelta(seconds=1))
    assert build_notification(state, enabled=True, now=NOW, last_notification=future) is None


def test_cross_protocol_history_and_unvalidated_model_copy_are_rejected():
    state = published()
    other = state.suggestion.model_copy(update={"protocol_hash": "f" * 64})
    with pytest.raises(ValueError, match="protocol"):
        build_notification(state, previous=other, enabled=True, now=NOW)
    invalid = state.model_copy(update={"suggestion": state.suggestion.model_copy(update={"paper_only": False})})
    with pytest.raises(ValidationError):
        build_notification(invalid, enabled=True, now=NOW)


@pytest.mark.parametrize(
    "update",
    [
        {"body": "Buy BTC now"},
        {"body": "x" * 513},
        {"order_id": "123"},
        {"destination": "execution"},
        {"paper_only": False},
        {"expires_at": NOW},
        {"expires_at": NOW + timedelta(seconds=16)},
        {"cooldown_key": "unbound"},
    ],
)
def test_notification_wire_record_rejects_arbitrary_copy_action_fields_and_invalid_timing(update):
    notice = build_notification(published(), enabled=True, now=NOW)
    with pytest.raises(ValidationError):
        PaperResearchNotification.model_validate(notice.model_dump() | update)


def test_broker_boundary_only_operation_fails_closed():
    adapter = DisabledBrokerExecutionAdapter()
    assert adapter.disabled is True
    with pytest.raises(RuntimeError, match="disabled"):
        adapter.capabilities()
    with pytest.raises(TypeError):
        DisabledBrokerExecutionAdapter(credentials="not-supported")
    assert not any(hasattr(adapter, name) for name in ("connect", "submit", "place_order", "execute", "credentials"))
