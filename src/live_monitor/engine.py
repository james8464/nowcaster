from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from src.live_monitor.bars import FinalizedBarLedger, aggregate_finalized
from src.live_monitor.types import (
    BarIntervalValue,
    Direction,
    LiveMonitorModel,
    MarketBar,
    MarketEvent,
    MarketQuote,
    MonitorHealth,
    MonitorWireEvent,
    ProviderHealthEvent,
)


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
    def __init__(self, *, session_id: str, decision_interval: BarIntervalValue = "5m"):
        self.session_id = session_id
        self.decision_interval = decision_interval
        self.ledger = FinalizedBarLedger()
        self.health: dict[tuple[str, str], MonitorHealth] = {}
        self.quotes: dict[tuple[str, str, str], MarketQuote] = {}
        self._sequence = 0
        self._events: dict[str, MonitorWireEvent] = {}
        self._aggregated_ids: set[str] = set()

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
            return (self.emit("quote", event.model_dump(mode="json"), emitted_at=event.received_at),)
        if not isinstance(event, MarketBar):
            raise TypeError("unsupported market event")
        acceptance = self.ledger.accept(event)
        if acceptance.status == "duplicate":
            return ()
        result = [self.emit("bar_finalized", event.model_dump(mode="json"), emitted_at=event.received_at)]
        scope = tuple(
            item
            for item in self.ledger.bars
            if (item.provider, item.feed, item.symbol) == (event.provider, event.feed, event.symbol)
        )
        for aggregated in aggregate_finalized(scope, self.decision_interval):
            if aggregated.bar_id in self._aggregated_ids:
                continue
            self._aggregated_ids.add(aggregated.bar_id)
            result.append(
                self.emit(
                    "decision",
                    {
                        "provider": aggregated.provider,
                        "feed": aggregated.feed,
                        "symbol": aggregated.symbol,
                        "interval": aggregated.interval,
                        "decision_time": aggregated.end.isoformat().replace("+00:00", "Z"),
                        "status": "abstain",
                        "reasons": ["qualified_evidence_unavailable"],
                    },
                    emitted_at=aggregated.received_at,
                )
            )
        return tuple(result)


__all__ = ["EligibilityEvidence", "LiveMonitorEngine", "MonitorDecision", "evaluate_alert_eligibility"]
