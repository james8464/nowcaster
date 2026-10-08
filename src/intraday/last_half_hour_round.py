"""Immutable historical-only round for the registered SPX500 hypothesis."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import random
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean

from src.intraday import last_half_hour, us_regular_sessions
from src.intraday.contracts import ConfirmedBar, InstrumentSpec
from src.intraday.last_half_hour import NY, _full_session, evaluate_last_half_hour
from src.intraday.us_regular_sessions import is_full_us_cash_session

WINDOWS = {
    "development": (date(2022, 1, 1), date(2024, 1, 1)),
    "validation": (date(2024, 1, 1), date(2025, 1, 1)),
    "sealed": (date(2025, 1, 1), date(2026, 1, 1)),
}
PRODUCT = InstrumentSpec(
    provider="oanda_practice", broker_symbol="SPX500_USD", market="us500", product="cfd",
    quote_currency="USD", point_value=Decimal(1),
)
PROTOCOL = {
    "rule": "spx500_first_and_penultimate_half_hours_agree_v1",
    "product": "SPX500_USD",
    "price_scope": "historical_base_exploratory",
    "baseline_slippage_points_per_side": "0.5",
    "stress_slippage_points_per_side": "1.0",
    "conversion_fee_fraction": "0.01",
    "stop_fraction": "0.0025",
    "target_fraction": "0.005",
    "entry_new_york": "15:35",
    "exit_new_york": "16:00",
    "windows": {key: [a.isoformat(), b.isoformat()] for key, (a, b) in WINDOWS.items()},
    "account_fill_claim": False,
    "implementation_sha256": hashlib.sha256(
        Path(__file__).read_bytes() + b"\0" + Path(last_half_hour.__file__).read_bytes()
        + b"\0" + Path(us_regular_sessions.__file__).read_bytes()
    ).hexdigest(),
}


def _read_captures(paths: Sequence[Path], stage: str) -> tuple[list[ConfirmedBar], str]:
    digest = hashlib.sha256()
    bars: list[ConfirmedBar] = []
    requests: list[tuple[datetime, datetime]] = []
    for path in paths:
        content = Path(path).read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        lines = content.splitlines()
        if not lines:
            raise ValueError("empty capture")
        header = json.loads(lines[0])
        if (
            header.get("price_scope") != "historical_base"
            or header.get("instrument") != PRODUCT.model_dump(mode="json")
        ):
            raise ValueError("capture must contain SPX500_USD historical bid/ask bars")
        try:
            requested_start = datetime.fromisoformat(header["requested_start"].replace("Z", "+00:00"))
            requested_end = datetime.fromisoformat(header["requested_end"].replace("Z", "+00:00"))
        except (KeyError, AttributeError, ValueError) as exc:
            raise ValueError("capture requested window is missing or invalid") from exc
        if (
            requested_start.tzinfo is None or requested_start.utcoffset() != timedelta(0)
            or requested_end.tzinfo is None or requested_end.utcoffset() != timedelta(0)
            or requested_start >= requested_end
        ):
            raise ValueError("capture requested window must be chronological UTC")
        requests.append((requested_start, requested_end))
        for line in lines[1:]:
            bar = ConfirmedBar.model_validate_json(line)
            if bar.instrument != PRODUCT or bar.price_scope != "historical_base":
                raise ValueError("capture must contain SPX500_USD historical bid/ask bars")
            if not requested_start <= bar.start < requested_end:
                raise ValueError("capture bar outside requested window")
            bars.append(bar)
    if not bars or len({bar.start for bar in bars}) != len(bars):
        raise ValueError("capture has no bars or duplicate timestamps")
    start, end = WINDOWS[stage]
    requested = sorted(requests)
    if requested[0][0] > datetime.combine(start, time(), UTC) or requested[-1][1] < datetime.combine(end, time(), UTC):
        raise ValueError("captures do not cover registered stage window")
    if any(right[0] > left[1] for left, right in zip(requested, requested[1:], strict=False)):
        raise ValueError("capture requested windows have a gap")
    return sorted(bars, key=lambda bar: bar.start), digest.hexdigest()


def _daily_interval(values: list[Decimal]) -> tuple[str | None, str | None]:
    if len(values) < 20:
        return None, None
    rng = random.Random(20261008)
    floats = [float(value) for value in values]
    sample_means = sorted(mean(rng.choices(floats, k=len(floats))) for _ in range(2000))
    return str(Decimal(str(sample_means[49]))), str(Decimal(str(sample_means[1949])))


def _always_direction(current: Sequence[ConfirmedBar], direction: str) -> Decimal:
    slip = Decimal("0.5")
    entry_bar, exit_bar = current[73], current[77]
    gross = (
        exit_bar.bid_close - slip - entry_bar.ask_open - slip
        if direction == "long"
        else entry_bar.bid_open - slip - exit_bar.ask_close - slip
    )
    return gross - abs(gross) * Decimal("0.01")


def _maximum_drawdown(values: Sequence[Decimal]) -> Decimal:
    equity = peak = worst = Decimal(0)
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _record_rejection(directory: Path, stage: str, reason: str, source_hash: str | None = None) -> None:
    """Retain failed selection requests without opening their sealed capture."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "rejections.jsonl"
    with path.open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        previous = "0" * 64
        for line in stream:
            prior = json.loads(line)
            digest = prior.pop("event_hash")
            if prior.get("previous_hash") != previous or hashlib.sha256(
                json.dumps(prior, sort_keys=True).encode()
            ).hexdigest() != digest:
                raise ValueError("rejection journal hash chain mismatch")
            previous = digest
        event = {
            "at": datetime.now(UTC).isoformat(), "stage": stage, "reason": reason,
            "protocol_sha256": hashlib.sha256(json.dumps(PROTOCOL, sort_keys=True).encode()).hexdigest(),
            "source_sha256": source_hash,
            "previous_hash": previous,
        }
        event["event_hash"] = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
        stream.write((json.dumps(event, sort_keys=True) + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())


def _source_digest(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        content = Path(path).read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _parse_stage_commits(lines: Sequence[bytes]) -> list[dict]:
    commits = []
    previous = "0" * 64
    for line in lines:
        event = json.loads(line)
        event_hash = event.pop("event_hash")
        if event.get("previous_hash") != previous or hashlib.sha256(
            json.dumps(event, sort_keys=True).encode()
        ).hexdigest() != event_hash:
            raise ValueError("prerequisite integrity: stage journal hash chain mismatch")
        previous = event_hash
        event["event_hash"] = event_hash
        commits.append(event)
    return commits


def _stage_commits(directory: Path) -> list[dict]:
    path = directory / "stages.jsonl"
    if not path.exists():
        return []
    with path.open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        return _read_locked_stage_commits(stream, directory)


def _read_locked_stage_commits(stream, directory: Path) -> list[dict]:
    stream.seek(0)
    content = stream.read()
    if not content:
        return []
    if content.endswith(b"\n"):
        return _parse_stage_commits(content.splitlines())
    prefix, separator, tail = content.rpartition(b"\n")
    committed_prefix = prefix + separator
    prior = _parse_stage_commits(committed_prefix.splitlines())
    try:
        json.loads(tail)
    except (ValueError, UnicodeDecodeError):
        recovery = directory / "recovery-artifacts"
        recovery.mkdir(exist_ok=True)
        saved = Path(tempfile.mkdtemp(prefix="journal-", dir=recovery)) / "partial.jsonl"
        with saved.open("xb") as output:
            output.write(tail)
            output.flush()
            os.fsync(output.fileno())
        stream.seek(len(committed_prefix))
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
        return prior
    commits = _parse_stage_commits(content.splitlines())
    stream.seek(0, os.SEEK_END)
    stream.write(b"\n")
    stream.flush()
    os.fsync(stream.fileno())
    return commits


def _result_bytes(result: dict) -> bytes:
    return (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()


def _restore_committed_result(directory: Path, event: dict, protocol_hash: str) -> None:
    result = event.get("result")
    if not isinstance(result, dict):
        raise ValueError("prerequisite integrity: committed result missing")
    content = _result_bytes(result)
    if (
        event.get("result_sha256") != hashlib.sha256(content).hexdigest()
        or event.get("protocol_sha256") != protocol_hash
        or result.get("stage") != event.get("stage")
        or result.get("protocol_sha256") != protocol_hash
        or result.get("source_sha256") != event.get("source_sha256")
    ):
        raise ValueError("prerequisite integrity: committed result identity changed")
    path = directory / f"{event['stage']}.json"
    with (directory / ".recovery.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists() and path.read_bytes() != content:
            valid_json = True
            try:
                observed = json.loads(path.read_bytes())
            except (ValueError, UnicodeDecodeError):
                valid_json = False
            if valid_json and observed != result:
                raise ValueError("prerequisite integrity: result or identity changed")
            recovery = directory / "recovery-artifacts"
            recovery.mkdir(exist_ok=True)
            saved = Path(tempfile.mkdtemp(prefix=f"{event['stage']}-", dir=recovery)) / "partial.json"
            os.replace(path, saved)
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        if path.read_bytes() != content:
            raise ValueError("prerequisite integrity: result or identity changed")


def _verify_prerequisites(directory: Path, stage: str, manifest_bytes: bytes) -> None:
    expected = list(WINDOWS)[:list(WINDOWS).index(stage)]
    commits = _stage_commits(directory)
    if [event.get("stage") for event in commits] != expected:
        raise ValueError("prerequisite integrity: stage commits missing or out of order")
    protocol_hash = hashlib.sha256(manifest_bytes).hexdigest()
    for name, event in zip(expected, commits, strict=True):
        if event.get("stage") != name:
            raise ValueError("prerequisite integrity: stage identity changed")
        _restore_committed_result(directory, event, protocol_hash)


def _commit_stage(directory: Path, stage: str, result: dict, manifest_bytes: bytes) -> None:
    with (directory / "stages.jsonl").open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        commits = _read_locked_stage_commits(stream, directory)
        expected_prior = list(WINDOWS)[:list(WINDOWS).index(stage)]
        if [event.get("stage") for event in commits] != expected_prior:
            raise ValueError("prerequisite integrity: stage commit order changed")
        event = {
            "stage": stage,
            "protocol_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "source_sha256": result["source_sha256"],
            "result_sha256": hashlib.sha256(_result_bytes(result)).hexdigest(),
            "result": result,
            "previous_hash": commits[-1]["event_hash"] if commits else "0" * 64,
        }
        event["event_hash"] = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
        stream.seek(0, os.SEEK_END)
        stream.write((json.dumps(event, sort_keys=True) + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())


def run_stage(stage: str, captures: Sequence[Path], directory: Path) -> dict:
    """Retain every post-capture failure with its attempted source identity."""
    state: dict[str, str] = {}
    try:
        return _run_stage(stage, captures, directory, state)
    except Exception:
        if state.get("phase") == "evaluating":
            _record_rejection(
                Path(directory).expanduser().resolve(), stage, "stage_data_invalid", state.get("source_hash")
            )
        raise


def _run_stage(stage: str, captures: Sequence[Path], directory: Path, state: dict[str, str]) -> dict:
    """Run once per fixed stage. Never overwrite or promote an exploratory result."""
    if stage not in WINDOWS or not captures:
        raise ValueError("registered stage and captures required")
    directory = Path(directory).expanduser().resolve()
    if {"ProspectiveStudies", "live-paper-study"} & set(directory.parts):
        raise ValueError("protected prospective study path")
    manifest_bytes = (json.dumps(PROTOCOL, sort_keys=True, indent=2) + "\n").encode()
    manifest = directory / "protocol.json"
    if stage != "development" and not manifest.exists():
        _record_rejection(directory, stage, "missing_protocol")
        raise ValueError("research protocol missing before later stage")
    if manifest.exists() and manifest.read_bytes() != manifest_bytes:
        _record_rejection(directory, stage, "protocol_mismatch")
        raise ValueError("research protocol changed")
    target = directory / f"{stage}.json"
    commits = _stage_commits(directory)
    committed = next((event for event in commits if event.get("stage") == stage), None)
    if committed is not None:
        _restore_committed_result(directory, committed, hashlib.sha256(manifest_bytes).hexdigest())
    if target.exists():
        _record_rejection(directory, stage, "stage_already_recorded")
        raise FileExistsError("research stage already recorded")
    try:
        _verify_prerequisites(directory, stage, manifest_bytes)
    except (ValueError, KeyError, json.JSONDecodeError):
        _record_rejection(directory, stage, "prerequisite_integrity")
        raise
    if stage == "sealed":
        for prerequisite in ("development", "validation"):
            prior_result = json.loads((directory / f"{prerequisite}.json").read_text(encoding="utf-8"))
            if (
                prior_result.get("quality_status") != "coverage_sufficient"
                or Decimal(prior_result.get("total_net_points", "0")) <= 0
                or Decimal(prior_result.get("stressed_net_points", "0")) <= 0
            ):
                _record_rejection(directory, stage, "failed_selection_gate")
                raise ValueError("sealed data cannot be inspected after failed coverage or net return gates")
    # The sealed capture is not even opened until the earlier-stage gates pass.
    try:
        source_digest = _source_digest(captures)
        bars, source_hash = _read_captures(captures, stage)
        if source_hash != source_digest:
            raise ValueError("capture changed during stage read")
    except (OSError, ValueError, json.JSONDecodeError):
        _record_rejection(directory, stage, "capture_invalid", locals().get("source_digest"))
        raise
    state.update(phase="evaluating", source_hash=source_hash)
    start, end = WINDOWS[stage]
    grouped: dict[date, list[ConfirmedBar]] = defaultdict(list)
    # Group by exchange-local session date; UTC offsets change with DST.
    for bar in bars:
        local_start = bar.start.astimezone(NY)
        local_end = bar.end.astimezone(NY)
        if (
            local_start.date() == local_end.date()
            and time(9, 30) <= local_start.time()
            and local_end.time() <= time(16)
        ):
            grouped[local_start.date()].append(bar)
    if any(day < start - timedelta(days=7) or day >= end for day in grouped):
        raise ValueError("capture contains dates outside registered stage window")
    days = [start + timedelta(days=offset) for offset in range((end - start).days)]
    days = [day for day in days if is_full_us_cash_session(day)]
    outcomes = []
    incomplete = 0
    missing = 0
    eligible = 0
    for day in days:
        prior_candidates = [day - timedelta(days=offset) for offset in range(1, 8)
                            if is_full_us_cash_session(day - timedelta(days=offset))]
        prior = grouped.get(max(prior_candidates), []) if prior_candidates else []
        current = grouped.get(day, [])
        if not current:
            missing += 1
        if not (_full_session(prior) and _full_session(current)):
            incomplete += 1
            continue
        eligible += 1
        base = evaluate_last_half_hour(prior, current, slippage_points=Decimal("0.5"))
        stress = evaluate_last_half_hour(prior, current, slippage_points=Decimal("1.0"))
        outcomes.append({
            "day": day.isoformat(), "direction": base.direction, "reason": base.reason,
            "baseline_net_points": str(base.net_points), "stressed_net_points": str(stress.net_points),
            "always_long_net_points": str(_always_direction(current, "long")),
            "always_short_net_points": str(_always_direction(current, "short")),
            "price_scope": base.price_scope,
        })
    pnl = [Decimal(row["baseline_net_points"]) for row in outcomes]
    stress_pnl = [Decimal(row["stressed_net_points"]) for row in outcomes]
    lower, upper = _daily_interval(pnl)
    coverage = Decimal(eligible) / Decimal(len(days))
    result = {
        "stage": stage, "protocol_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "source_sha256": source_hash, "price_scope": "historical_base_exploratory",
        "account_fill_claim": False, "eligible_days": eligible, "incomplete_days": incomplete,
        "missing_days": missing, "expected_full_sessions": len(days),
        "full_session_coverage": str(coverage),
        "quality_status": "coverage_sufficient" if coverage >= Decimal("0.995") else "insufficient_coverage",
        "trade_count": sum(row["direction"] is not None for row in outcomes),
        "long_count": sum(row["direction"] == "long" for row in outcomes),
        "short_count": sum(row["direction"] == "short" for row in outcomes),
        "long_net_points": str(sum((Decimal(row["baseline_net_points"]) for row in outcomes
                                    if row["direction"] == "long"), Decimal(0))),
        "short_net_points": str(sum((Decimal(row["baseline_net_points"]) for row in outcomes
                                     if row["direction"] == "short"), Decimal(0))),
        "baseline_points": "0", "total_net_points": str(sum(pnl, Decimal(0))),
        "stressed_net_points": str(sum(stress_pnl, Decimal(0))),
        "always_long_net_points": str(sum((Decimal(row["always_long_net_points"]) for row in outcomes), Decimal(0))),
        "always_short_net_points": str(sum((Decimal(row["always_short_net_points"]) for row in outcomes), Decimal(0))),
        "maximum_drawdown_points": str(_maximum_drawdown(pnl)),
        "daily_mean_95pct_lower_bound_points": lower,
        "daily_mean_95pct_upper_bound_points": upper,
        "days": outcomes,
        "limitations": (
            "Historical base-price candles, not account-specific fills; "
            "GBP conversion and financing unavailable."
        ),
    }
    directory.mkdir(parents=True, exist_ok=True)
    if not manifest.exists() and any((directory / f"{name}.json").exists() for name in WINDOWS):
        raise ValueError("research stage exists without its protocol")
    if not manifest.exists():
        with manifest.open("xb") as stream:
            stream.write(manifest_bytes)
            stream.flush()
            os.fsync(stream.fileno())
    _commit_stage(directory, stage, result, manifest_bytes)
    with target.open("xb") as stream:
        stream.write(_result_bytes(result))
        stream.flush()
        os.fsync(stream.fileno())
    state["phase"] = "complete"
    return result
