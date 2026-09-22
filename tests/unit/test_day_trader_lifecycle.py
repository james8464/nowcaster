"""Prospective lifecycle outcomes never borrow pre-decision candle extremes."""

from datetime import timedelta

import pytest

from src.research.day_trader_decision import gate_suggestion
from src.research.day_trader_lifecycle import LifecycleLedger, LifecycleObservation, PaperLifecycle, advance_lifecycle
from src.research.round_two_contracts import RoundObservation
from tests.integration.test_day_trader_decision import NOW, context, suggestion


def lifecycle():
    return PaperLifecycle.from_report(
        gate_suggestion(suggestion(), context(), NOW), created_at=NOW, maximum_holding_seconds=600
    )


def observation(seconds=118, **changes):
    at = NOW + timedelta(seconds=seconds)
    values = dict(
        provider="binance",
        feed="spot",
        symbol="BTCUSDT",
        provider_at=at,
        received_at=at + timedelta(seconds=1),
        available_at=at + timedelta(seconds=1),
        source_key=f"bar-{seconds}",
        open="101",
        high="103",
        low="100",
        close="102",
        volume="100",
    )
    values.update(changes)
    bar = RoundObservation(**values)
    return LifecycleObservation(bar=bar, evaluated_at=bar.available_at, finalized=True)


def test_overlapping_entry_bar_ignores_extremes_but_retains_prospective_close():
    initial = lifecycle()
    updated = advance_lifecycle(initial, observation(58, low="98", high="105"))
    assert updated.exit_reason is None
    assert initial.last_observation is None
    closed = advance_lifecycle(updated, observation(118, low="98", high="105"))
    assert closed.exit_reason == "invalidation"
    assert closed.completed_at == NOW + timedelta(seconds=119)


@pytest.mark.parametrize(
    "values,expected",
    [({"high": "105"}, "target"), ({"low": "98"}, "invalidation"), ({"low": "98", "high": "105"}, "invalidation")],
)
def test_later_complete_bar_conservatively_resolves_barriers(values, expected):
    current = advance_lifecycle(lifecycle(), observation(58))
    assert advance_lifecycle(current, observation(**values)).exit_reason == expected


def test_pre_entry_extremes_cannot_overrule_later_close():
    assert advance_lifecycle(lifecycle(), observation(58, low="98", high="105", close="104")).exit_reason == "target"


def test_time_limit_uses_later_known_time_without_post_deadline_extremes():
    current = lifecycle()
    for seconds in range(58, 599, 60):
        current = advance_lifecycle(current, observation(seconds))
    late = observation(658, high="105")
    assert advance_lifecycle(current, late).exit_reason == "time_limit"


def test_stale_or_missing_observation_records_expiry_without_claiming_barrier():
    obs = observation(high="105")
    stale = LifecycleObservation(bar=obs.bar, evaluated_at=obs.evaluated_at + timedelta(seconds=16))
    assert advance_lifecycle(lifecycle(), stale).exit_reason == "expired"
    assert (
        advance_lifecycle(lifecycle(), LifecycleObservation(evaluated_at=NOW + timedelta(seconds=120))).exit_reason
        == "expired"
    )


def test_future_nonfinal_wrong_symbol_and_out_of_order_cannot_advance():
    initial = lifecycle()
    obs = observation(58)
    for invalid in (
        obs.model_copy(update={"evaluated_at": NOW}),
        obs.model_copy(update={"finalized": False}),
        observation(symbol="ETHUSDT"),
        observation(-2),
    ):
        assert advance_lifecycle(initial, invalid) == initial
    updated = advance_lifecycle(initial, obs)
    assert advance_lifecycle(updated, observation(58, high="105")) == updated


