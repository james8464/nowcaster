"""Protocol-bound, append-only state for the local paper-only signal service.

This module deliberately contains no transport, notification delivery, account, or
execution integration.  Its persisted records are evidence of a research service,
not instructions to transact.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.research.round_two_contracts import _utc
from src.research.trend_advisor import TrendAdvisorSuggestion

_ACTION_WORDS = frozenset({"buy", "sell", "order", "broker", "execute", "execution", "position", "trade"})


def _bounded_research_text(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 512:
        raise ValueError(f"{label} must be non-empty and bounded")
    words = {word.strip(".,:;!?()[]{}\"'").lower() for word in normalized.split()}
    if words & _ACTION_WORDS:
        raise ValueError(f"{label} must not contain action-shaped language")
    return normalized


class LiveSignalEvent(BaseModel):
    """One append-only, non-executable service event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        "started",
        "stopped",
        "provider_health",
        "gap",
        "reconnect",
        "evaluated",
        "published",
        "abstaining",
        "notification_attempt",
        "notification_outcome",
    ]
    at: datetime
    detail: str | None = Field(default=None, max_length=512)
    posture: Literal["long_research", "stand_aside"] | None = None
    candidate_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    notification_outcome: Literal["not_requested", "suppressed", "delivered", "failed"] | None = None

    @field_validator("at")
    @classmethod
    def _utc_timestamp(cls, value: datetime) -> datetime:
        return _utc(value, "live signal event timestamp")

    @field_validator("detail")
    @classmethod
    def _research_only_detail(cls, value: str | None) -> str | None:
        return None if value is None else _bounded_research_text(value, "event detail")

    @model_validator(mode="after")
    def _event_shape_is_bounded(self) -> LiveSignalEvent:
        if self.kind == "published" and (self.posture != "long_research" or self.candidate_hash is None):
            raise ValueError("published event requires a paper-only research posture and candidate")
        if self.kind == "abstaining" and self.posture not in {None, "stand_aside"}:
            raise ValueError("abstaining event cannot carry a research posture")
        if self.kind == "notification_outcome" and self.notification_outcome is None:
            raise ValueError("notification outcome event requires an outcome")
        if self.kind != "notification_outcome" and self.notification_outcome is not None:
            raise ValueError("notification outcome belongs only to its outcome event")
        return self

    @classmethod
    def started(cls, *, now: datetime) -> LiveSignalEvent:
        return cls(kind="started", at=now, detail="service_started")

    @classmethod
    def stopped(cls, *, now: datetime, reason: str) -> LiveSignalEvent:
        return cls(kind="stopped", at=now, detail=reason)


class LiveSignalState(BaseModel):
    """Current local research-service state; never an executable instruction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["stopped", "warming", "abstaining", "published", "stale", "failed"]
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_at: datetime
    evaluated_at: datetime | None = None
    reasons: tuple[str, ...] = Field(default=(), max_length=16)
    suggestion: TrendAdvisorSuggestion | None = None

    @field_validator("updated_at", "evaluated_at")
    @classmethod
    def _utc_times(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value, "live signal state timestamp")

    @field_validator("reasons")
    @classmethod
    def _bounded_reasons(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_bounded_research_text(value, "state reason") for value in values)

    @model_validator(mode="after")
    def _state_shape_is_safe(self) -> LiveSignalState:
        if self.suggestion is not None and self.suggestion.protocol_hash != self.protocol_hash:
            raise ValueError("state suggestion protocol mismatch")
        if self.kind == "published":
            if self.suggestion is None or self.suggestion.posture != "long_research":
                raise ValueError("published state requires a long research suggestion")
            if self.suggestion.expires_at <= self.updated_at:
                raise ValueError("published state cannot carry an expired suggestion")
        elif self.suggestion is not None:
            raise ValueError("only published state may carry a suggestion")
        return self


class SignalEventLedger:
    """A small sealed JSONL ledger bound to one immutable round protocol."""

    _MANIFEST = "signal-events-manifest.json"
    _EVENTS = "signal-events.jsonl"

    def __init__(self, directory: Path, *, protocol_hash: str):
        if not isinstance(protocol_hash, str) or len(protocol_hash) != 64 or any(
            character not in "0123456789abcdef" for character in protocol_hash
        ):
            raise ValueError("protocol hash must be a lowercase SHA-256 digest")
        self.directory = Path(directory)
        self.protocol_hash = protocol_hash
        self.directory.mkdir(parents=True, exist_ok=True)
        self._bind_protocol()

    @property
    def events_path(self) -> Path:
        return self.directory / self._EVENTS

    def _bind_protocol(self) -> None:
        manifest = self.directory / self._MANIFEST
        payload = {"format": "live-paper-signal-events-v1", "protocol_hash": self.protocol_hash}
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            descriptor = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                existing = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("signal event protocol manifest is unreadable") from exc
            if existing != payload:
                raise ValueError("signal event protocol mismatch") from None
            return
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def append(self, event: LiveSignalEvent) -> None:
        if not isinstance(event, LiveSignalEvent):
            raise TypeError("ledger accepts LiveSignalEvent records only")
        line = event.model_dump_json(exclude_none=True) + "\n"
        descriptor = os.open(self.events_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, line.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def events(self) -> tuple[LiveSignalEvent, ...]:
        if not self.events_path.exists():
            return ()
        items: list[LiveSignalEvent] = []
        try:
            with self.events_path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        raise ValueError(f"signal event ledger has blank line {line_number}")
                    items.append(LiveSignalEvent.model_validate_json(line))
        except (OSError, ValueError) as exc:
            raise ValueError("signal event ledger is unreadable") from exc
        return tuple(items)


def should_publish(
    previous: TrendAdvisorSuggestion | None,
    suggestion: TrendAdvisorSuggestion,
    now: datetime,
) -> bool:
    """Whether a fresh, materially different paper-only research posture merits publication."""
    now = _utc(now, "publication timestamp")
    if suggestion.posture != "long_research" or suggestion.expires_at <= now:
        return False
    if suggestion.available_at is None or suggestion.available_at > now:
        return False
    if previous is None:
        return True
    return (
        previous.posture,
        previous.candidate_hash,
        previous.entry_low,
        previous.entry_high,
        previous.invalidation,
        previous.target,
        previous.expires_at,
    ) != (
        suggestion.posture,
        suggestion.candidate_hash,
        suggestion.entry_low,
        suggestion.entry_high,
        suggestion.invalidation,
        suggestion.target,
        suggestion.expires_at,
    )
