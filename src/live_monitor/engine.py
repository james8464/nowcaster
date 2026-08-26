from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import Field, model_validator

from src.live_monitor.bars import FinalizedBarLedger, aggregate_finalized
from src.live_monitor.levels import plan_trade_levels
from src.live_monitor.lifecycle import AlertLifecycle
from src.live_monitor.types import (
    AlertState,
    BarIntervalValue,
    Direction,
    LifecycleEvent,
    LiveMonitorModel,
    MarketBar,
    MarketEvent,
    MarketQuote,
    MonitorHealth,
    MonitorWireEvent,
    ProviderHealthEvent,
    TradeLevelPolicy,
    TradePlan,
)
from src.strategies.types import canonical_hash


class MonitorPersistence(Protocol):
    def create_setup(self, session_id: str, setup_id: str, plan: TradePlan) -> None: ...

    def record_transition(self, transition) -> bool: ...


class EligibilityEvidence(LiveMonitorModel):
    provider: str
    feed: str
    symbol: str
    interval: BarIntervalValue
    mode: Literal["development", "walk_forward_learning", "frozen", "paper"]
    promoted: bool
    no_repaint_passed: bool
    calibration_status: str
    economic_evidence_status: str
    direction: Direction
    probability: Decimal = Field(ge=0, le=1)
    vote_margin: Decimal = Field(ge=0, le=1)
    expected_net_edge: Decimal
    breadth: int = Field(ge=0)
    data_through: datetime
    shortable: bool
    easy_to_borrow: bool
    reasons: tuple[str, ...] = ()


class MonitorDecision(LiveMonitorModel):
    status: Literal["long", "short", "abstain"]
    direction: Direction | None
    confidence: Decimal
    expected_net_edge: Decimal
    reasons: tuple[str, ...]
    decided_at: datetime

    @model_validator(mode="after")
    def posture_matches_direction(self) -> MonitorDecision:
        if (self.status == "abstain") != (self.direction is None):
            raise ValueError("abstention must have no direction")
        return self


def evaluate_alert_eligibility(
    evidence: EligibilityEvidence,
    quote: MarketQuote,
    *,
    health: MonitorHealth,
    now: datetime,
    maximum_age: timedelta = timedelta(seconds=30),
    minimum_probability: Decimal = Decimal("0.55"),
    minimum_vote_margin: Decimal = Decimal("0.20"),
    minimum_breadth: int = 2,
) -> MonitorDecision:
    reasons = list(evidence.reasons)
    if evidence.mode not in {"frozen", "paper"}:
        reasons.append("qualified_mode_required")
    if not evidence.promoted:
        reasons.append("promotion_required")
    if not evidence.no_repaint_passed:
        reasons.append("no_repaint_required")
    if evidence.calibration_status != "calibrated":
        reasons.append("calibration_required")
    if evidence.economic_evidence_status != "authenticated":
        reasons.append("economic_evidence_required")
    if (evidence.provider, evidence.feed, evidence.symbol) != (quote.provider, quote.feed, quote.symbol):
        reasons.append("provider_feed_mismatch")
    if health is not MonitorHealth.HEALTHY:
        reasons.append("market_data_unhealthy")
    if now - evidence.data_through > maximum_age or evidence.data_through > now:
        reasons.append("stale_evidence")
    if now - quote.provider_time > maximum_age or quote.provider_time > now:
        reasons.append("stale_quote")
    if evidence.probability < minimum_probability:
        reasons.append("probability_calibration")
    if evidence.vote_margin < minimum_vote_margin:
        reasons.append("vote_margin")
    if evidence.breadth < minimum_breadth:
        reasons.append("minimum_breadth")
    if evidence.expected_net_edge <= 0:
        reasons.append("cost_buffer")
    if (
        evidence.provider == "alpaca"
        and evidence.direction is Direction.SHORT
        and (not evidence.shortable or not evidence.easy_to_borrow)
    ):
        reasons.append("shortability_required")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return MonitorDecision(
        status="abstain" if unique_reasons else evidence.direction.value,
        direction=None if unique_reasons else evidence.direction,
        confidence=evidence.probability,
        expected_net_edge=evidence.expected_net_edge,
        reasons=unique_reasons,
        decided_at=now,
    )


