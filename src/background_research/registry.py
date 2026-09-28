"""Root-locked immutable ledger; snapshots are derived, never recovery authority."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.background_research.contracts import (
    LearningBatch,
    LearningCampaign,
    LearningCostPolicy,
    LearningEvent,
    LearningStatus,
)
from src.research.round_two_contracts import ResearchRoundProtocol
from src.research.round_two_registry import append_jsonl_fsync, jsonl_writer_lock, load_round_protocol
from src.strategies.types import canonical_hash, canonical_json

DEFAULT_DIRECTORY = Path.home() / "Library/Application Support/Nowcaster/BackgroundResearch"
TERMINAL_STATES = {"completed", "failed"}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate JSON keys")
    return dict(pairs)


def _safe_directory(path: Path) -> Path:
    path = Path(path).expanduser().resolve()
    if {"ProspectiveStudies", "live-paper-study"}.intersection(path.parts):
        raise ValueError("protected study directories cannot contain background research state")
    if path == path.parent or path == Path.home():
        raise ValueError("background research requires a dedicated directory")
    if any((parent / "protocol.json").exists() for parent in (path, *path.parents)):
        raise ValueError("background research cannot write inside a source study directory")
    return path


def _source(campaign: LearningCampaign) -> ResearchRoundProtocol:
    directory = campaign.source_directory.expanduser().resolve()
    manifest = (directory / "protocol.json").resolve()
    if {"ProspectiveStudies", "live-paper-study"}.intersection((*directory.parts, *manifest.parts)):
        raise ValueError("protected study cannot be used as a learning source")
    return load_round_protocol(directory)


def _validate_source(campaign: LearningCampaign, protocol: ResearchRoundProtocol) -> None:
    if campaign.source_protocol_hash != protocol.identity_hash:
        raise ValueError("source protocol identity mismatch")
    if campaign.schedule != protocol.schedule or campaign.cost_policy != LearningCostPolicy.from_protocol(protocol):
        raise ValueError("campaign schedule and gates must exactly match source protocol")
    families = {(candidate.symbol, candidate.strategy_id) for candidate in protocol.candidates}
    if not set(campaign.symbols).issubset(protocol.symbols) or any(
        (space.symbol, space.strategy_id) not in families for space in campaign.search_spaces
    ):
        raise ValueError("campaign search families must match source protocol")


class _State:
    def __init__(self):
        self.campaigns: dict[str, tuple[LearningCampaign, ResearchRoundProtocol]] = {}
        self.batches: dict[str, LearningBatch] = {}
        self.events: dict[str, list[LearningEvent]] = {}
        self.exposures: list[tuple[str, str, str]] = []

    def key(self, batch: LearningBatch) -> tuple[str, str, str, str]:
        protocol = self.campaigns[batch.campaign_hash][1]
        return protocol.source.provider, protocol.source.feed, batch.symbol, protocol.interval

    def batch_state(self, batch_id: str) -> str:
        states = [event.state for event in self.events[batch_id] if event.kind == "state"]
        return states[-1] if states else "training"

    def can_reserve(self, batch: LearningBatch) -> bool:
        campaign = self.campaigns[batch.campaign_hash][0]
        if batch.symbol not in campaign.symbols or batch.max_attempts != campaign.max_attempts_per_batch:
            raise ValueError("batch does not match campaign symbol or attempt budget")
        schedule = campaign.schedule
        elapsed = batch.training_start - schedule.starts_at
        if elapsed < timedelta(0) or elapsed % timedelta(days=schedule.step_days) != timedelta(0):
            raise ValueError("batch training window must follow the registered schedule")
        if (
            batch.training_end - batch.training_start != timedelta(days=schedule.train_days)
            or batch.validation_end - batch.training_end != timedelta(days=schedule.validation_days)
            or batch.holdout_end - batch.validation_end != timedelta(days=schedule.sealed_test_days)
        ):
            raise ValueError("batch windows must match the registered schedule")
        peers = [old for old in self.batches.values() if self.key(old) == self.key(batch)]
        if batch.batch_id in self.batches:
            return False
        if any(old.created_at > batch.created_at for old in peers):
            return False
        if any(old.data_fingerprint == batch.data_fingerprint for old in peers):
            return False
        if sum(old.utc_day == batch.utc_day for old in peers) >= campaign.max_batches_per_asset_day:
            return False
        own = [old for old in peers if old.campaign_hash == batch.campaign_hash]
        if any(self.batch_state(old.batch_id) not in TERMINAL_STATES for old in own):
            return False
        return not (own and batch.training_start <= max(old.training_start for old in own))

    def apply(self, record: dict[str, Any]) -> None:
        kind = record.get("kind")
        expected = {
            "campaign": {"kind", "campaign", "protocol"},
            "batch": {"kind", "batch"},
            "event": {"kind", "batch_id", "event"},
            "holdout": {"kind", "batch_id", "candidate_hash", "exposure_id"},
        }
        if kind not in expected or set(record) != expected[kind]:
            raise ValueError("unknown ledger record schema")
        if kind == "campaign":
            campaign = LearningCampaign.model_validate(record["campaign"])
            protocol = ResearchRoundProtocol.model_validate(record["protocol"])
            _validate_source(campaign, protocol)
            if any(old.campaign_id == campaign.campaign_id for old, _ in self.campaigns.values()):
                raise ValueError("duplicate campaign identity")
            self.campaigns[campaign.identity_hash] = (campaign, protocol)
        elif kind == "batch":
            batch = LearningBatch.model_validate(record["batch"])
            if not self.can_reserve(batch):
                raise ValueError("duplicate or ineligible batch reservation")
            self.batches[batch.batch_id] = batch
            self.events[batch.batch_id] = []
        elif kind == "event":
            batch = self.batches[record["batch_id"]]
            event = LearningEvent.model_validate(record["event"])
            events = self.events[batch.batch_id]
            attempts = {item.attempt_id: item for item in events if item.kind == "attempt"}
            if event.kind == "attempt":
                if self.batch_state(batch.batch_id) in TERMINAL_STATES:
                    raise ValueError("terminal batch cannot accept another attempt")
                if event.attempt_id in attempts or len(attempts) >= batch.max_attempts:
                    raise ValueError("attempt identity already retained or attempt budget exhausted")
            elif event.kind == "attempt_result":
                attempt = attempts.get(event.attempt_id)
                if attempt is None or attempt.candidate_hash != event.candidate_hash:
                    raise ValueError("attempt result requires its reserved candidate identity")
                if attempt.outcome not in {None, "reserved"} or any(
                    item.kind == "attempt_result" and item.attempt_id == event.attempt_id for item in events
                ):
                    raise ValueError("attempt outcome already retained")
            elif event.kind == "state" and self.batch_state(batch.batch_id) in TERMINAL_STATES:
                raise ValueError("terminal batch state cannot be reset")
            events.append(event)
        else:
            batch = self.batches[record["batch_id"]]
            candidate = record["candidate_hash"]
            # Reuse strict digest validation without widening the public event schema.
            LearningEvent(kind="attempt", attempt_id="holdout", candidate_hash=candidate)
            for old_id, _, _ in self.exposures:
                old = self.batches[old_id]
                if self.key(old) == self.key(batch) and max(old.validation_end, batch.validation_end) < min(
                    old.holdout_end, batch.holdout_end
                ):
                    raise ValueError("holdout range already exposed")
            expected_id = canonical_hash({"batch_id": batch.batch_id, "candidate_hash": candidate})
            if record["exposure_id"] != expected_id:
                raise ValueError("holdout exposure identity mismatch")
            self.exposures.append((batch.batch_id, candidate, expected_id))

    def status(self, campaign_hash: str) -> LearningStatus:
        campaign, protocol = self.campaigns[campaign_hash]
        keys = {
            (protocol.source.provider, protocol.source.feed, symbol, protocol.interval) for symbol in campaign.symbols
        }
        own = [batch for batch in self.batches.values() if batch.campaign_hash == campaign_hash]
        latest = own[-1] if own else None
        attempts = failures = batch_attempts = batch_failures = 0
        for batch in self.batches.values():
            if self.key(batch) not in keys:
                continue
            events = self.events[batch.batch_id]
            count = sum(event.kind == "attempt" for event in events)
            failed = sum(
                event.kind in {"attempt", "attempt_result"} and event.outcome in {"failed", "invalid", "interrupted"}
                for event in events
            )
            attempts += count
            failures += failed
            if latest is not None and latest.batch_id == batch.batch_id:
                batch_attempts, batch_failures = count, failed
        values = dict(
            campaign_hash=campaign_hash,
            campaign_id=campaign.campaign_id,
            attempt_count=attempts,
            failure_count=failures,
            batch_attempt_count=batch_attempts,
            batch_failure_count=batch_failures,
        )
        if latest:
            values.update(
                batch_id=latest.batch_id, state=self.batch_state(latest.batch_id), reason="Reserved bounded batch"
            )
            values["next_eligible_at"] = max(
                datetime.combine(latest.utc_day + timedelta(days=1), datetime.min.time(), tzinfo=UTC),
                latest.holdout_end + timedelta(days=campaign.schedule.step_days),
            )
            for event in self.events[latest.batch_id]:
                if event.kind == "state":
                    values.update(state=event.state, reason=event.reason)
                    if event.next_eligible_at:
                        values["next_eligible_at"] = event.next_eligible_at
                elif event.kind == "checkpoint":
                    values["last_checkpoint"] = event.checkpoint
        return LearningStatus(**values)


class LearningRegistry:
    def __init__(self, root: Path = DEFAULT_DIRECTORY):
        self.root = _safe_directory(root)
        self.ledger = self.root / "events.jsonl"

    @contextmanager
    def _locked(self):
        _safe_directory(self.root)
        for name in ("events.jsonl", ".events.jsonl.lock", "status.json"):
            path = self.root / name
            if path.is_symlink() or (path.exists() and path.stat().st_nlink > 1):
                raise ValueError("registry files cannot link to another directory")
        with jsonl_writer_lock(self.ledger):
            yield

    def _read(self) -> tuple[_State, bytes, list[dict]]:
        state = _State()
        raw = self.ledger.read_bytes() if self.ledger.exists() else b""
        rows = []
        try:
            if raw and not raw.endswith(b"\n"):
                raise ValueError("torn trailing JSON")
            previous = "0" * 64
            for sequence, line in enumerate(raw.splitlines()):
                row = json.loads(line, object_pairs_hook=_unique_object)
                if set(row) != {"sequence", "previous_hash", "record", "record_hash"}:
                    raise ValueError("ledger envelope schema mismatch")
                if type(row["sequence"]) is not int or row["sequence"] != sequence or row["previous_hash"] != previous:
                    raise ValueError("ledger sequence mismatch")
                if row["record_hash"] != canonical_hash(
                    {key: row[key] for key in ("sequence", "previous_hash", "record")}
                ):
                    raise ValueError("ledger hash mismatch")
                state.apply(row["record"])
                previous = row["record_hash"]
                rows.append(row)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise ValueError(f"malformed background research ledger: {error}") from error
        return state, raw, rows

    def _commit(self, state: _State, raw: bytes, rows: list[dict], record: dict) -> None:
        state.apply(record)
        row = {"sequence": len(rows), "previous_hash": rows[-1]["record_hash"] if rows else "0" * 64, "record": record}
        row["record_hash"] = canonical_hash(row)
        status = {key: state.status(key).model_dump(mode="json") for key in state.campaigns}
        staged: list[Path] = []
        replacements: dict[Path, Path] = {}
        backups: dict[Path, Path | None] = {}
        replaced: list[Path] = []
        try:
            for destination, payload in (
                (self.ledger, raw),
                (self.root / "status.json", (canonical_json(status) + "\n").encode()),
            ):
                descriptor, name = tempfile.mkstemp(prefix=".research-stage-", dir=self.root)
                temporary = Path(name)
                staged.append(temporary)
                replacements[destination] = temporary
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                if destination == self.ledger:
                    append_jsonl_fsync(temporary, [row], writer_lock_held=True)
                backup = None
                if destination.exists():
                    descriptor, name = tempfile.mkstemp(prefix=".research-backup-", dir=self.root)
                    backup = Path(name)
                    staged.append(backup)
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(destination.read_bytes())
                        stream.flush()
                        os.fsync(stream.fileno())
                backups[destination] = backup
            # The ledger is recovery authority. A crash between replacements leaves a
            # stale cache, which every read ignores and derives afresh from the ledger.
            for destination, temporary in replacements.items():
                os.replace(temporary, destination)
                replaced.append(destination)
            descriptor = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except BaseException:
            for destination in reversed(replaced):
                backup = backups[destination]
                if backup is None:
                    destination.unlink(missing_ok=True)
                else:
                    os.replace(backup, destination)
            raise
        finally:
            for path in staged:
                path.unlink(missing_ok=True)

    def register(self, campaign: LearningCampaign) -> None:
        campaign = campaign.validated()
        protocol = _source(campaign)
        _validate_source(campaign, protocol)
        source = campaign.source_directory.expanduser().resolve()
        if self.root == source or source in self.root.parents:
            raise ValueError("registry must be separate from source study")
        with self._locked():
            state, raw, rows = self._read()
            for old, _ in state.campaigns.values():
                if old.campaign_id == campaign.campaign_id:
                    if old.identity_hash != campaign.identity_hash:
                        raise ValueError("campaign identity is immutable; register a new campaign")
                    return
            self._commit(
                state,
                raw,
                rows,
                {
                    "kind": "campaign",
                    "campaign": campaign.model_dump(mode="json"),
                    "protocol": protocol.model_dump(mode="json"),
                },
            )

    def reserve_batch(self, batch: LearningBatch) -> bool:
        batch = batch.validated()
        with self._locked():
            state, raw, rows = self._read()
            campaign = state.campaigns[batch.campaign_hash][0]
            _validate_source(campaign, _source(campaign))
            if not state.can_reserve(batch):
                return False
            self._commit(state, raw, rows, {"kind": "batch", "batch": batch.model_dump(mode="json")})
            return True

    def append_event(self, batch_id: str, event: dict) -> None:
        event = LearningEvent.model_validate(event)
        with self._locked():
            state, raw, rows = self._read()
            self._commit(
                state, raw, rows, {"kind": "event", "batch_id": batch_id, "event": event.model_dump(mode="json")}
            )

    def reserve_holdout(self, batch_id: str, candidate_hash: str) -> str:
        exposure_id = canonical_hash({"batch_id": batch_id, "candidate_hash": candidate_hash})
        with self._locked():
            state, raw, rows = self._read()
            self._commit(
                state,
                raw,
                rows,
                {"kind": "holdout", "batch_id": batch_id, "candidate_hash": candidate_hash, "exposure_id": exposure_id},
            )
        return exposure_id

    def read_status(self, campaign_hash: str) -> LearningStatus:
        try:
            with self._locked():
                state, _, _ = self._read()
                return state.status(campaign_hash)
        except (ValueError, OSError, KeyError) as error:
            return LearningStatus(campaign_hash=campaign_hash, state="blocked", reason=str(error))
