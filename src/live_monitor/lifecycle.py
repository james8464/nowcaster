from __future__ import annotations

from src.live_monitor.types import AlertState, LifecycleEvent, LifecycleTransition, TradePlan
from src.strategies.types import canonical_hash

_TERMINAL = {
    AlertState.TARGET_2,
    AlertState.STOPPED,
    AlertState.CLOSED,
    AlertState.INVALIDATED,
    AlertState.EXPIRED,
}

_TRANSITIONS: dict[AlertState, set[AlertState]] = {
    AlertState.WATCHING: {AlertState.CANDIDATE},
    AlertState.CANDIDATE: {AlertState.ENTRY_ALERTED, AlertState.INVALIDATED, AlertState.EXPIRED},
    AlertState.ENTRY_ALERTED: {
        AlertState.TRACKED,
        AlertState.UNTRACKED,
        AlertState.STOPPED,
        AlertState.CLOSED,
        AlertState.INVALIDATED,
        AlertState.EXPIRED,
    },
    AlertState.TRACKED: {
        AlertState.TARGET_1,
        AlertState.STOPPED,
        AlertState.CLOSED,
        AlertState.INVALIDATED,
        AlertState.EXPIRED,
    },
    AlertState.UNTRACKED: {
        AlertState.TARGET_1,
        AlertState.STOPPED,
        AlertState.CLOSED,
        AlertState.INVALIDATED,
        AlertState.EXPIRED,
    },
    AlertState.TARGET_1: {
        AlertState.TARGET_2,
        AlertState.STOPPED,
        AlertState.CLOSED,
        AlertState.INVALIDATED,
        AlertState.EXPIRED,
    },
}


class AlertLifecycle:
    def __init__(self, setup_id: str, plan: TradePlan):
        if len(setup_id) != 64:
            raise ValueError("setup_id must be a SHA-256 identity")
        self.setup_id = setup_id
        self.plan = plan
        self.state = AlertState.WATCHING
        self.actual_fill = None
        self._events: dict[str, LifecycleEvent] = {}
        self._transitions: list[LifecycleTransition] = []

    @classmethod
    def restore(cls, setup_id: str, plan: TradePlan, *, state: AlertState) -> AlertLifecycle:
        if state in _TERMINAL:
            raise ValueError("terminal lifecycle cannot be restored as active")
        lifecycle = cls(setup_id, plan)
        lifecycle.state = state
        return lifecycle

    @property
    def transitions(self) -> tuple[LifecycleTransition, ...]:
        return tuple(self._transitions)

    def apply(self, event: LifecycleEvent) -> LifecycleTransition | None:
        if event.setup_id != self.setup_id:
            raise ValueError("lifecycle event belongs to another setup")
        previous_event = self._events.get(event.event_id)
        if previous_event is not None:
            if previous_event != event:
                raise ValueError("conflicting lifecycle event identity")
            return None
        if self.state in _TERMINAL:
            raise ValueError("terminal lifecycle cannot transition")
        if event.target_state not in _TRANSITIONS.get(self.state, set()):
            raise ValueError(f"invalid lifecycle transition from {self.state} to {event.target_state}")
        if event.target_state is AlertState.TRACKED and event.actual_fill is None:
            raise ValueError("tracking requires an actual fill")
        if event.target_state is not AlertState.TRACKED and event.actual_fill is not None:
            raise ValueError("actual fill is accepted only when tracking a setup")

        transition = LifecycleTransition(
            transition_id=canonical_hash((self.setup_id, event.event_id, self.state, event.target_state)),
            event_id=event.event_id,
            setup_id=self.setup_id,
            from_state=self.state,
            to_state=event.target_state,
            occurred_at=event.occurred_at,
            reason=event.reason,
            actual_fill=event.actual_fill,
        )
        self._events[event.event_id] = event
        self._transitions.append(transition)
        self.state = event.target_state
        if event.actual_fill is not None:
            self.actual_fill = event.actual_fill
        return transition


__all__ = ["AlertLifecycle"]
