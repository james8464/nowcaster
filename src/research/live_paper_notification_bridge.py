"""Local append-only reservation bridge for opt-in macOS paper notifications."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from src.research.live_paper_notifications import PaperResearchNotification, build_notification
from src.research.live_paper_signal_runtime import read_live_signal_status
from src.research.live_paper_signals import LiveSignalEvent, LiveSignalState, SignalEventLedger
from src.research.round_two_contracts import _utc
from src.research.round_two_registry import append_jsonl_fsync, jsonl_writer_lock, load_round_protocol
from src.research.trend_advisor import TrendAdvisorSuggestion
from src.strategies.types import canonical_hash


class DeliveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["attempt", "outcome"]
    at: datetime
    payload: PaperResearchNotification
    suggestion: TrendAdvisorSuggestion
    outcome: Literal["delivered", "failed"] | None = None

    @model_validator(mode="after")
    def valid(self):
        _utc(self.at, "delivery timestamp")
        if (self.kind == "outcome") != (self.outcome is not None):
            raise ValueError("delivery record shape")
        if self.at < self.payload.generated_at:
            raise ValueError("delivery clock regression")
        if self.kind == "attempt" and self.at != self.payload.generated_at:
            raise ValueError("attempt timestamp mismatch")
        expected = build_notification(
            # Revalidate the original reservation against the same pure boundary.
            LiveSignalState(
                kind="published",
                protocol_hash=self.suggestion.protocol_hash,
                updated_at=self.suggestion.decision_at,
                evaluated_at=self.suggestion.decision_at,
                suggestion=self.suggestion,
            ),
            enabled=True,
            now=self.payload.generated_at,
        )
        if expected != self.payload:
            raise ValueError("delivery payload does not bind suggestion")
        return self


def _bind_suggestion(protocol, suggestion):
    if suggestion.round_id != protocol.round_id or suggestion.protocol_hash != protocol.identity_hash:
        raise ValueError("notification round mismatch")
    if suggestion.source_hash != canonical_hash(protocol.source.model_dump(mode="json")):
        raise ValueError("notification source mismatch")
    if not any(
        candidate.direction == "long"
        and candidate.symbol == suggestion.symbol
        and candidate.strategy_id == suggestion.strategy_id
        and canonical_hash(candidate.model_dump(mode="json")) == suggestion.candidate_hash
        for candidate in protocol.candidates
    ):
        raise ValueError("notification candidate mismatch")


def _read(path: Path, protocol) -> list[DeliveryRecord]:
    raw = path.read_bytes() if path.exists() else b""
    if raw and not raw.endswith(b"\n"):
        raise ValueError("unterminated notification evidence")
    rows = [DeliveryRecord.model_validate_json(line) for line in raw.splitlines()]
    attempts, outcomes = {}, set()
    for row in rows:
        key = row.payload.material_key
        if row.payload.protocol_hash != protocol.identity_hash:
            raise ValueError("notification protocol mismatch")
        _bind_suggestion(protocol, row.suggestion)
        if row.kind == "attempt":
            if key in attempts:
                raise ValueError("duplicate notification attempt")
            attempts[key] = row
        else:
            if key not in attempts or key in outcomes or row.payload != attempts[key].payload:
                raise ValueError("unbound notification outcome")
            outcomes.add(key)
    return rows


def _bound(directory: Path, identity: str):
    protocol = load_round_protocol(directory)
    if identity != protocol.identity_hash:
        raise ValueError("notification protocol mismatch")
    return SignalEventLedger(directory, protocol_hash=identity), protocol


def read_notification_evidence(directory: Path, *, protocol_hash: str, material_key: str) -> dict:
    """Resolve one retained reservation without changing any research evidence."""
    directory = Path(directory)
    protocol = load_round_protocol(directory)
    if protocol.identity_hash != protocol_hash:
        raise ValueError("notification protocol mismatch")
    rows = _read(directory / "paper-notification-delivery.jsonl", protocol)
    matches = [row for row in rows if row.payload.material_key == material_key]
    if not matches or matches[0].kind != "attempt":
        raise ValueError("unknown notification evidence")
    return {
        "notification": matches[0].payload.model_dump(mode="json"),
        "suggestion": matches[0].suggestion.model_dump(mode="json"),
        "outcome": matches[-1].outcome or "pending",
    }


def reserve_notification(
    directory: Path, *, enabled: bool, protocol_hash: str, now: datetime | None = None
) -> PaperResearchNotification | None:
    now = _utc(now or datetime.now(UTC), "notification timestamp")
    directory = Path(directory)
    ledger, protocol = _bound(directory, protocol_hash)
    path = directory / "paper-notification-delivery.jsonl"
    with jsonl_writer_lock(path):
        rows = _read(path, protocol)
        state = read_live_signal_status(directory, now=now)
        if state.suggestion is None:
            return None
        _bind_suggestion(protocol, state.suggestion)
        previous = next(
            (row for row in reversed(rows) if row.kind == "attempt" and row.payload.symbol == state.suggestion.symbol),
            None,
        )
        payload = build_notification(
            state,
            enabled=enabled,
            now=now,
            previous=previous.suggestion if previous else None,
            last_notification=previous.payload if previous else None,
        )
        if payload is None:
            return None
        row = DeliveryRecord(kind="attempt", at=now, payload=payload, suggestion=state.suggestion)
        append_jsonl_fsync(path, [row.model_dump(mode="json")], writer_lock_held=True)
        ledger.append(
            LiveSignalEvent(
                kind="notification_attempt",
                at=now,
                candidate_hash=payload.candidate_hash,
                detail="paper_research_reserved",
            )
        )
        return payload


def record_notification_outcome(
    directory: Path, *, protocol_hash: str, material_key: str, outcome: str, now: datetime | None = None
) -> str:
    now = _utc(now or datetime.now(UTC), "notification timestamp")
    directory = Path(directory)
    ledger, protocol = _bound(directory, protocol_hash)
    path = directory / "paper-notification-delivery.jsonl"
    with jsonl_writer_lock(path):
        rows = _read(path, protocol)
        matches = [row for row in rows if row.payload.material_key == material_key]
        if len(matches) != 1 or matches[0].kind != "attempt" or outcome not in {"delivered", "failed"}:
            raise ValueError("unknown, completed or invalid notification attempt")
        attempt = matches[0]
        if now < attempt.at:
            raise ValueError("notification clock regression")
        if now >= attempt.payload.expires_at:
            outcome = "failed"
        row = DeliveryRecord(
            kind="outcome", at=now, payload=attempt.payload, suggestion=attempt.suggestion, outcome=outcome
        )
        append_jsonl_fsync(path, [row.model_dump(mode="json")], writer_lock_held=True)
        ledger.append(
            LiveSignalEvent(
                kind="notification_outcome",
                at=now,
                candidate_hash=attempt.payload.candidate_hash,
                notification_outcome=outcome,
            )
        )
        return outcome
