"""Pure opt-in notification boundary for accepted live paper research.

No delivery, permission request, networking or service-loop wiring occurs here.
The caller retains the previous suggestion and notification per cooldown key;
the latter should be reserved before delivery so a failed delivery cannot spam.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.research.live_paper_signals import LiveSignalState, should_publish
from src.research.round_two_contracts import _utc
from src.research.trend_advisor import TrendAdvisorSuggestion


def _body(symbol: str, expires_at: datetime) -> str:
    expiry = expires_at.isoformat().replace("+00:00", "Z")
    return f"Paper-only research posture — not a trade instruction. {symbol}. Expires {expiry}."


class PaperResearchNotification(BaseModel):
    """Bounded non-executable payload, suitable for retaining before delivery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    material_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    generated_at: datetime
    expires_at: datetime
    cooldown_key: str = Field(max_length=72)
    paper_only: Literal[True] = True
    destination: Literal["strategy_lab_evidence"] = "strategy_lab_evidence"
    title: Literal["Nowcaster paper research"] = "Nowcaster paper research"
    body: str = Field(max_length=256)

    @field_validator("generated_at", "expires_at")
    @classmethod
    def _timestamps(cls, value: datetime) -> datetime:
        return _utc(value, "notification timestamp")

    @model_validator(mode="after")
    def _bound_payload(self) -> PaperResearchNotification:
        if not self.generated_at < self.expires_at <= self.generated_at + timedelta(seconds=15):
            raise ValueError("notification must be fresh and expiry bounded")
        if self.cooldown_key != f"{self.protocol_hash}:{self.symbol}":
            raise ValueError("notification cooldown key must bind protocol and symbol")
        if self.body != _body(self.symbol, self.expires_at):
            raise ValueError("notification body must contain only fixed paper research text")
        return self


def build_notification(
    state: LiveSignalState,
    *,
    enabled: bool,
    now: datetime,
    previous: TrendAdvisorSuggestion | None = None,
    last_notification: PaperResearchNotification | None = None,
) -> PaperResearchNotification | None:
    """Project a fresh material publication into an opt-in, non-actionable payload.

    A five-minute cooldown applies per protocol and symbol, including failed
    delivery attempts. Inputs are revalidated because model_copy bypasses
    Pydantic validation. A payload is never authorization to execute anything.
    """
    if enabled is not True:
        return None
    now = _utc(now, "notification timestamp")
    state = LiveSignalState.model_validate(state.model_dump())
    if state.kind != "published" or state.reasons or state.evaluated_at is None or state.updated_at > now:
        return None
    suggestion = state.suggestion
    if state.evaluated_at < suggestion.decision_at:
        return None
    if previous is not None:
        previous = TrendAdvisorSuggestion.model_validate(previous.model_dump())
        if previous.protocol_hash != state.protocol_hash:
            raise ValueError("previous suggestion protocol mismatch")
        if previous.symbol != suggestion.symbol:
            previous = None
    if not should_publish(previous, suggestion, now):
        return None
    cooldown_key = f"{state.protocol_hash}:{suggestion.symbol}"
    material = suggestion.model_dump(
        mode="json",
        include={
            "protocol_hash",
            "symbol",
            "posture",
            "candidate_hash",
            "entry_low",
            "entry_high",
            "invalidation",
            "target",
            "expires_at",
        },
    )
    material_key = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if last_notification is not None:
        last_notification = PaperResearchNotification.model_validate(last_notification.model_dump())
        if last_notification.protocol_hash != state.protocol_hash:
            raise ValueError("notification history protocol mismatch")
        if last_notification.generated_at > now:
            return None
        if last_notification.cooldown_key == cooldown_key and (
            last_notification.material_key == material_key
            or now - last_notification.generated_at < timedelta(minutes=5)
        ):
            return None
    return PaperResearchNotification(
        protocol_hash=state.protocol_hash,
        candidate_hash=suggestion.candidate_hash,
        material_key=material_key,
        symbol=suggestion.symbol,
        generated_at=now,
        expires_at=suggestion.expires_at,
        cooldown_key=cooldown_key,
        body=_body(suggestion.symbol, suggestion.expires_at),
    )


__all__ = ["PaperResearchNotification", "build_notification"]
