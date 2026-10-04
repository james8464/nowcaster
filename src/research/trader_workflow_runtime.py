"""Opt-in durable diagnostic simulation; never a qualified signal or order."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.research.day_trader_context import CalendarSnapshot
from src.research.round_two_contracts import _utc
from src.research.round_two_registry import (
    _write_first_manifest,
    append_jsonl_fsync,
    jsonl_writer_lock,
    load_round_protocol,
)
from src.research.round_two_runtime import _write_atomic_json
from src.research.trader_workflow import WorkflowPolicy, parse_observations, select_setups
from src.research.trader_workflow_account import WorkflowAccount, WorkflowTransition, advance_account
from src.strategies.types import canonical_hash, canonical_json

WORKFLOW_DIRECTORY = "diagnostic-workflow-v1"
MAX_RECORD_BYTES = 1024 * 1024
_WRITERS = {}
IMPLEMENTATION_SOURCES = (
    "trader_workflow_runtime.py",
    "trader_workflow.py",
    "trader_workflow_account.py",
    "day_trader_context.py",
    "round_two_contracts.py",
    "trend_advisor.py",
    "live_paper_signal_runtime.py",
)


def _directory(directory):
    directory = Path(directory).expanduser().resolve()
    if "ProspectiveStudies" in directory.parts or "live-paper-study" in directory.parts:
        raise ValueError("protected study cannot enable diagnostic workflow")
    root = directory / WORKFLOW_DIRECTORY
    if root.is_symlink() or root.exists() and any(p.is_symlink() for p in root.iterdir()):
        raise ValueError("symlinked workflow evidence is refused")
    return directory


def _implementation_hash():
    root = Path(__file__).resolve().parent
    sources = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in IMPLEMENTATION_SOURCES}
    sources["strategies/types.py"] = hashlib.sha256((root.parent / "strategies/types.py").read_bytes()).hexdigest()
    return canonical_hash(sources)


def _decode_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate workflow JSON field")
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=unique)


def _read_json(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_RECORD_BYTES + 1)
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError("oversized workflow evidence")
    return _decode_json(data)


def _signature(path):
    try:
        stat = path.stat()
    except FileNotFoundError as error:
        raise ValueError("missing workflow evidence") from error
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def _source_times(account, features):
    return (
        max(
            account.activated_at,
            max((r.quote_provider_at or r.provider_at for r in features), default=account.activated_at),
        ),
        max(account.activated_at, max((r.available_at for r in features), default=account.activated_at)),
    )


def _head(directory, manifest):
    root = directory / WORKFLOW_DIRECTORY
    if (root / "error.json").exists():
        raise ValueError("workflow collection evidence unavailable")
    head = _read_json(root / "head.json")
    if set(head) != {"sequence", "hash", "manifestHash", "journalStat", "lastOffset", "clockAt"}:
        raise ValueError("invalid workflow checkpoint")
    if head["manifestHash"] != manifest["hash"] or head["journalStat"] != _signature(root / "transitions.jsonl"):
        raise ValueError("workflow journal checkpoint mismatch")
    if type(head["sequence"]) is not int or head["sequence"] < 0 or type(head["lastOffset"]) is not int:
        raise ValueError("invalid workflow checkpoint sequence/offset")
    if not 0 <= head["lastOffset"] <= head["journalStat"][2]:
        raise ValueError("invalid workflow checkpoint offset")
    _at(head["clockAt"])
    return head


@dataclass
class _VerifiedWriter:
    state: tuple
    implementation_hash: str
    manifest_stat: list
    head_stat: list
    journal_stat: list
    offset: int
    clock: datetime


def _remember(directory, state, implementation_hash):
    root = directory / WORKFLOW_DIRECTORY
    head = _read_json(root / "head.json")
    writer = _VerifiedWriter(
        state,
        implementation_hash,
        _signature(root / "manifest.json"),
        _signature(root / "head.json"),
        _signature(root / "transitions.jsonl"),
        head["lastOffset"],
        _at(head["clockAt"]),
    )
    # A process normally collects one directory. Limit retained inactive writers.
    if directory not in _WRITERS and len(_WRITERS) >= 8:
        _WRITERS.pop(next(iter(_WRITERS)))
    _WRITERS[directory] = writer
    return writer


def _writer(directory, policy):
    root = directory / WORKFLOW_DIRECTORY
    implementation_hash = _implementation_hash()
    writer = _WRITERS.get(directory)
    if writer is not None and (
        writer.state[0].policy_hash == policy.identity_hash
        and writer.implementation_hash == implementation_hash
        and writer.manifest_stat == _signature(root / "manifest.json")
        and writer.head_stat == _signature(root / "head.json")
        and writer.journal_stat == _signature(root / "transitions.jsonl")
        and not (root / "error.json").exists()
    ):
        return writer
    _WRITERS.pop(directory, None)
    return _remember(directory, _replay(directory, policy), implementation_hash)


def _write_head(directory, *, count, digest, manifest_hash, offset, clock):
    root = directory / WORKFLOW_DIRECTORY
    _write_atomic_json(
        root / "head.json",
        {
            "sequence": count,
            "hash": digest,
            "manifestHash": manifest_hash,
            "journalStat": _signature(root / "transitions.jsonl"),
            "lastOffset": offset,
            "clockAt": clock.isoformat().replace("+00:00", "Z"),
        },
    )


def _at(value):
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")), "workflow timestamp")


def _bind_manifest(directory, policy):
    path = directory / WORKFLOW_DIRECTORY / "manifest.json"
    manifest = _read_json(path)
    expected = {
        "schemaVersion",
        "paperOnly",
        "protocolHash",
        "policyHash",
        "sourceIdentityHash",
        "implementationHash",
        "activatedAt",
        "policy",
        "warmup",
        "hash",
    }
    if set(manifest) != expected or manifest["paperOnly"] is not True or manifest["schemaVersion"] != 1:
        raise ValueError("workflow manifest identity mismatch")
    identity = (
        policy.round_protocol.identity_hash,
        policy.identity_hash,
        policy.source_identity_hash,
        _implementation_hash(),
    )
    if (
        tuple(manifest[name] for name in ("protocolHash", "policyHash", "sourceIdentityHash", "implementationHash"))
        != identity
    ):
        raise ValueError("workflow manifest identity mismatch")
    if manifest["policy"] != policy.model_dump(mode="json") or manifest["hash"] != canonical_hash(
        {k: v for k, v in manifest.items() if k != "hash"}
    ):
        raise ValueError("workflow manifest identity mismatch")
    activated = _at(manifest["activatedAt"])
    warmup = parse_observations(manifest["warmup"], policy)
    if any(r.available_at > activated or not r.finalized for r in warmup):
        raise ValueError("invalid workflow warmup")
    return manifest, activated, warmup


def _features(rows):
    """One representation per closed minute, newest available quote enrichment."""
    latest = {}
    for row in rows:
        key = (row.symbol, row.provider_at)
        old = latest.get(key)
        if old and any(getattr(old, name) != getattr(row, name) for name in ("open", "high", "low", "close", "volume")):
            raise ValueError("conflicting workflow finalized candle")
        if old is None or (row.available_at, row.quote_available_at or row.available_at, row.source_key) > (
            old.available_at,
            old.quote_available_at or old.available_at,
            old.source_key,
        ):
            latest[key] = row
    # Context currently consumes at most 60 minutes; retain a bounded wider window.
    return tuple(
        r
        for symbol in ("BTCUSDT", "ETHUSDT")
        for r in sorted((r for r in latest.values() if r.symbol == symbol), key=lambda r: r.provider_at)[-200:]
    )


def _new_rows(observations, seen, activated, now, policy):
    rows, pending = [], {}
    for row in parse_observations(observations, policy):
        if row.available_at > now:
            continue
        if not row.finalized or row.close is None:
            raise ValueError("workflow requires finalized observations")
        digest = canonical_hash(row.model_dump(mode="json"))
        prior = seen.get(row.source_key) or pending.get(row.source_key)
        if prior is not None:
            if prior != digest:
                raise ValueError("conflicting workflow duplicate input")
            continue
        # Old history is eligible only as the activation manifest's frozen warmup.
        if row.available_at <= activated:
            continue
        pending[row.source_key] = digest
        rows.append(row)
    rows.sort(key=lambda r: (r.available_at, r.provider_at, r.symbol, r.source_key))
    return tuple(rows)


def _transition(account, features, rows, calendar, now, policy):
    features = _features((*features, *rows))
    decisions = select_setups(policy.round_protocol, features, calendar, now, policy) if rows else ()
    return advance_account(account, decisions, rows, now, policy), features


def _replay(directory, policy):
    if (directory / WORKFLOW_DIRECTORY / "error.json").exists():
        raise ValueError("workflow collection evidence unavailable")
    manifest, activated, features = _bind_manifest(directory, policy)
    if not (directory / WORKFLOW_DIRECTORY / "transitions.jsonl").is_file():
        raise ValueError("missing workflow journal")
    head = _head(directory, manifest)
    account = WorkflowAccount.initial(policy, activated)
    seen = {r.source_key: canonical_hash(r.model_dump(mode="json")) for r in features}
    features = _features(features)
    journal = directory / WORKFLOW_DIRECTORY / "transitions.jsonl"
    if not journal.is_file():
        raise ValueError("missing workflow journal")
    previous, count, decisions = manifest["hash"], 0, ()
    offset, last_offset = 0, 0
    with journal.open("rb") as stream:
        while line := stream.readline(MAX_RECORD_BYTES + 1):
            if len(line) > MAX_RECORD_BYTES or not line.endswith(b"\n"):
                raise ValueError("torn or oversized workflow journal")
            record = _decode_json(line)
            _validate_record(record)
            if record["sequence"] != count + 1 or record["previousHash"] != previous:
                raise ValueError("workflow journal hash mismatch")
            now = _at(record["at"])
            if now < account.last_at:
                raise ValueError("workflow clock regression")
            rows = _new_rows(record["observations"], seen, activated, now, policy)
            if [r.model_dump(mode="json") for r in rows] != record["observations"]:
                raise ValueError("invalid workflow journal observation replay")
            calendar = CalendarSnapshot.model_validate(record["calendar"]) if record["calendar"] is not None else None
            rebuilt, features = _transition(account, features, rows, calendar, now, policy)
            retained = WorkflowTransition.model_validate(record["transition"])
            if rebuilt != retained or _source_times(rebuilt.account, features) != (
                _at(record["sourceAt"]),
                _at(record["updatedAt"]),
            ):
                raise ValueError("workflow transition replay mismatch")
            account, decisions = rebuilt.account, rebuilt.decisions
            seen.update({r.source_key: canonical_hash(r.model_dump(mode="json")) for r in rows})
            previous, count = record["hash"], count + 1
            last_offset, offset = offset, offset + len(line)
    # Detect missing/truncated complete tails as well as torn records. A crash
    # between journal fsync and checkpoint fails closed, never silently rolls back.
    if head["sequence"] != count or head["hash"] != previous or head["lastOffset"] != last_offset:
        raise ValueError("workflow journal checkpoint mismatch")
    if _at(head["clockAt"]) < account.last_at:
        raise ValueError("workflow clock regression")
    return account, decisions, features, seen, previous, count


def _validate_record(record):
    if set(record) != {
        "sequence",
        "previousHash",
        "at",
        "observations",
        "calendar",
        "transition",
        "hash",
        "sourceAt",
        "updatedAt",
    }:
        raise ValueError("invalid workflow journal record")
    if record["hash"] != canonical_hash({k: v for k, v in record.items() if k != "hash"}):
        raise ValueError("workflow journal hash mismatch")


def _bounded_projection(directory, policy):
    """Read verified writer's final journal record, guarded against any file edit.

    Account reconstruction remains the writer's startup/audit responsibility.
    Status never silently repairs a changed checkpoint or journal.
    """
    manifest, activated, warmup = _bind_manifest(directory, policy)
    head = _head(directory, manifest)
    journal = directory / WORKFLOW_DIRECTORY / "transitions.jsonl"
    if not head["sequence"]:
        if head["journalStat"][2] != 0 or head["hash"] != manifest["hash"] or head["lastOffset"] != 0:
            raise ValueError("workflow journal checkpoint mismatch")
        account = WorkflowAccount.initial(policy, activated)
        decisions = ()
        source_at, updated_at = _source_times(account, warmup)
    else:
        if not 0 < head["journalStat"][2] - head["lastOffset"] <= MAX_RECORD_BYTES:
            raise ValueError("invalid workflow journal tail")
        with journal.open("rb") as stream:
            stream.seek(head["lastOffset"])
            data = stream.read(MAX_RECORD_BYTES + 1)
        if not data.endswith(b"\n") or data.count(b"\n") != 1:
            raise ValueError("torn workflow journal tail")
        record = _decode_json(data)
        _validate_record(record)
        transition = WorkflowTransition.model_validate(record["transition"])
        if record["sequence"] != head["sequence"] or record["hash"] != head["hash"]:
            raise ValueError("workflow journal checkpoint mismatch")
        account, decisions = transition.account, transition.decisions
        if (
            (account.policy_hash, account.protocol_hash, account.source_identity_hash)
            != (policy.identity_hash, policy.round_protocol.identity_hash, policy.source_identity_hash)
            or account.activated_at != activated
            or account.last_at != _at(record["at"])
        ):
            raise ValueError("workflow account identity mismatch")
        source_at, updated_at = _at(record["sourceAt"]), _at(record["updatedAt"])
        if not activated <= source_at <= updated_at <= account.last_at:
            raise ValueError("invalid workflow source chronology")
    if _at(head["clockAt"]) < account.last_at:
        raise ValueError("workflow clock regression")
    return account, decisions, source_at, updated_at, _at(head["clockAt"])


def _record_collection_error(directory, now):
    """Sticky separate failure: preserve account evidence, clear positive UI."""
    directory = _directory(directory)
    root = directory / WORKFLOW_DIRECTORY
    with jsonl_writer_lock(root / "transitions.jsonl"):
        _write_first_manifest(
            root / "error.json",
            (
                canonical_json(
                    {
                        "at": now,
                        "reason": "workflow_collection_evidence_unavailable",
                    }
                )
                + "\n"
            ).encode(),
        )


def _camel(value):
    if isinstance(value, dict):
        return {
            key.split("_")[0] + "".join(s.title() for s in key.split("_")[1:]): _camel(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_camel(item) for item in value]
    return value


def _status(
    policy,
    account=None,
    decisions=(),
    *,
    now,
    error=None,
    disabled=False,
    source_rows=(),
    source_at=None,
    updated_at=None,
):
    result = {
        "schemaVersion": 1,
        "paperOnly": True,
        "protocolHash": policy.round_protocol.identity_hash,
        "policyHash": policy.identity_hash,
        "updatedAt": None,
        "state": "disabled" if disabled else "error",
        "reasons": [error] if error else [],
        "decisions": [],
        "account": None,
        "positions": [],
        "recentTrades": [],
        "review": None,
    }
    if account is None:
        return result
    state = (
        "pending_exit"
        if account.pending_exit
        else "position_open"
        if account.position
        else "pending_entry"
        if account.pending_entry
        else "watching"
    )
    reasons = []
    if not account.position and (
        account.daily_loss >= policy.initial_cash * policy.daily_loss_fraction
        or account.daily_entries >= policy.maximum_daily_entries
        or account.cooldown_until
        and now < account.cooldown_until
    ):
        state, reasons = "limited", ["entry_limits_active"]
    if now < account.last_at:
        return _status(policy, now=now, error="workflow_clock_regression")
    if source_at is None:
        source_at, updated_at = _source_times(account, source_rows)
    if now - source_at >= timedelta(seconds=min(15, policy.round_protocol.maximum_observation_age_seconds)):
        state, reasons = "stale", ["workflow_source_stale"]
    values = account.model_dump(mode="json")
    result.update(
        updatedAt=updated_at.isoformat().replace("+00:00", "Z"),
        state=state,
        reasons=reasons,
        decisions=[_camel(d.model_dump(mode="json")) for d in decisions][:2],
        account=_camel({k: v for k, v in values.items() if k not in {"history", "setup_reviews", "position"}}),
        positions=[_camel(values["position"])] if account.position else [],
        recentTrades=_camel(values["history"][-20:]),
        review={
            "completedTrades": account.completed_trades,
            "wins": account.total_wins,
            "losses": account.total_losses,
            "netPnl": str(account.realized_pnl),
            "fees": str(account.fees),
            "maximumDrawdown": str(account.maximum_drawdown),
            "setups": _camel(values["setup_reviews"]),
        },
    )
    return result


def enable_workflow(directory, now=None):
    """Explicit activation; never changes the original round or its ledger."""
    directory = _directory(directory)
    policy = WorkflowPolicy(round_protocol=load_round_protocol(directory))
    now = _utc(now or datetime.now(UTC), "workflow activation")
    root = directory / WORKFLOW_DIRECTORY
    with jsonl_writer_lock(root / "transitions.jsonl"):
        if not (root / "manifest.json").exists():
            if (root / "transitions.jsonl").exists() or (root / "head.json").exists():
                raise ValueError("incomplete workflow activation")
            from src.research.live_paper_signal_runtime import _context_observations
            from src.research.round_two_quality import load_observations

            warmup = parse_observations(_context_observations(directory, load_observations(directory)), policy)
            warmup = _features(tuple(r for r in warmup if r.available_at <= now))
            manifest = {
                "schemaVersion": 1,
                "paperOnly": True,
                "protocolHash": policy.round_protocol.identity_hash,
                "policyHash": policy.identity_hash,
                "sourceIdentityHash": policy.source_identity_hash,
                "implementationHash": _implementation_hash(),
                "activatedAt": now.isoformat().replace("+00:00", "Z"),
                "policy": policy.model_dump(mode="json"),
                "warmup": [r.model_dump(mode="json") for r in warmup],
            }
            manifest["hash"] = canonical_hash(manifest)
            _write_first_manifest(root / "transitions.jsonl", b"")
            _write_head(
                directory, count=0, digest=manifest["hash"], manifest_hash=manifest["hash"], offset=0, clock=now
            )
            _write_first_manifest(root / "manifest.json", (canonical_json(manifest) + "\n").encode())
        writer = _remember(directory, _replay(directory, policy), _implementation_hash())
        account, decisions, features, *_ = writer.state
        return _status(policy, account, decisions, now=account.last_at, source_rows=features)


def advance_workflow(directory, observations, calendar, now):
    """Retain exact new inputs and deterministic transitions under one writer lock."""
    directory = _directory(directory)
    policy = WorkflowPolicy(round_protocol=load_round_protocol(directory))
    now = _utc(now, "workflow advance")
    root = directory / WORKFLOW_DIRECTORY
    if not root.exists():
        return _status(policy, now=now, disabled=True)
    with jsonl_writer_lock(root / "transitions.jsonl"):
        writer = _writer(directory, policy)
        account, decisions, features, seen, previous, count = writer.state
        if now < writer.clock:
            raise ValueError("workflow clock regression")
        rows = _new_rows(observations, seen, account.activated_at, now, policy)
        if not rows and now == account.last_at:
            return _status(policy, account, decisions, now=now, source_rows=features)
        calendar = CalendarSnapshot.model_validate(calendar) if isinstance(calendar, dict) else calendar
        transition, features = _transition(account, features, rows, calendar, now, policy)
        # Receipt-free heartbeats carry no new evidence. Persist only actual
        # economic/state changes (expiry, limits, rollover resets, holding exit).
        if (
            not rows
            and not transition.events
            and transition.account.model_dump(exclude={"last_at", "utc_day"})
            == (account.model_dump(exclude={"last_at", "utc_day"}))
        ):
            head = _read_json(root / "head.json")
            if now > writer.clock:
                _write_head(
                    directory,
                    count=count,
                    digest=previous,
                    manifest_hash=head["manifestHash"],
                    offset=writer.offset,
                    clock=now,
                )
                writer.clock, writer.head_stat = now, _signature(root / "head.json")
            return _status(policy, account, decisions, now=now, source_rows=features)
        source_at, updated_at = _source_times(transition.account, features)
        record = {
            "sequence": count + 1,
            "previousHash": previous,
            "at": now.isoformat().replace("+00:00", "Z"),
            "observations": [r.model_dump(mode="json") for r in rows],
            "calendar": calendar.model_dump(mode="json") if calendar is not None else None,
            "transition": transition.model_dump(mode="json"),
            "sourceAt": source_at.isoformat().replace("+00:00", "Z"),
            "updatedAt": updated_at.isoformat().replace("+00:00", "Z"),
        }
        record["hash"] = canonical_hash(record)
        if len((canonical_json(record) + "\n").encode()) > MAX_RECORD_BYTES:
            raise ValueError("oversized workflow transition")
        offset = writer.journal_stat[2]
        append_jsonl_fsync(root / "transitions.jsonl", [record], writer_lock_held=True)
        head = _read_json(root / "head.json")
        _write_head(
            directory,
            count=count + 1,
            digest=record["hash"],
            manifest_hash=head["manifestHash"],
            offset=offset,
            clock=now,
        )
        seen.update({r.source_key: canonical_hash(r.model_dump(mode="json")) for r in rows})
        _remember(
            directory,
            (transition.account, transition.decisions, features, seen, record["hash"], count + 1),
            writer.implementation_hash,
        )
        return _status(policy, transition.account, transition.decisions, now=now, source_rows=features)


def workflow_status(directory, now=None):
    """Bounded read-only projection; any changed evidence clears positive state."""
    directory = _directory(directory)
    policy = WorkflowPolicy(round_protocol=load_round_protocol(directory))
    now = _utc(now or datetime.now(UTC), "workflow status")
    root = directory / WORKFLOW_DIRECTORY
    if not root.exists():
        return _status(policy, now=now, disabled=True)
    # Read without creating files. The activation lock is retained for enabled
    # workflows, so readers cannot observe a legitimate in-flight append.
    import fcntl

    try:
        with (root / ".transitions.jsonl.lock").open("rb") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            account, decisions, source_at, updated_at, clock = _bounded_projection(directory, policy)
        if now < clock:
            return _status(policy, now=now, error="workflow_clock_regression")
        return _status(policy, account, decisions, now=now, source_at=source_at, updated_at=updated_at)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return _status(policy, now=now, error="workflow_evidence_unavailable")
