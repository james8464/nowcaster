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
from src.strategies.calendars import XNYS_CALENDAR
from src.strategies.types import canonical_hash


class MonitorPersistence(Protocol):
    def create_setup(self, session_id: str, setup_id: str, plan: TradePlan) -> None: ...

    def record_transition(self, transition) -> bool: ...

    def record_finalized_bar(self, session_id: str, bar: MarketBar) -> bool: ...

    def record_decision(self, session_id: str, payload: dict) -> bool: ...

    def record_health_event(self, session_id: str, event: ProviderHealthEvent) -> bool: ...


class EligibilityEvidence(LiveMonitorModel):
    cohort_id: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    dataset_hash: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    evidence_hash: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    strategy_versions: tuple[tuple[str, str], ...] = ()
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
    if evidence.provider == "alpaca":
        session = XNYS_CALENDAR.session_bounds(quote.provider_time)
        if session is None or not session[0] <= quote.provider_time < session[1]:
            reasons.append("regular_session_required")
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
        config_hash: str = "0" * 64,
        decision_interval: BarIntervalValue = "5m",
        evidence_resolver: Callable[[tuple[MarketBar, ...], MarketQuote], EligibilityEvidence | None] | None = None,
        persistence: MonitorPersistence | None = None,
    ):
        self.session_id = session_id
        if len(config_hash) != 64 or any(character not in "0123456789abcdef" for character in config_hash):
            raise ValueError("live monitor configuration hash is invalid")
        self.config_hash = config_hash
        self.decision_interval = decision_interval
        self.evidence_resolver = evidence_resolver
        self.persistence = persistence
        self.ledger = FinalizedBarLedger()
        self.health: dict[tuple[str, str], MonitorHealth] = {}
        self.quotes: dict[tuple[str, str, str], MarketQuote] = {}
        self._sequence = 0
        self._events: dict[str, MonitorWireEvent] = {}
        self._evaluated_buckets: dict[tuple[str, str, str, datetime, datetime], str] = {}
        self._pending: dict[tuple[str, str, str], list[tuple[tuple[MarketBar, ...], MarketBar]]] = {}
        self._decision_watermark: dict[tuple[str, str, str], datetime] = {}
        self._active: dict[tuple[str, str, str], AlertLifecycle] = {}
        self._history: dict[tuple[str, str, str], tuple[MarketBar, ...]] = {}
        self._last_risk_end: dict[tuple[str, str, str], datetime] = {}
        self._last_quote_emitted: dict[tuple[str, str, str], datetime] = {}
        self._continuity_healthy: dict[tuple[str, str, str], bool] = {}
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
            self._history[scope] = self._history[scope][-5_000:]

    def restore_setup(self, plan: TradePlan, *, state: AlertState, actual_fill: Decimal | None = None) -> None:
        scope = (plan.provider, plan.feed, plan.symbol)
        if scope in self._active:
            raise ValueError("only one active setup may be restored per scope")
        self._active[scope] = AlertLifecycle.restore(plan.plan_id, plan, state=state, actual_fill=actual_fill)

    def track_setup(self, setup_id: str, *, actual_fill: Decimal, at: datetime) -> tuple[MonitorWireEvent, ...]:
        lifecycle = next((item for item in self._active.values() if item.setup_id == setup_id), None)
        if lifecycle is None:
            raise ValueError("active setup was not found")
        return tuple(self._transition(lifecycle, AlertState.TRACKED, at, "operator_fill_tracked", actual_fill))

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
        if len(self._events) > 4_096:
            self._events.pop(next(iter(self._events)))
        self._sequence += 1
        return event

    def accept_market_event(self, event: MarketEvent) -> tuple[MonitorWireEvent, ...]:
        if isinstance(event, ProviderHealthEvent):
            self.health[(event.provider, event.feed)] = event.status
            if event.status is not MonitorHealth.HEALTHY:
                affected = {
                    scope
                    for scope in (*self._continuity_healthy, *self._pending, *self._active)
                    if scope[:2] == (event.provider, event.feed)
                }
                for scope in affected:
                    self._continuity_healthy[scope] = False
                    self._pending.pop(scope, None)
            if self.persistence is not None:
                self.persistence.record_health_event(self.session_id, event)
            return (
                self.emit(
                    "heartbeat" if event.reason == "heartbeat" else "provider_health",
                    event.model_dump(mode="json"),
                    emitted_at=event.occurred_at,
                ),
            )
        if isinstance(event, MarketQuote):
            self.quotes[(event.provider, event.feed, event.symbol)] = event
            provider_scope = (event.provider, event.feed)
            if self.health.get(provider_scope, MonitorHealth.WARMING) in {
                MonitorHealth.WARMING,
                MonitorHealth.HEALTHY,
            }:
                self.health[provider_scope] = MonitorHealth.HEALTHY
            scope = (event.provider, event.feed, event.symbol)
            result: list[MonitorWireEvent] = []
            previous = self._last_quote_emitted.get(scope)
            if previous is None or event.provider_time - previous >= timedelta(seconds=1):
                result.append(self.emit("quote", event.model_dump(mode="json"), emitted_at=event.received_at))
                self._last_quote_emitted[scope] = event.provider_time
            pending = self._pending.get(scope, [])
            remaining = []
            for pending_bars, pending_bar in pending:
                if event.provider_time >= max(pending_bar.end, pending_bar.available_at):
                    result.extend(self._decide(pending_bars, pending_bar, event))
                else:
                    remaining.append((pending_bars, pending_bar))
            if remaining:
                self._pending[scope] = remaining
            else:
                self._pending.pop(scope, None)
            return tuple(result)
        if not isinstance(event, MarketBar):
            raise TypeError("unsupported market event")
        acceptance = self.ledger.accept(event)
        if acceptance.status == "duplicate":
            return ()
        if self.persistence is not None:
            self.persistence.record_finalized_bar(self.session_id, event)
        result = [self.emit("bar_finalized", event.model_dump(mode="json"), emitted_at=event.received_at)]
        event_scope = (event.provider, event.feed, event.symbol)
        scope = tuple(
            item
            for item in self.ledger.bars
            if (item.provider, item.feed, item.symbol) == (event.provider, event.feed, event.symbol)
        )
        latest = {}
        for item in scope:
            if item.interval == "1m":
                latest[item.start] = item
        contiguous = self._contiguous_tail(tuple(latest[key] for key in sorted(latest)))
        required = max(
            2, {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}[self.decision_interval]
        )
        self._continuity_healthy[event_scope] = len(contiguous) >= required
        latest_risk_end = self._last_risk_end.get(event_scope)
        if (
            event.repair_verified
            or (
                self.health.get((event.provider, event.feed)) is MonitorHealth.HEALTHY
                and self._continuity_healthy[event_scope]
            )
        ) and (latest_risk_end is None or event.end > latest_risk_end):
            self._last_risk_end[event_scope] = event.end
            result.extend(self._monitor_risk(event))
        aggregated_bars = aggregate_finalized(scope, self.decision_interval)
        if event.repair_verified:
            # Verified late bars are replayed for protective exits only. Mark any
            # completed decision buckets consumed so a later quote cannot create
            # a retrospective entry from data unavailable in real time.
            for aggregated in aggregated_bars:
                bucket = (aggregated.provider, aggregated.feed, aggregated.symbol, aggregated.start, aggregated.end)
                self._evaluated_buckets[bucket] = aggregated.bar_id
                if len(self._evaluated_buckets) > 10_000:
                    self._evaluated_buckets.pop(next(iter(self._evaluated_buckets)))
                watermark = self._decision_watermark.get(event_scope)
                if watermark is None or aggregated.end > watermark:
                    self._decision_watermark[event_scope] = aggregated.end
            return tuple(result)
        for aggregated in aggregated_bars:
            bucket = (aggregated.provider, aggregated.feed, aggregated.symbol, aggregated.start, aggregated.end)
            if bucket in self._evaluated_buckets:
                continue
            self._evaluated_buckets[bucket] = aggregated.bar_id
            if len(self._evaluated_buckets) > 10_000:
                self._evaluated_buckets.pop(next(iter(self._evaluated_buckets)))
            watermark = self._decision_watermark.get(event_scope)
            if watermark is not None and aggregated.end <= watermark:
                continue
            self._decision_watermark[event_scope] = aggregated.end
            quote = self.quotes.get(event_scope)
            if quote is None or quote.provider_time < max(aggregated.end, aggregated.available_at):
                pending = self._pending.setdefault(event_scope, [])
                pending.append((scope, aggregated))
                del pending[:-8]
                result.append(self._abstention(aggregated, "awaiting_post_finalization_quote"))
                continue
            result.extend(self._decide(scope, aggregated, quote))
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

    def _decide(self, bars: tuple[MarketBar, ...], aggregated: MarketBar, quote: MarketQuote) -> list[MonitorWireEvent]:
        scope = (aggregated.provider, aggregated.feed, aggregated.symbol)
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
        provider_health = self.health.get((aggregated.provider, aggregated.feed), MonitorHealth.WARMING)
        effective_health = (
            MonitorHealth.HEALTHY
            if provider_health is MonitorHealth.HEALTHY and self._continuity_healthy.get(scope, False)
            else MonitorHealth.RECONNECTING
        )
        decision = evaluate_alert_eligibility(
            evidence,
            quote,
            health=effective_health,
            now=now,
        )
        payload = {
            "provider": aggregated.provider,
            "feed": aggregated.feed,
            "symbol": aggregated.symbol,
            "interval": aggregated.interval,
            "decision_time": aggregated.end.isoformat().replace("+00:00", "Z"),
            "cohort_id": evidence.cohort_id,
            "dataset_hash": evidence.dataset_hash,
            "evidence_hash": evidence.evidence_hash,
            "policy_hash": evidence.policy_hash,
            "strategy_versions": evidence.strategy_versions,
            "config_hash": self.config_hash,
            **decision.model_dump(mode="json"),
        }
        result = [self.emit("decision", payload, emitted_at=now)]
        if self.persistence is not None:
            self.persistence.record_decision(self.session_id, payload)
        active = self._active.get(scope)
        if decision.direction is None:
            operational = {
                "market_data_unhealthy",
                "stale_evidence",
                "stale_quote",
                "provider_feed_mismatch",
                "live_warmup_incomplete",
                "current_signal_unavailable",
                "qualified_mode_required",
                "promotion_required",
                "no_repaint_required",
                "calibration_required",
                "economic_evidence_required",
                "shortability_required",
                "regular_session_required",
            }
            operational_reasons = set(decision.reasons).intersection(operational)
            if operational_reasons:
                unavailable = ProviderHealthEvent(
                    provider=aggregated.provider,
                    feed=aggregated.feed,
                    status=MonitorHealth.STALE,
                    reason="monitoring_unavailable",
                    occurred_at=now,
                )
                if self.persistence is not None:
                    self.persistence.record_health_event(self.session_id, unavailable)
                result.append(self.emit("provider_health", unavailable.model_dump(mode="json"), emitted_at=now))
            elif active is not None and evidence.direction is not active.plan.direction:
                result.extend(self._transition(active, AlertState.CLOSED, now, "eligible_direction_reversal"))
                result.append(self._notification(active.plan, "close", now, "eligible_direction_reversal"))
                self._active.pop(scope, None)
            elif active is not None:
                result.extend(self._transition(active, AlertState.CLOSED, now, "evidence_gate_failed"))
                result.append(self._notification(active.plan, "close", now, "evidence_gate_failed"))
                self._active.pop(scope, None)
            return result
        if active is not None and active.plan.direction is decision.direction:
            return result
        if active is not None:
            result.extend(self._transition(active, AlertState.CLOSED, now, "eligible_direction_reversal"))
            result.append(self._notification(active.plan, "close", now, "eligible_direction_reversal"))
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
            identity_context={
                "cohort_id": evidence.cohort_id,
                "dataset_hash": evidence.dataset_hash,
                "evidence_hash": evidence.evidence_hash,
                "policy_hash": canonical_hash(
                    {
                        "evidence_policy_hash": evidence.policy_hash,
                        "level_policy": self._level_policy.model_dump(mode="json"),
                    }
                ),
                "strategy_versions": evidence.strategy_versions,
                "config_hash": self.config_hash,
            },
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
        payload = {
            "provider": bar.provider,
            "feed": bar.feed,
            "symbol": bar.symbol,
            "interval": bar.interval,
            "decision_time": bar.end.isoformat().replace("+00:00", "Z"),
            "status": "abstain",
            "reasons": [reason],
        }
        if self.persistence is not None:
            self.persistence.record_decision(self.session_id, payload)
        return self.emit(
            "decision",
            payload,
            emitted_at=bar.received_at,
        )

    def _transition(
        self,
        lifecycle: AlertLifecycle,
        target: AlertState,
        at: datetime,
        reason: str,
        actual_fill: Decimal | None = None,
    ) -> list[MonitorWireEvent]:
        event = LifecycleEvent(
            event_id=canonical_hash((lifecycle.setup_id, target, at, reason)),
            setup_id=lifecycle.setup_id,
            target_state=target,
            occurred_at=at,
            reason=reason,
            actual_fill=actual_fill,
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
        delayed = bar.received_at - bar.end > timedelta(seconds=30)
        if (plan.direction is Direction.LONG and bar.low <= plan.stop) or (
            plan.direction is Direction.SHORT and bar.high >= plan.stop
        ):
            target, category, reason = AlertState.STOPPED, "stop", "protective_stop_touched"
        elif bar.end >= plan.expires_at:
            target, category, reason = AlertState.EXPIRED, "close", "entry_plan_expired"
        elif lifecycle.state is AlertState.TARGET_1 and (
            (plan.direction is Direction.LONG and bar.high >= plan.target_2)
            or (plan.direction is Direction.SHORT and bar.low <= plan.target_2)
        ):
            target, category, reason = AlertState.TARGET_2, "target", "target_2_touched"
        elif lifecycle.state in {AlertState.TRACKED, AlertState.UNTRACKED} and (
            (plan.direction is Direction.LONG and bar.high >= plan.target_1)
            or (plan.direction is Direction.SHORT and bar.low <= plan.target_1)
        ):
            reached_target_2 = (plan.direction is Direction.LONG and bar.high >= plan.target_2) or (
                plan.direction is Direction.SHORT and bar.low <= plan.target_2
            )
            if reached_target_2:
                target_1_reason = "target_1_touched_delayed_observation" if delayed else "target_1_touched"
                target_2_reason = "target_2_touched_delayed_observation" if delayed else "target_2_touched"
                result = self._transition(lifecycle, AlertState.TARGET_1, bar.received_at, target_1_reason)
                result.append(self._notification(plan, "target", bar.received_at, target_1_reason))
                result.extend(self._transition(lifecycle, AlertState.TARGET_2, bar.received_at, target_2_reason))
                result.append(self._notification(plan, "target", bar.received_at, target_2_reason))
                self._active.pop(scope, None)
                return result
            target, category, reason = AlertState.TARGET_1, "target", "target_1_touched"
        if target is None or category is None:
            return []
        if delayed:
            reason += "_delayed_observation"
        result = self._transition(lifecycle, target, bar.received_at, reason)
        result.append(self._notification(plan, category, bar.received_at, reason))
        if target in {AlertState.EXPIRED, AlertState.STOPPED, AlertState.TARGET_2}:
            self._active.pop(scope, None)
        return result


__all__ = ["EligibilityEvidence", "LiveMonitorEngine", "MonitorDecision", "evaluate_alert_eligibility"]
