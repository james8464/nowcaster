"""Append-only hypothetical outcomes of retained, causal research decisions.

No fill is asserted: the entry zone is a hypothesis, not an execution. Publication
expiry limits creation; subsequent observations have their own freshness limit.
An observation overlapping creation uses only its prospective closing value.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.research.day_trader_decision import DecisionContextReport
from src.research.round_two_contracts import RoundObservation, _utc
from src.research.round_two_registry import _write_first_manifest, append_jsonl_fsync, jsonl_writer_lock
from src.strategies.types import canonical_hash, canonical_json

LIFECYCLE_POLICY_HASH = canonical_hash(
    dict(
        version="paper-lifecycle-v1",
        freshness_seconds=15,
        maximum_gap_seconds=120,
        ambiguous_bar="invalidation_first",
        overlapping_bar="close_only",
        default_holding_seconds=3600,
    )
)


class LifecycleObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evaluated_at: datetime
    bar: RoundObservation | None = None
    finalized: bool = True
    context_report: DecisionContextReport | None = None

    @field_validator("evaluated_at")
    @classmethod
    def utc_time(cls, value):
        return _utc(value, "lifecycle observation")


class PaperLifecycle(BaseModel):
    """A revision of a hypothesis, retaining its original evidence forever."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    paper_only: Literal[True] = True
    policy_hash: Literal[LIFECYCLE_POLICY_HASH] = LIFECYCLE_POLICY_HASH
    origin_report: DecisionContextReport
    created_at: datetime
    maximum_holding_seconds: int = Field(default=3600, ge=60, le=86400)
    deadline: datetime
    lifecycle_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(default=0, ge=0, le=10000)
    previous_record_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    last_observation: LifecycleObservation | None = None
    exit_reason: Literal["expired", "invalidation", "target", "regime_change", "time_limit"] | None = None
    completed_at: datetime | None = None
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("created_at", "deadline", "completed_at")
    @classmethod
    def utc_times(cls, value):
        return None if value is None else _utc(value, "lifecycle timestamp")

    @model_validator(mode="after")
    def validate_identity(self):
        suggestion = self.origin_report.suggestion
        if suggestion.posture != "long_research" or self.origin_report.context is None:
            raise ValueError("lifecycle requires an accepted context report")
        if not self.origin_report.evaluated_at <= self.created_at < suggestion.expires_at:
            raise ValueError("lifecycle creation requires fresh retained evidence")
        if self.deadline != self.created_at + timedelta(seconds=self.maximum_holding_seconds):
            raise ValueError("lifecycle deadline mismatch")
        identity = dict(
            report_hash=self.origin_report.report_hash,
            created_at=self.created_at.isoformat(),
            maximum_holding_seconds=self.maximum_holding_seconds,
            policy_hash=self.policy_hash,
        )
        if self.lifecycle_hash != canonical_hash(identity):
            raise ValueError("lifecycle identity mismatch")
        if self.revision == 0:
            if any(
                value is not None
                for value in (self.previous_record_hash, self.last_observation, self.exit_reason, self.completed_at)
            ):
                raise ValueError("initial lifecycle cannot carry an outcome")
        elif self.previous_record_hash is None or self.last_observation is None:
            raise ValueError("lifecycle revision requires earlier evidence")
        if (self.exit_reason is None) != (self.completed_at is None):
            raise ValueError("lifecycle completion requires a reason and timestamp")
        if self.completed_at is not None and (
            self.last_observation is None
            or self.completed_at != self.last_observation.evaluated_at
            or self.completed_at <= self.created_at
        ):
            raise ValueError("lifecycle outcome must use the later knowledge timestamp")
        if self.record_hash != canonical_hash(self.model_dump(mode="json", exclude={"record_hash"})):
            raise ValueError("lifecycle record hash mismatch")
        return self

    @classmethod
    def from_report(
        cls, report: DecisionContextReport, *, created_at: datetime, maximum_holding_seconds: int = 3600
    ) -> PaperLifecycle:
        report = DecisionContextReport.model_validate(report.model_dump())
        created_at = _utc(created_at, "lifecycle creation")
        identity = dict(
            report_hash=report.report_hash,
            created_at=created_at.isoformat(),
            maximum_holding_seconds=maximum_holding_seconds,
            policy_hash=LIFECYCLE_POLICY_HASH,
        )
        return _sealed(
            dict(
                origin_report=report,
                created_at=created_at,
                maximum_holding_seconds=maximum_holding_seconds,
                deadline=created_at + timedelta(seconds=maximum_holding_seconds),
                lifecycle_hash=canonical_hash(identity),
            )
        )


def _sealed(values: dict) -> PaperLifecycle:
    values = dict(values)
    if isinstance(values.get("origin_report"), dict):
        values["origin_report"] = DecisionContextReport.model_validate(values["origin_report"])
    if isinstance(values.get("last_observation"), dict):
        values["last_observation"] = LifecycleObservation.model_validate(values["last_observation"])
    unsigned = PaperLifecycle.model_construct(**values, record_hash="0" * 64)
    payload = unsigned.model_dump(mode="json", exclude={"record_hash"})
    return PaperLifecycle.model_validate({**payload, "record_hash": canonical_hash(payload)})


