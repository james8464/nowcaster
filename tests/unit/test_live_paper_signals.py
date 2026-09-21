from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.research.live_paper_signals import (
    LiveSignalEvent,
    LiveSignalState,
    SignalEventLedger,
    should_publish,
)
from src.research.trend_advisor import TrendAdvisorSuggestion

UTC_NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
HASH = "a" * 64


def fresh_long(*, target: Decimal = Decimal("103")) -> TrendAdvisorSuggestion:
    return TrendAdvisorSuggestion(
        symbol="BTCUSDT",
        strategy_id="research-trend",
        round_id="round-2",
        protocol_hash=HASH,
        source_hash="b" * 64,
        candidate_hash="c" * 64,
        evidence_hash="d" * 64,
        policy_hash="e" * 64,
        posture="long_research",
        decision_at=UTC_NOW,
        available_at=UTC_NOW,
        expires_at=UTC_NOW + timedelta(seconds=10),
        entry_low=Decimal("100"),
        entry_high=Decimal("101"),
        invalidation=Decimal("99"),
        target=target,
        reasons=("trend_aligned", "candidate_confirmed"),
    )


def expired_long() -> TrendAdvisorSuggestion:
    return fresh_long().model_copy(update={"expires_at": UTC_NOW})


def test_ledger_refuses_protocol_mismatch_and_preserves_events(tmp_path):
    ledger = SignalEventLedger(tmp_path, protocol_hash=HASH)
    ledger.append(LiveSignalEvent.started(now=UTC_NOW))

    assert ledger.events()[-1].kind == "started"
    with pytest.raises(ValueError, match="protocol"):
        SignalEventLedger(tmp_path, protocol_hash="b" * 64)
    assert ledger.events()[-1].kind == "started"


def test_ledger_appends_fsync_safe_jsonl_events_in_order(tmp_path):
    ledger = SignalEventLedger(tmp_path, protocol_hash=HASH)
    started = LiveSignalEvent.started(now=UTC_NOW)
    stopped = LiveSignalEvent.stopped(now=UTC_NOW + timedelta(seconds=1), reason="user_requested")

    ledger.append(started)
    ledger.append(stopped)

    reloaded = SignalEventLedger(tmp_path, protocol_hash=HASH)
    assert reloaded.events() == (started, stopped)
    assert (tmp_path / "signal-events.jsonl").read_text().count("\n") == 2


def test_ledger_refuses_orphan_or_corrupt_retained_evidence_without_rewriting_it(tmp_path):
    events_path = tmp_path / "signal-events.jsonl"
    events_path.write_text('{"kind":"started"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="manifest"):
        SignalEventLedger(tmp_path, protocol_hash=HASH)
    assert events_path.read_text(encoding="utf-8") == '{"kind":"started"}\n'
    assert not (tmp_path / "signal-events-manifest.json").exists()

    ledger = SignalEventLedger(tmp_path / "sealed", protocol_hash=HASH)
    ledger.append(LiveSignalEvent.started(now=UTC_NOW))
    retained = ledger.events_path.read_text(encoding="utf-8")
    with ledger.events_path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind":"stopped"')

    with pytest.raises(ValueError, match="unreadable"):
        ledger.append(LiveSignalEvent.stopped(now=UTC_NOW, reason="user_requested"))
    assert ledger.events_path.read_text(encoding="utf-8") == retained + '{"kind":"stopped"'


def test_ledger_refuses_a_valid_but_unterminated_jsonl_record_before_append(tmp_path):
    ledger = SignalEventLedger(tmp_path, protocol_hash=HASH)
    ledger.append(LiveSignalEvent.started(now=UTC_NOW))
    retained = ledger.events_path.read_bytes()
    unterminated = retained.rstrip(b"\n")
    ledger.events_path.write_bytes(unterminated)

    with pytest.raises(ValueError, match="unreadable"):
        ledger.append(LiveSignalEvent.stopped(now=UTC_NOW, reason="user_requested"))
    assert ledger.events_path.read_bytes() == unterminated


def test_events_and_state_reject_action_shaped_or_unbounded_values():
    with pytest.raises(ValidationError):
        LiveSignalEvent(kind="order_submitted", at=UTC_NOW)
    with pytest.raises(ValidationError):
        LiveSignalEvent(kind="started", at=UTC_NOW, detail="x" * 513)
    with pytest.raises(ValidationError):
        LiveSignalState(kind="published", protocol_hash=HASH, updated_at=UTC_NOW, suggestion=fresh_long(), order_id="x")


def test_material_change_and_expiry_control_publication():
    assert should_publish(None, fresh_long(), UTC_NOW)
    assert not should_publish(fresh_long(), fresh_long(), UTC_NOW)
    assert not should_publish(None, expired_long(), UTC_NOW)


def test_material_comparator_requires_exact_posture_candidate_levels_and_expiry():
    previous = fresh_long()

    assert should_publish(previous, fresh_long(target=Decimal("104")), UTC_NOW)
    assert should_publish(
        previous,
        fresh_long().model_copy(update={"candidate_hash": "f" * 64}),
        UTC_NOW,
    )
    assert should_publish(
        previous,
        fresh_long().model_copy(update={"expires_at": UTC_NOW + timedelta(seconds=9)}),
        UTC_NOW,
    )


def test_stand_aside_never_publishes_as_a_research_posture():
    aside = fresh_long().model_copy(
        update={
            "posture": "stand_aside",
            "entry_low": None,
            "entry_high": None,
            "invalidation": None,
            "target": None,
            "reasons": ("trend_not_aligned",),
        }
    )
    assert not should_publish(None, aside, UTC_NOW)


def test_published_state_and_publication_enforce_causal_timestamp_ordering():
    future_decision = fresh_long().model_copy(
        update={
            "decision_at": UTC_NOW + timedelta(seconds=1),
            "available_at": UTC_NOW,
            "expires_at": UTC_NOW + timedelta(seconds=11),
        }
    )
    assert not should_publish(None, future_decision, UTC_NOW)

    with pytest.raises(ValidationError, match="causal"):
        LiveSignalState(
            kind="published",
            protocol_hash=HASH,
            updated_at=UTC_NOW - timedelta(seconds=1),
            suggestion=fresh_long(),
        )
    with pytest.raises(ValidationError, match="evaluated"):
        LiveSignalState(
            kind="warming",
            protocol_hash=HASH,
            updated_at=UTC_NOW,
            evaluated_at=UTC_NOW + timedelta(seconds=1),
        )