def test_later_causal_regime_report_closes_without_rewriting_origin():
    at = NOW + timedelta(seconds=118)
    new_suggestion = suggestion(decision_at=at, available_at=at, expires_at=at + timedelta(seconds=5))
    new_context = context(decision_at=at, available_at=at, expires_at=at + timedelta(seconds=5), calendar_blackout=True)
    obs = observation()
    later = LifecycleObservation(
        bar=obs.bar, evaluated_at=obs.evaluated_at, context_report=gate_suggestion(new_suggestion, new_context, at)
    )
    current = advance_lifecycle(lifecycle(), observation(58))
    closed = advance_lifecycle(current, later)
    assert closed.exit_reason == "regime_change"
    assert closed.origin_report.evaluated_at == NOW


def test_stand_aside_expired_creation_and_action_payload_rejected():
    with pytest.raises(ValueError):
        PaperLifecycle.from_report(gate_suggestion(suggestion(), None, NOW), created_at=NOW)
    with pytest.raises(ValueError):
        PaperLifecycle.from_report(gate_suggestion(suggestion(), context(), NOW), created_at=NOW + timedelta(seconds=6))
    with pytest.raises(ValueError):
        PaperLifecycle.model_validate({**lifecycle().model_dump(), "quantity": 1})


def test_ledger_recovery_is_append_only_idempotent_and_protocol_bound(tmp_path):
    ledger = LifecycleLedger(tmp_path, protocol_hash="a" * 64)
    initial = lifecycle()
    ledger.append(initial)
    prefix = ledger.events_path.read_bytes()
    closed = advance_lifecycle(initial, observation(58, high="105", close="104"))
    ledger.append(closed)
    ledger.append(closed)
    assert ledger.events_path.read_bytes().startswith(prefix)
    recovered = LifecycleLedger(tmp_path, protocol_hash="a" * 64)
    assert recovered.events() == (initial, closed)
    assert recovered.latest() == (closed,)
    assert advance_lifecycle(closed, observation(178, low="98")) == closed
    with pytest.raises(ValueError):
        LifecycleLedger(tmp_path, protocol_hash="f" * 64)


def test_ledger_refuses_rewritten_chain_or_torn_tail(tmp_path):
    ledger = LifecycleLedger(tmp_path, protocol_hash="a" * 64)
    initial = lifecycle()
    ledger.append(initial)
    with pytest.raises(ValueError):
        ledger.append(advance_lifecycle(advance_lifecycle(initial, observation(58)), observation(118)))
    with ledger.events_path.open("ab") as stream:
        stream.write(b"{")
    retained = ledger.events_path.read_bytes()
    with pytest.raises(ValueError):
        ledger.append(advance_lifecycle(initial, observation()))
    assert ledger.events_path.read_bytes() == retained


def test_ledger_prevents_live_holding_policy_changes(tmp_path):
    ledger = LifecycleLedger(tmp_path, protocol_hash="a" * 64)
    ledger.append(lifecycle())
    changed = PaperLifecycle.from_report(
        gate_suggestion(suggestion(), context(), NOW), created_at=NOW, maximum_holding_seconds=1200
    )
    with pytest.raises(ValueError):
        ledger.append(changed)


@pytest.mark.parametrize("preceding", [False, True])
def test_any_missing_provider_minute_expires_before_a_later_barrier(preceding):
    current = advance_lifecycle(lifecycle(), observation(58)) if preceding else lifecycle()
    skipped = observation(178 if preceding else 118, high="105")
    assert advance_lifecycle(current, skipped).exit_reason == "expired"


@pytest.mark.parametrize("close,expected", [("98", "invalidation"), ("102", "expired")])
def test_missing_context_cannot_hide_a_known_invalidation(close, expected):
    at = NOW + timedelta(seconds=58)
    missing = gate_suggestion(
        suggestion(decision_at=at, available_at=at, expires_at=at + timedelta(seconds=5)), None, at
    )
    bar = observation(58, low="98", close=close).bar
    later = LifecycleObservation(bar=bar, evaluated_at=bar.available_at, context_report=missing)
    assert advance_lifecycle(lifecycle(), later).exit_reason == expected
