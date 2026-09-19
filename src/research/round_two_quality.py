"""Causal, append-only observation ingestion and Research Round 2 quality gates."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.research.round_two_contracts import ResearchRoundProtocol, RoundObservation
from src.research.round_two_registry import append_jsonl_fsync, jsonl_writer_lock, load_round_protocol

OBSERVATIONS_FILE = "observations.jsonl"
_ONE_MINUTE = timedelta(minutes=1)


class EligibleSegment(BaseModel):
    """A contiguous clean interval that has completed the protocol warm-up."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    starts_at: datetime
    ends_at: datetime
    coverage: Decimal = Field(ge=0, le=1)


class QualitySummary(BaseModel):
    """Read-only quality view; it deliberately cannot repair retained evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    protocol: ResearchRoundProtocol
    observations: tuple[RoundObservation, ...]

    def reasons_for(
        self,
        decision_at: datetime,
        *,
        fold_starts_at: datetime | None = None,
        fold_ends_at: datetime | None = None,
    ) -> tuple[str, ...]:
        return _reasons_for(
            self.observations,
            self.protocol,
            decision_at,
            fold_starts_at=fold_starts_at,
            fold_ends_at=fold_ends_at,
        )


def _utc_decision(decision_at: datetime) -> datetime:
    if decision_at.tzinfo is None or decision_at.utcoffset() != timedelta(0):
        raise ValueError("decision_at must be an explicit UTC datetime")
    return decision_at.astimezone(UTC)


def _spread_bps(observation: RoundObservation) -> Decimal | None:
    if observation.bid is None or observation.ask is None:
        return None
    midpoint = (observation.bid + observation.ask) / Decimal("2")
    return (observation.ask - observation.bid) / midpoint * Decimal("10000")


def _intrinsic_reason(observation: RoundObservation, protocol: ResearchRoundProtocol) -> str | None:
    if observation.provider_error is not None:
        return "provider_error"
    if observation.close is None:
        return "unfinalized_input"
    spread = _spread_bps(observation)
    if spread is not None and spread > protocol.maximum_spread_bps:
        return "spread_exceeds_maximum"
    return None


def validate_observation(observation: RoundObservation, decision_at: datetime) -> tuple[str, ...]:
    """Return causal exclusions for a retained observation at a UTC decision time."""
    decision_at = _utc_decision(decision_at)
    reasons: list[str] = []
    if observation.available_at > decision_at:
        reasons.append("available_after_decision")
    if observation.available_at <= decision_at and decision_at - observation.available_at > timedelta(seconds=15):
        reasons.append("observation_stale")
    return tuple(reasons)


def load_observations(directory: Path) -> tuple[RoundObservation, ...]:
    """Read retained observations exactly as appended; malformed history is never repaired."""
    path = Path(directory) / OBSERVATIONS_FILE
    if not path.is_file():
        return ()
    observations: list[RoundObservation] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line:
            raise ValueError(f"empty observation ledger line {line_number}")
        try:
            observations.append(RoundObservation.model_validate_json(line))
        except ValueError as error:
            raise ValueError(f"invalid observation ledger line {line_number}") from error
    return tuple(observations)


def append_observations(
    directory: Path, protocol: ResearchRoundProtocol, observations: Sequence[RoundObservation]
) -> QualitySummary:
    """Validate and durably append novel finalized observations, never changing history."""
    protocol = protocol.validated()
    directory = Path(directory)
    retained_protocol = load_round_protocol(directory)
    if retained_protocol.identity_hash != protocol.identity_hash:
        raise ValueError("protocol identity does not match retained round")
    ledger_path = directory / OBSERVATIONS_FILE
    with jsonl_writer_lock(ledger_path):
        existing_observations = load_observations(directory)
        existing = {item.source_key: item for item in existing_observations}
        pending: dict[str, RoundObservation] = {}
        for item in observations:
            item.validate_for(protocol)
            reason = _intrinsic_reason(item, protocol)
            if reason == "unfinalized_input":
                raise ValueError("finalized input is required")
            retained = existing.get(item.source_key) or pending.get(item.source_key)
            if retained is not None:
                if retained != item:
                    raise ValueError("conflicting source key")
                continue
            pending[item.source_key] = item
        append_jsonl_fsync(
            ledger_path,
            [item.model_dump(mode="json") for item in pending.values()],
            writer_lock_held=True,
        )
    return summarize_quality(load_observations(directory), protocol)


def _clean_runs(
    observations: Sequence[RoundObservation], protocol: ResearchRoundProtocol, decision_at: datetime
) -> dict[str, list[list[RoundObservation]]]:
    """Build runs in retained arrival order so late backfills remain exclusion evidence."""
    runs: dict[str, list[list[RoundObservation]]] = {symbol: [] for symbol in protocol.symbols}
    for symbol in protocol.symbols:
        bars = (item for item in observations if item.symbol == symbol and item.available_at <= decision_at)
        current: list[RoundObservation] = []
        last_provider_at: datetime | None = None
        for item in bars:
            clean = _intrinsic_reason(item, protocol) is None
            if not clean:
                if current:
                    runs[symbol].append(current)
                    current = []
                last_provider_at = item.provider_at
                continue
            if last_provider_at is not None and item.provider_at <= last_provider_at:
                if current:
                    runs[symbol].append(current)
                current = []
                continue
            if last_provider_at is not None and item.provider_at != last_provider_at + _ONE_MINUTE:
                if current:
                    runs[symbol].append(current)
                current = []
            current.append(item)
            last_provider_at = item.provider_at
        if current:
            runs[symbol].append(current)
    return runs


def eligible_segments(
    observations: Sequence[RoundObservation], protocol: ResearchRoundProtocol, decision_at: datetime
) -> tuple[EligibleSegment, ...]:
    """Return complete clean runs; no interpolation, gap fill, or backfill is performed."""
    protocol = protocol.validated()
    decision_at = _utc_decision(decision_at)
    segments: list[EligibleSegment] = []
    for symbol, runs in _clean_runs(observations, protocol, decision_at).items():
        for run in runs:
            elapsed = run[-1].provider_at - run[0].provider_at
            if elapsed < timedelta(minutes=protocol.warmup_minutes):
                continue
            expected = int(elapsed / _ONE_MINUTE) + 1
            coverage = Decimal(len(run)) / Decimal(expected)
            if coverage >= protocol.minimum_coverage:
                segments.append(
                    EligibleSegment(
                        symbol=symbol,
                        starts_at=run[0].provider_at,
                        ends_at=run[-1].provider_at,
                        coverage=coverage,
                    )
                )
    return tuple(segments)


def _reasons_for(
    observations: Sequence[RoundObservation],
    protocol: ResearchRoundProtocol,
    decision_at: datetime,
    *,
    fold_starts_at: datetime | None,
    fold_ends_at: datetime | None,
) -> tuple[str, ...]:
    decision_at = _utc_decision(decision_at)
    if (fold_starts_at is None) != (fold_ends_at is None):
        raise ValueError("fold_starts_at and fold_ends_at must be supplied together")
    if fold_starts_at is not None and fold_ends_at is not None:
        fold_starts_at = _utc_decision(fold_starts_at)
        fold_ends_at = _utc_decision(fold_ends_at)
        if fold_ends_at < fold_starts_at or fold_ends_at > decision_at:
            raise ValueError("fold must end on or before decision_at")
    reasons: set[str] = set()
    causal_runs = _clean_runs(observations, protocol, decision_at)
    for symbol in protocol.symbols:
        if fold_starts_at is not None and fold_ends_at is not None:
            expected = int((fold_ends_at - fold_starts_at) / _ONE_MINUTE) + 1
            retained_times = {
                item.provider_at
                for run in causal_runs[symbol]
                for item in run
                if fold_starts_at <= item.provider_at <= fold_ends_at
            }
            if Decimal(len(retained_times)) / Decimal(expected) < protocol.minimum_coverage:
                reasons.add("coverage_below_minimum")
        visible = [item for item in observations if item.symbol == symbol and item.available_at <= decision_at]
        if not visible:
            reasons.add("no_available_observations")
            continue
        latest = max(visible, key=lambda item: item.available_at)
        if decision_at - latest.available_at > timedelta(seconds=protocol.maximum_observation_age_seconds):
            reasons.add("observation_stale")
        if _intrinsic_reason(latest, protocol) is not None:
            reasons.add(_intrinsic_reason(latest, protocol) or "")
            continue
        active_run = causal_runs[symbol][-1] if causal_runs[symbol] else []
        warm_enough = bool(active_run) and active_run[-1].provider_at == latest.provider_at and (
            active_run[-1].provider_at - active_run[0].provider_at >= timedelta(minutes=protocol.warmup_minutes)
        )
        if not warm_enough:
            reasons.add("continuity_warmup")
    return tuple(sorted(reasons))


def summarize_quality(observations: Sequence[RoundObservation], protocol: ResearchRoundProtocol) -> QualitySummary:
    """Create a ledger summary without mutating evidence or granting qualification."""
    protocol = protocol.validated()
    return QualitySummary(protocol=protocol, observations=tuple(observations))