def _event_identity(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


class LiveMonitorEngine:
    def __init__(
        self,
        *,
        session_id: str,
        decision_interval: BarIntervalValue = "5m",
        evidence_resolver: Callable[[tuple[MarketBar, ...], MarketQuote], EligibilityEvidence | None] | None = None,
        persistence: MonitorPersistence | None = None,
    ):
        self.session_id = session_id
        self.decision_interval = decision_interval
        self.evidence_resolver = evidence_resolver
        self.persistence = persistence
        self.ledger = FinalizedBarLedger()
        self.health: dict[tuple[str, str], MonitorHealth] = {}
        self.quotes: dict[tuple[str, str, str], MarketQuote] = {}
        self._sequence = 0
        self._events: dict[str, MonitorWireEvent] = {}
        self._aggregated_ids: set[str] = set()
        self._active: dict[tuple[str, str, str], AlertLifecycle] = {}
        self._history: dict[tuple[str, str, str], tuple[MarketBar, ...]] = {}
        self._level_policy = TradeLevelPolicy(
            atr_multiplier=Decimal("1"),
            maximum_chase_bps=Decimal("10"),
            maximum_stop_atr=Decimal("4"),
            minimum_stop_noise_multiple=Decimal("2"),
            minimum_target_1_r=Decimal("1"),
            minimum_target_2_r=Decimal("1.5"),
            expires_after_bars=3,
        )

    def seed_history(self, bars: tuple[MarketBar, ...]) -> None:
        """Add immutable pre-session warm-up bars without emitting retrospective decisions."""
        grouped: dict[tuple[str, str, str], list[MarketBar]] = {}
        for bar in bars:
            if bar.interval != self.decision_interval:
                raise ValueError("seed history must match the decision interval")
            grouped.setdefault((bar.provider, bar.feed, bar.symbol), []).append(bar)
        for scope, values in grouped.items():
            latest = {item.start: item for item in sorted(values, key=lambda item: (item.start, item.revision))}
            self._history[scope] = tuple(latest[key] for key in sorted(latest))

    def restore_setup(self, plan: TradePlan, *, state: AlertState) -> None:
        scope = (plan.provider, plan.feed, plan.symbol)
        if scope in self._active:
            raise ValueError("only one active setup may be restored per scope")
        self._active[scope] = AlertLifecycle.restore(plan.plan_id, plan, state=state)

    def emit(self, event_type: str, payload: dict, *, emitted_at: datetime) -> MonitorWireEvent:
        event_id = _event_identity((self.session_id, event_type, emitted_at.isoformat(), payload))
        previous = self._events.get(event_id)
        if previous is not None:
            return previous
        event = MonitorWireEvent(
            schema_version=1,
            event_id=event_id,
            sequence=self._sequence,
            event_type=event_type,
            emitted_at=emitted_at,
            payload=payload,
        )
        self._events[event_id] = event
        self._sequence += 1
        return event

    def accept_market_event(self, event: MarketEvent) -> tuple[MonitorWireEvent, ...]:
        if isinstance(event, ProviderHealthEvent):
            self.health[(event.provider, event.feed)] = event.status
            return (
                self.emit(
                    "provider_health",
                    event.model_dump(mode="json"),
                    emitted_at=event.occurred_at,
                ),
            )
        if isinstance(event, MarketQuote):
            self.quotes[(event.provider, event.feed, event.symbol)] = event
            self.health.setdefault((event.provider, event.feed), MonitorHealth.HEALTHY)
            return (self.emit("quote", event.model_dump(mode="json"), emitted_at=event.received_at),)
        if not isinstance(event, MarketBar):
            raise TypeError("unsupported market event")
        acceptance = self.ledger.accept(event)
        if acceptance.status == "duplicate":
            return ()
        self.health.setdefault((event.provider, event.feed), MonitorHealth.HEALTHY)
        result = [self.emit("bar_finalized", event.model_dump(mode="json"), emitted_at=event.received_at)]
        result.extend(self._monitor_risk(event))
        scope = tuple(
            item
            for item in self.ledger.bars
            if (item.provider, item.feed, item.symbol) == (event.provider, event.feed, event.symbol)
        )
        for aggregated in aggregate_finalized(scope, self.decision_interval):
            if aggregated.bar_id in self._aggregated_ids:
                continue
            self._aggregated_ids.add(aggregated.bar_id)
            result.extend(self._decide(scope, aggregated))
        return tuple(result)

    @staticmethod
    def _risk_inputs(
        bars: tuple[MarketBar, ...], direction: Direction
    ) -> tuple[Decimal, Decimal, tuple[Decimal, ...]] | None:
        if len(bars) < 2:
            return None
        ranges: list[Decimal] = []
        previous = bars[0].close
        for bar in bars[-15:]:
            ranges.append(max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous)))
            previous = bar.close
        atr = sum(ranges, Decimal(0)) / Decimal(len(ranges))
        if atr <= 0:
            return None
        recent = bars[-10:]
        if direction is Direction.LONG:
            structure = min(item.low for item in recent)
            anchor = bars[-1].close
            targets = (anchor + atr * Decimal("1.5"), anchor + atr * Decimal("2.5"), anchor + atr * Decimal("4"))
        else:
            structure = max(item.high for item in recent)
            anchor = bars[-1].close
            targets = (anchor - atr * Decimal("1.5"), anchor - atr * Decimal("2.5"), anchor - atr * Decimal("4"))
        return atr, structure, targets

    def _decide(self, bars: tuple[MarketBar, ...], aggregated: MarketBar) -> list[MonitorWireEvent]:
        scope = (aggregated.provider, aggregated.feed, aggregated.symbol)
        quote = self.quotes.get(scope)
        if quote is None:
            return [self._abstention(aggregated, "live_quote_unavailable")]
        if self.evidence_resolver is None:
            return [self._abstention(aggregated, "qualified_evidence_unavailable")]
        live_decision_bars = tuple(aggregate_finalized(bars, self.decision_interval))
        combined = {item.start: item for item in self._history.get(scope, ()) if item.end <= aggregated.end}
        combined.update({item.start: item for item in live_decision_bars if item.end <= aggregated.end})
        decision_bars = self._contiguous_tail(tuple(combined[key] for key in sorted(combined)))
        evidence = self.evidence_resolver(decision_bars, quote)
        if evidence is None:
            return [self._abstention(aggregated, "qualified_evidence_unavailable")]
        now = max(aggregated.received_at, quote.received_at)
        decision = evaluate_alert_eligibility(
            evidence,
            quote,
            health=self.health.get((aggregated.provider, aggregated.feed), MonitorHealth.WARMING),
            now=now,
        )
        payload = {
            "provider": aggregated.provider,
            "feed": aggregated.feed,
            "symbol": aggregated.symbol,
            "interval": aggregated.interval,
            "decision_time": aggregated.end.isoformat().replace("+00:00", "Z"),
            **decision.model_dump(mode="json"),
        }
        result = [self.emit("decision", payload, emitted_at=now)]
        if decision.direction is None:
            return result
        active = self._active.get(scope)
        if active is not None and active.plan.direction is decision.direction:
            return result
        if active is not None:
            result.extend(self._transition(active, AlertState.CLOSED, now, "eligible_direction_reversal"))
            self._active.pop(scope, None)
        risk = self._risk_inputs(decision_bars if len(decision_bars) >= 2 else bars, decision.direction)
        if risk is None:
            result.append(self._abstention(aggregated, "risk_levels_unavailable"))
            return result
        plan = plan_trade_levels(
            quote,
            decision.direction,
            atr=risk[0],
            structural_invalidation=risk[1],
            expected_targets=risk[2],
            decision_interval=self.decision_interval,
            decision_time=aggregated.end,
            policy=self._level_policy,
        )
        if plan is None:
            result.append(self._abstention(aggregated, "risk_reward_infeasible"))
            return result
        lifecycle = AlertLifecycle(plan.plan_id, plan)
        self._active[scope] = lifecycle
        if self.persistence is not None:
            self.persistence.create_setup(self.session_id, plan.plan_id, plan)
        result.extend(self._transition(lifecycle, AlertState.CANDIDATE, now, "eligible_closed_bar_decision"))
        result.extend(self._transition(lifecycle, AlertState.ENTRY_ALERTED, now, "hypothetical_entry_plan_issued"))
        result.extend(self._transition(lifecycle, AlertState.UNTRACKED, now, "awaiting_operator_fill_tracking"))
        result.append(self._notification(plan, "entry", now, "eligible_closed_bar_decision"))
        return result

    @staticmethod
    def _contiguous_tail(bars: tuple[MarketBar, ...]) -> tuple[MarketBar, ...]:
        if not bars:
            return ()
        start = len(bars) - 1
        while start > 0 and bars[start - 1].end == bars[start].start:
            start -= 1
        return bars[start:]

    def _abstention(self, bar: MarketBar, reason: str) -> MonitorWireEvent:
        return self.emit(
            "decision",
            {
                "provider": bar.provider,
                "feed": bar.feed,
                "symbol": bar.symbol,
                "interval": bar.interval,
                "decision_time": bar.end.isoformat().replace("+00:00", "Z"),
                "status": "abstain",
                "reasons": [reason],
            },
            emitted_at=bar.received_at,
        )

    def _transition(
        self, lifecycle: AlertLifecycle, target: AlertState, at: datetime, reason: str
    ) -> list[MonitorWireEvent]:
        event = LifecycleEvent(
            event_id=canonical_hash((lifecycle.setup_id, target, at, reason)),
            setup_id=lifecycle.setup_id,
            target_state=target,
            occurred_at=at,
            reason=reason,
        )
        transition = lifecycle.apply(event)
        if transition is not None and self.persistence is not None:
            self.persistence.record_transition(transition)
        return (
            []
            if transition is None
            else [self.emit("lifecycle_transition", transition.model_dump(mode="json"), emitted_at=at)]
        )

    def _notification(self, plan: TradePlan, category: str, at: datetime, reason: str) -> MonitorWireEvent:
        direction = plan.direction.value
        labels = {
            "entry": f"{plan.symbol}: {direction.title()} setup",
            "target": f"{plan.symbol}: target reached",
            "stop": f"{plan.symbol}: protective stop reached",
            "close": f"{plan.symbol}: close setup",
        }
        payload = {
            **plan.model_dump(mode="json"),
            "category": category,
            "title": labels[category],
            "body": (
                f"Hypothetical {direction} plan · entry {plan.entry_low}–{plan.entry_high} · "
                f"SL {plan.stop} · TP {plan.target_1}/{plan.target_2}. Review before acting."
            ),
            "reason": reason,
        }
        return self.emit("notification_request", payload, emitted_at=at)

    def _monitor_risk(self, bar: MarketBar) -> list[MonitorWireEvent]:
        scope = (bar.provider, bar.feed, bar.symbol)
        lifecycle = self._active.get(scope)
        if lifecycle is None:
            return []
        plan = lifecycle.plan
        target: AlertState | None = None
        category: str | None = None
        reason = ""
        if bar.end >= plan.expires_at:
            target, category, reason = AlertState.EXPIRED, "close", "entry_plan_expired"
        elif (plan.direction is Direction.LONG and bar.low <= plan.stop) or (
            plan.direction is Direction.SHORT and bar.high >= plan.stop
        ):
            target, category, reason = AlertState.STOPPED, "stop", "protective_stop_touched"
        elif lifecycle.state is AlertState.TARGET_1 and (
            (plan.direction is Direction.LONG and bar.high >= plan.target_2)
            or (plan.direction is Direction.SHORT and bar.low <= plan.target_2)
        ):
            target, category, reason = AlertState.TARGET_2, "target", "target_2_touched"
        elif lifecycle.state in {AlertState.TRACKED, AlertState.UNTRACKED} and (
            (plan.direction is Direction.LONG and bar.high >= plan.target_1)
            or (plan.direction is Direction.SHORT and bar.low <= plan.target_1)
        ):
            target, category, reason = AlertState.TARGET_1, "target", "target_1_touched"
        if target is None or category is None:
            return []
        result = self._transition(lifecycle, target, bar.received_at, reason)
        result.append(self._notification(plan, category, bar.received_at, reason))
        if target in {AlertState.EXPIRED, AlertState.STOPPED, AlertState.TARGET_2}:
            self._active.pop(scope, None)
        return result


__all__ = ["EligibilityEvidence", "LiveMonitorEngine", "MonitorDecision", "evaluate_alert_eligibility"]