def advance_lifecycle(lifecycle: PaperLifecycle, observation: LifecycleObservation) -> PaperLifecycle:
    """Resolve only later knowable evidence, preserving terminal outcomes.

    Nonfinal, future, foreign or out-of-order observations cannot advance state.
    Missing/stale/gapped evidence expires the hypothesis without inferring a hit.
    A candle crossing the holding deadline cannot establish a timely barrier.
    """
    lifecycle = PaperLifecycle.model_validate(lifecycle.model_dump())
    observation = LifecycleObservation.model_validate(observation.model_dump())
    if lifecycle.completed_at is not None:
        return lifecycle
    previous = lifecycle.last_observation
    previous_at = previous.evaluated_at if previous else lifecycle.created_at
    now, bar = observation.evaluated_at, observation.bar
    if now <= previous_at or not observation.finalized:
        return lifecycle
    if bar is not None:
        if (
            bar.provider != "binance"
            or bar.feed != "spot"
            or bar.symbol != lifecycle.origin_report.suggestion.symbol
            or bar.available_at > now
            or bar.provider_at <= lifecycle.created_at
        ):
            return lifecycle
        if previous and previous.bar and bar.provider_at <= previous.bar.provider_at:
            return lifecycle
    reason = None
    if now >= lifecycle.deadline:
        reason = "time_limit"
    elif (
        bar is None
        or bar.provider_error is not None
        or bar.close is None
        or not bar.close.is_finite()
        or now - bar.provider_at > timedelta(seconds=15)
        or now - previous_at > timedelta(seconds=120)
    ):
        reason = "expired"
    else:
        report = observation.context_report
        if report is not None:
            origin = lifecycle.origin_report
            if (
                report.protocol_hash != origin.protocol_hash
                or report.suggestion.symbol != origin.suggestion.symbol
                or report.suggestion.source_hash != origin.suggestion.source_hash
                or report.context is None
                or report.context.context_protocol_hash != origin.context.context_protocol_hash
                or report.evaluated_at > now
                or report.suggestion.decision_at < bar.provider_at
                or now >= report.suggestion.expires_at
            ):
                return lifecycle
        whole_bar = bar.provider_at - timedelta(minutes=1) >= lifecycle.created_at
        low = bar.low if whole_bar else bar.close
        high = bar.high if whole_bar else bar.close
        if low is None or high is None or not low.is_finite() or not high.is_finite():
            reason = "expired"
        elif low <= lifecycle.origin_report.suggestion.invalidation:
            reason = "invalidation"
        elif high >= lifecycle.origin_report.suggestion.target:
            reason = "target"
        elif report is not None and report.suggestion.posture == "stand_aside":
            reason = "regime_change"
    values = lifecycle.model_dump(exclude={"record_hash"})
    values.update(
        revision=lifecycle.revision + 1,
        previous_record_hash=lifecycle.record_hash,
        last_observation=observation,
        exit_reason=reason,
        completed_at=now if reason else None,
    )
    return _sealed(values)


class LifecycleLedger:
    """Durably retains every revision and verifies its causal transition on read."""

    def __init__(self, directory: Path, *, protocol_hash: str):
        if len(protocol_hash) != 64 or any(c not in "0123456789abcdef" for c in protocol_hash):
            raise ValueError("invalid lifecycle protocol hash")
        self.directory = Path(directory)
        self.protocol_hash = protocol_hash
        self.directory.mkdir(parents=True, exist_ok=True)
        with jsonl_writer_lock(self.events_path):
            self._manifest()
            self._read()

    @property
    def events_path(self):
        return self.directory / "paper-lifecycles.jsonl"

    def _manifest(self):
        manifest = self.directory / "paper-lifecycles-manifest.json"
        payload = (
            canonical_json(dict(protocol_hash=self.protocol_hash, policy_hash=LIFECYCLE_POLICY_HASH)) + "\n"
        ).encode()
        if self.events_path.exists() and not manifest.exists():
            raise ValueError("lifecycle history has no manifest")
        if not _write_first_manifest(manifest, payload) and manifest.read_bytes() != payload:
            raise ValueError("lifecycle protocol mismatch")

    def _check(self, item, latest):
        if item.origin_report.protocol_hash != self.protocol_hash:
            raise ValueError("lifecycle protocol mismatch")
        for retained in latest.values():
            if (
                item.maximum_holding_seconds != retained.maximum_holding_seconds
                or item.origin_report.context.context_protocol_hash
                != retained.origin_report.context.context_protocol_hash
            ):
                raise ValueError("lifecycle policy changed; register a separate ledger")
        previous = latest.get(item.lifecycle_hash)
        if previous is None:
            if item.revision != 0:
                raise ValueError("lifecycle initial revision missing")
        elif (
            item.revision != previous.revision + 1
            or item.previous_record_hash != previous.record_hash
            or item.last_observation is None
            or advance_lifecycle(previous, item.last_observation) != item
        ):
            raise ValueError("lifecycle transition mismatch")
        latest[item.lifecycle_hash] = item

    def _read(self):
        if not self.events_path.exists():
            return ()
        data = self.events_path.read_bytes()
        if data and not data.endswith(b"\n"):
            raise ValueError("unterminated lifecycle evidence")
        items, latest = [], {}
        for line in data.splitlines():
            item = PaperLifecycle.model_validate(json.loads(line))
            self._check(item, latest)
            items.append(item)
        return tuple(items)

    def events(self) -> tuple[PaperLifecycle, ...]:
        with jsonl_writer_lock(self.events_path):
            self._manifest()
            return self._read()

    def latest(self) -> tuple[PaperLifecycle, ...]:
        return tuple({item.lifecycle_hash: item for item in self.events()}.values())

    def append(self, lifecycle: PaperLifecycle) -> None:
        item = PaperLifecycle.model_validate(lifecycle.model_dump())
        with jsonl_writer_lock(self.events_path):
            self._manifest()
            events = self._read()
            if any(previous.record_hash == item.record_hash for previous in events):
                return
            latest = {previous.lifecycle_hash: previous for previous in events}
            self._check(item, latest)
            append_jsonl_fsync(self.events_path, [item.model_dump(mode="json")], writer_lock_held=True)
