from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.live_monitor.lifecycle import AlertLifecycle
from src.live_monitor.types import AlertState, Direction, LifecycleEvent, TradePlan

NOW = datetime(2026, 8, 26, 14, 0, tzinfo=UTC)
SETUP_ID = "a" * 64


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
    )


def event(ordinal: int, state: AlertState, **updates) -> LifecycleEvent:
    values = {
        "event_id": f"{ordinal:064x}",
        "setup_id": SETUP_ID,
        "target_state": state,
        "occurred_at": NOW + timedelta(seconds=ordinal),
        "reason": state.value,
        "actual_fill": None,
    }
    values.update(updates)
    return LifecycleEvent(**values)


def test_lifecycle_is_monotonic_idempotent_and_tracks_actual_fill() -> None:
    lifecycle = AlertLifecycle(SETUP_ID, plan())

    assert lifecycle.apply(event(1, AlertState.CANDIDATE)).from_state is AlertState.WATCHING
    assert lifecycle.apply(event(2, AlertState.ENTRY_ALERTED)).to_state is AlertState.ENTRY_ALERTED
    tracked = lifecycle.apply(event(3, AlertState.TRACKED, actual_fill=Decimal("100.05")))
    assert tracked is not None and tracked.actual_fill == Decimal("100.05")
    assert lifecycle.apply(event(3, AlertState.TRACKED, actual_fill=Decimal("100.05"))) is None
    assert lifecycle.apply(event(4, AlertState.TARGET_1)).to_state is AlertState.TARGET_1
    assert lifecycle.apply(event(5, AlertState.TARGET_2)).to_state is AlertState.TARGET_2

    with pytest.raises(ValueError, match="terminal"):
        lifecycle.apply(event(6, AlertState.CLOSED))


def test_invalid_transition_or_conflicting_duplicate_never_mutates_state() -> None:
    lifecycle = AlertLifecycle(SETUP_ID, plan())

    with pytest.raises(ValueError, match="transition"):
        lifecycle.apply(event(1, AlertState.TRACKED, actual_fill=Decimal("100")))
    assert lifecycle.state is AlertState.WATCHING

    lifecycle.apply(event(2, AlertState.CANDIDATE))
    with pytest.raises(ValueError, match="conflicting"):
        lifecycle.apply(event(2, AlertState.ENTRY_ALERTED))
    assert lifecycle.state is AlertState.CANDIDATE


@pytest.mark.parametrize(
    "terminal",
    [AlertState.STOPPED, AlertState.CLOSED, AlertState.INVALIDATED, AlertState.EXPIRED],
)
def test_active_setup_can_end_for_each_risk_reason(terminal: AlertState) -> None:
    lifecycle = AlertLifecycle(SETUP_ID, plan())
    lifecycle.apply(event(1, AlertState.CANDIDATE))
    lifecycle.apply(event(2, AlertState.ENTRY_ALERTED))
    lifecycle.apply(event(3, AlertState.UNTRACKED))

    transition = lifecycle.apply(event(4, terminal))

    assert transition is not None and transition.to_state is terminal
