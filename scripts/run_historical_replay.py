"""Register and run the fixed, pinned retrospective account replay."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import multiprocessing
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from multiprocessing.connection import wait
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from src.ingestion.binance_archive import BinancePublicArchive  # noqa: E402
from src.research.historical_replay import HistoricalReplay  # noqa: E402
from src.research.historical_replay_reporting import period_changes, quality_profile, render_report  # noqa: E402
from src.research.holding_period_search import research_runtime_fingerprint  # noqa: E402
from src.research.prospective_runtime import load_registry, study_source_hash  # noqa: E402
from src.strategies.types import BarInterval, canonical_hash  # noqa: E402

DISCOVERY_PATH = Path("data/research/holding-period-search-2026-09-08.json")
DISCOVERY_SHA256 = "58c25ec6e836ecd7b17963a03ef6e9f3e43a03b78666c534506b874699a5f903"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
HYPOTHESIS = "fixed_selected_rule_account_replay_v1"
BASE_URL = "https://data.binance.vision/data/spot"
source_identity = study_source_hash


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _exclusive(path: Path, payload: bytes):
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _write_json(path: Path, value):
    _exclusive(path, (_json(value) + "\n").encode())


def _append(path: Path, value):
    with path.open("a") as stream:
        stream.write(_json(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def effective_workers(requested: int) -> int:
    if type(requested) is not int or requested not in (1, 2):
        raise ValueError("workers must be 1 or 2")
    return min(requested, max(1, (os.cpu_count() or 1) - 2))


def _window(discovery):
    start = datetime.fromisoformat(discovery["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(discovery["end_exclusive"].replace("Z", "+00:00"))
    if (
        any(
            value.tzinfo is None
            or value.utcoffset() != timedelta(0)
            or value.hour
            or value.minute
            or value.second
            or value.microsecond
            for value in (start, end)
        )
        or end <= start
    ):
        raise ValueError("manifest window must contain ordered UTC midnight boundaries")
    return start, end


def validate_manifest(discovery: dict) -> list[dict]:
    """Validate pins and the complete archive schedule before deriving local paths."""
    try:
        start, end = _window(discovery)
        if discovery["symbols"] != list(SYMBOLS) or discovery["interval"] != "1h":
            raise ValueError("manifest scope must be BTCUSDT/ETHUSDT hourly")
        rows = discovery["archive_manifests"]
        if not isinstance(rows, list) or canonical_hash(rows) != discovery["archive_manifest_hash"]:
            raise ValueError("manifest digest mismatch")
        expected = []
        for symbol in SYMBOLS:
            cursor = start.replace(day=1)
            while cursor < end:
                following = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
                left, right = max(start, cursor), min(end, following)
                if left == cursor and right == following:
                    expected.append((symbol, "monthly", f"{symbol}-1h-{cursor:%Y-%m}.zip"))
                else:
                    while left < right:
                        expected.append((symbol, "daily", f"{symbol}-1h-{left:%Y-%m-%d}.zip"))
                        left += timedelta(days=1)
                cursor = following
        if len(rows) != len(expected):
            raise ValueError("manifest archive schedule mismatch")
        keys = {
            "symbol",
            "frequency",
            "name",
            "sha256",
            "compressed_bytes",
            "selected_rows",
            "invalid_boundary_rows",
            "checksum_verified",
        }
        for row, identity in zip(rows, expected, strict=True):
            if set(row) != keys or (row["symbol"], row["frequency"], row["name"]) != identity:
                raise ValueError("manifest fields or archive schedule mismatch")
            if not isinstance(row["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None:
                raise ValueError("manifest archive digest is malformed")
            if (
                any(
                    type(row[key]) is not int or row[key] < (1 if key == "compressed_bytes" else 0)
                    for key in ("compressed_bytes", "selected_rows", "invalid_boundary_rows")
                )
                or row["checksum_verified"] is not True
            ):
                raise ValueError("manifest counts or checksum flag are malformed")
            if row["compressed_bytes"] > 16 * 1024 * 1024 or row["selected_rows"] + row["invalid_boundary_rows"] > (
                24 if row["frequency"] == "daily" else 31 * 24
            ):
                raise ValueError("manifest archive exceeds bounded hourly scope")
        return rows
    except (KeyError, TypeError, AttributeError, OverflowError) as error:
        raise ValueError("malformed archive manifest") from error


def validate_candidates(discovery, registry):
    if [candidate.get("symbol") for candidate in discovery["candidates"]] != list(SYMBOLS):
        raise ValueError("candidate scope mismatch")
    for candidate in discovery["candidates"]:
        HistoricalReplay(candidate, registry)


def load_pinned_discovery(root: Path) -> dict:
    payload = (root / DISCOVERY_PATH).read_bytes()
    if hashlib.sha256(payload).hexdigest() != DISCOVERY_SHA256:
        raise ValueError("original discovery digest mismatch")
    discovery = json.loads(payload)
    rows = validate_manifest(discovery)
    if (
        _window(discovery) != (datetime(2017, 8, 17, tzinfo=UTC), datetime(2026, 9, 8, tzinfo=UTC))
        or len(rows) != 260
        or len(discovery["trials"]) != 24
        or any(sum(item["selected_rows"] for item in rows if item["symbol"] == symbol) != 79270 for symbol in SYMBOLS)
    ):
        raise ValueError("original discovery scope mismatch")
    validate_candidates(discovery, load_registry(root))
    return discovery


def _archive_paths(cache, row):
    path = cache / "binance-public-data/spot" / row["frequency"] / "klines" / row["symbol"] / "1h" / row["name"]
    checksum = path.with_name(path.name + ".CHECKSUM")
    for target in (path, checksum):
        if not target.resolve().is_relative_to(cache) or any(
            parent.is_symlink() for parent in (target, *target.parents) if parent != cache.parent
        ):
            raise ValueError("protected archive path or symlink")
    return path, checksum


def _verify_zip(payload, row):
    if len(payload) != row["compressed_bytes"] or hashlib.sha256(payload).hexdigest() != row["sha256"]:
        raise ValueError(f"pinned archive changed: {row['name']}")


def _verify_checksum(payload, row):
    try:
        text = payload.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ValueError(f"invalid pinned checksum: {row['name']}") from error
    if re.fullmatch(rf"{row['sha256']}[ \t]+\*?{re.escape(row['name'])}", text) is None:
        raise ValueError(f"pinned checksum digest or filename mismatch: {row['name']}")


def _download(client, url, limit):
    with client.stream("GET", url) as response:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > limit:
                raise ValueError("pinned archive download exceeds expected byte bound")
        return bytes(content)


def preflight_archives(discovery, cache_dir: Path, *, allow_download=False):
    rows = validate_manifest(discovery)
    cache = cache_dir.expanduser().resolve()
    missing = []
    # Inspect every existing pin before allowing a single restoration request.
    for row in rows:
        paths = _archive_paths(cache, row)
        for path, check, limit in (
            (paths[0], _verify_zip, row["compressed_bytes"]),
            (paths[1], _verify_checksum, 4096),
        ):
            if path.exists():
                if path.stat().st_size > limit:
                    raise ValueError(f"pinned archive/checksum exceeds size: {path.name}")
                check(path.read_bytes(), row)
            else:
                missing.append((row, path, check, limit))
    if missing and not allow_download:
        raise FileNotFoundError(
            f"offline archive restoration required: {len(missing)} missing pinned files; first {missing[0][1].name}"
        )
    if missing:

        def restore(item):
            row, path, check, limit = item
            url = f"{BASE_URL}/{row['frequency']}/klines/{row['symbol']}/1h/{path.name}"
            with httpx.Client(timeout=60, follow_redirects=False) as client:
                payload = _download(client, url, limit)
            check(payload, row)
            path.parent.mkdir(parents=True, exist_ok=True)
            _exclusive(path, payload)

        with ThreadPoolExecutor(max_workers=min(4, len(missing))) as pool:
            list(pool.map(restore, missing))


def _no_network(request):
    raise RuntimeError(f"network forbidden after archive preflight: {request.url}")


def load_scopes(discovery: dict, cache_dir: Path, *, allow_download: bool = False) -> dict:
    """Lower-level exact loader; accepts validated small manifests for offline tests."""
    preflight_archives(discovery, cache_dir, allow_download=allow_download)
    start, end = _window(discovery)
    scopes = {}
    with httpx.Client(transport=httpx.MockTransport(_no_network)) as client:
        archive = BinancePublicArchive(client, cache_dir=cache_dir)
        for symbol in SYMBOLS:
            result = archive.fetch(symbol=symbol, interval=BarInterval.ONE_HOUR, start=start, end=end)
            expected = [row for row in discovery["archive_manifests"] if row["symbol"] == symbol]
            actual = [{"symbol": symbol, **row} for row in result.manifest]
            if result.unavailable or actual != expected:
                raise ValueError(f"loaded archive manifest mismatch: {symbol}")
            bars = result.bars
            if (
                bars.empty
                or len(bars) != sum(row["selected_rows"] for row in expected)
                or bars.open_timestamp.duplicated().any()
            ):
                raise ValueError(f"duplicate or mismatched archive rows: {symbol}")
            # The archive parser drops duplicate identical rows. The retained row
            # sum above detects that drop; the engine validates every remaining row.
            scopes[symbol] = bars
    return scopes


def _validate_paths(root, cache, output):
    if root != ROOT:
        raise ValueError("root must match the executing replay source checkout")
    for target in (cache, output):
        if any(part in ("ProspectiveStudies", "live-paper-study") for part in target.parts):
            raise ValueError("protected prospective/source path")
        if target.is_relative_to(root) or root.is_relative_to(target):
            raise ValueError("cache/output must be separate from source")
        # Worktree checkouts must not write into the surrounding source repository.
        if ".worktrees" in root.parts:
            primary = Path(*root.parts[: root.parts.index(".worktrees")])
            if target.is_relative_to(primary):
                raise ValueError("cache/output must be separate from source repository")
    if output.is_relative_to(cache) or cache.is_relative_to(output) or cache.is_relative_to(output.parent):
        raise ValueError("output and cache must be separate")
    if output.parent.name != "HistoricalReplays":
        raise ValueError("output must be a new attempt under a separate HistoricalReplays parent")
    if output.exists():
        raise FileExistsError("replay output already exists; retained attempts cannot be overwritten")


def _records(parent):
    path = parent / "campaigns.jsonl"
    try:
        if path.is_symlink():
            raise ValueError("registry symlink is forbidden")
        records = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        previous = None
        directories = set()
        for number, record in enumerate(records, 1):
            if (
                record["attempt_number"] != number
                or record["previous_hash"] != previous
                or record["hypothesis"] != HYPOTHESIS
                or record["directory"] in directories
                or canonical_hash({key: value for key, value in record.items() if key != "hash"}) != record["hash"]
            ):
                raise ValueError("registry integrity failure")
            directory = Path(record["directory"])
            if directory.parent != parent or not directory.is_dir():
                raise ValueError("registry attempt directory missing or moved")
            protocol_path = directory / "protocol.json"
            if not protocol_path.exists():
                status_path = directory / "status.json"
                status = json.loads(status_path.read_text()) if status_path.exists() else {}
                if status.get("status") != "failed":
                    raise ValueError("registry attempt protocol is missing")
            if (
                protocol_path.exists()
                and canonical_hash(json.loads(protocol_path.read_text())) != record["protocol_hash"]
            ):
                raise ValueError("registry protocol identity mismatch")
            previous = record["hash"]
            directories.add(record["directory"])
        if any(str(path.parent) not in directories for path in parent.glob("*/protocol.json")):
            raise ValueError("registry has unregistered retained protocol")
        return records
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("registry integrity failure") from error


def _status(output, status, **fields):
    record = dict(status=status, at=datetime.now(UTC).isoformat(), **fields)
    _append(output / "status.jsonl", record)
    if status in ("failed", "succeeded"):
        _write_json(output / "status.json", record)


def _register(root, output, discovery, requested, effective, cache):
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    if (parent / "campaigns.lock").is_symlink():
        raise ValueError("registry lock symlink is forbidden")
    with (parent / "campaigns.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = _records(parent)
        protocol = dict(
            schema_version=1,
            hypothesis=HYPOTHESIS,
            attempt_number=len(records) + 1,
            registered_at=datetime.now(UTC).isoformat(),
            source_identity=source_identity(root),
            source_root=str(root),
            runtime=research_runtime_fingerprint(),
            discovery_sha256=DISCOVERY_SHA256,
            prior_selection=dict(
                trial_count=len(discovery["trials"]),
                trials=discovery["trials"],
                discovery_source_hash=discovery["source_hash"],
                independent_validation=False,
            ),
            candidates=discovery["candidates"],
            archive_manifests=discovery["archive_manifests"],
            archive_manifest_hash=discovery["archive_manifest_hash"],
            start=discovery["start"],
            end_exclusive=discovery["end_exclusive"],
            workers_requested=requested,
            workers_effective=effective,
            cache_dir=str(cache),
            retrospective=True,
            promotable=False,
            independent_validation=False,
            assumptions=dict(
                initial_cash_per_account="10000",
                independent_accounts=True,
                risk_budget="25",
                cash_fraction="0.25",
                lot_increments=dict(BTCUSDT="0.00001", ETHUSDT="0.0001"),
                minimum_notional="5",
                minimum_target_bps=68,
                fee_bps=10,
                half_spread_bps=2,
                slippage_bps=5,
                cost_multipliers=[1, 2],
                calculation_window_observed_bars=1000,
                entry="next_continuous_bar_open_full_fill",
                precedence="adverse_stop_then_expiry_then_target",
                ambiguous="stop_first",
                gap="cancel_pending_and_first_observed_open_liquidation",
                availability="archive_close_not_point_in_time_vintage",
                period="interval_ending_carry_equity",
                drawdown="observed_hourly_close",
                terminal_liquidation=False,
                historical_exchange_rules=False,
                quote_queue_latency_partial_fill_fidelity=False,
                borrowing_funding_taxes_volume_capacity=False,
                prospective_flat_extra_34bps_equivalent=False,
            ),
        )
        output.mkdir(exist_ok=False)
        try:
            record = dict(
                attempt_number=protocol["attempt_number"],
                hypothesis=HYPOTHESIS,
                directory=str(output),
                protocol_hash=canonical_hash(protocol),
                previous_hash=records[-1]["hash"] if records else None,
            )
            record["hash"] = canonical_hash(record)
            _append(parent / "campaigns.jsonl", record)
            _write_json(output / "protocol.json", protocol)
            original_discovery = (root / DISCOVERY_PATH).read_bytes()
            if hashlib.sha256(original_discovery).hexdigest() != DISCOVERY_SHA256:
                raise ValueError("original discovery digest changed during registration")
            _exclusive(output / "discovery.json", original_discovery)
            _status(output, "registered")
        except BaseException as error:
            _status(output, "failed", error_type=type(error).__name__, error=str(error))
            raise
    return protocol


def _run_asset(root, output, candidate, bars, start, end, invalid_boundary_rows):
    directory = output / candidate["symbol"]
    directory.mkdir(exist_ok=False)
    with (directory / "events.jsonl").open("x", buffering=1) as events:

        def emit(event):
            events.write(_json(event) + "\n")
            events.flush()

        replay = HistoricalReplay(candidate, load_registry(root), on_event=emit)
        try:
            for index, values in enumerate(bars.itertuples(index=False, name=None), 1):
                replay.push_bar(dict(zip(bars.columns, values, strict=True)))
                if index % 1000 == 0:
                    print(_json(dict(symbol=candidate["symbol"], bars=index, total=len(bars))), flush=True)
            result = replay.result()
            result["quality"] = quality_profile(bars, start, end, invalid_boundary_rows=invalid_boundary_rows)
            result["worker_pid"] = os.getpid()
            for scenario in result["scenarios"]:
                scenario["periods"] = period_changes(scenario["equity_curve"], start, end)
            os.fsync(events.fileno())
            return result
        except BaseException as error:
            events.flush()
            os.fsync(events.fileno())
            _write_json(directory / "failure.json", dict(error_type=type(error).__name__, error=str(error)))
            raise


def _asset_process(connection, arguments, expected_source, expected_runtime):
    try:
        if source_identity(arguments[0]) != expected_source or research_runtime_fingerprint() != expected_runtime:
            raise RuntimeError("asset worker source/runtime identity mismatch")
        connection.send(dict(ok=True, result=_run_asset(*arguments)))
    except BaseException as error:
        connection.send(dict(ok=False, error_type=type(error).__name__, error=str(error)))
    finally:
        connection.close()


def _parallel_assets(arguments, protocol):
    """One complete chronology per process; failure stops every surviving worker."""
    context = multiprocessing.get_context("spawn")
    processes = []
    pending = {}
    results = [None] * len(arguments)
    try:
        for index, args in enumerate(arguments):
            receiver, sender = context.Pipe(duplex=False)
            process = context.Process(
                target=_asset_process, args=(sender, args, protocol["source_identity"], protocol["runtime"])
            )
            process.start()
            sender.close()
            processes.append(process)
            pending[receiver] = index
        while pending:
            for connection in wait(list(pending), timeout=1):
                index = pending.pop(connection)
                try:
                    message = connection.recv()
                except EOFError as error:
                    raise RuntimeError(f"asset worker exited without a result: {SYMBOLS[index]}") from error
                finally:
                    connection.close()
                if not message["ok"]:
                    raise RuntimeError(f"{SYMBOLS[index]} worker {message['error_type']}: {message['error']}")
                results[index] = message["result"]
            for connection, index in pending.items():
                if not processes[index].is_alive() and not connection.poll():
                    raise RuntimeError(f"asset worker stopped unexpectedly: {SYMBOLS[index]}")
        return results
    finally:
        # Every event line has been flushed by its owning process. Termination
        # never removes either worker's existing evidence or reuses its engine.
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        for connection in pending:
            connection.close()


def run_replay(
    root: Path, cache_dir: Path, output_dir: Path, *, allow_download: bool = False, workers: int = 1
) -> dict:
    effective = effective_workers(workers)
    root, cache, output = (path.expanduser().resolve() for path in (root, cache_dir, output_dir))
    _validate_paths(root, cache, output)
    discovery = load_pinned_discovery(root)
    protocol = _register(root, output, discovery, workers, effective, cache)
    try:
        _status(output, "preflight")
        scopes = load_scopes(discovery, cache, allow_download=allow_download)
        _status(output, "evaluating")
        arguments = [
            (
                root,
                output,
                candidate,
                scopes[candidate["symbol"]],
                discovery["start"],
                discovery["end_exclusive"],
                sum(
                    row["invalid_boundary_rows"]
                    for row in discovery["archive_manifests"]
                    if row["symbol"] == candidate["symbol"]
                ),
            )
            for candidate in discovery["candidates"]
        ]
        assets = [_run_asset(*args) for args in arguments] if effective == 1 else _parallel_assets(arguments, protocol)
        if (
            source_identity(root) != protocol["source_identity"]
            or research_runtime_fingerprint() != protocol["runtime"]
        ):
            raise RuntimeError("replay source/runtime changed during evaluation")
        result = dict(schema_version=1, status="succeeded", protocol=protocol, assets=assets)
        report = render_report(result)
        _write_json(output / "result.json", result)
        _exclusive(output / "report.md", report.encode())
        _status(output, "succeeded", result_hash=canonical_hash(result))
        return result
    except BaseException as error:
        _status(output, "failed", error_type=type(error).__name__, error=str(error))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--cache-dir", type=Path, default=Path("~/Library/Caches/NowcasterHistoricalReplay"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    args = parser.parse_args(argv)
    try:
        result = run_replay(
            args.root, args.cache_dir, args.output_dir, allow_download=args.allow_download, workers=args.workers
        )
        print(_json(dict(status=result["status"], output_dir=str(args.output_dir.expanduser().resolve()))), flush=True)
        return 0
    except KeyboardInterrupt:
        print("Replay interrupted; registered attempt and partial evidence retained.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as error:
        print(f"Replay refused or failed; any registered attempt is retained: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
