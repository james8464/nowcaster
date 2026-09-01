"""Bounded public-market validation; isolated database, no credentials or orders.

Run from the project root: python -m scripts.validate_live_monitor --seconds 900
An optional --engine path tests the exact packaged native-app helper instead.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import selectors
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.live_monitor.types import MarketBar, MarketQuote, MonitorWireEvent, ProviderHealthEvent

_VALIDATION_ENVIRONMENT_ALLOWLIST = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TMPDIR",
)
_EXPECTED_PROVIDER = "binance"
_EXPECTED_FEED = "spot"
_SAFE_OBSERVATION_TYPES = frozenset({"ready", "heartbeat", "provider_health", "quote", "bar_finalized", "decision"})


def _validation_environment() -> dict[str, str]:
    environment = {name: os.environ[name] for name in _VALIDATION_ENVIRONMENT_ALLOWLIST if name in os.environ}
    environment.setdefault("PATH", os.defpath)
    environment["NOWCASTER_DISABLE_DOTENV"] = "1"
    environment["PYTHONUTF8"] = "1"
    return environment


def _instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("observation timestamps must be UTC")
    return result


def _distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {
        "count": len(values),
        "min": round(ordered[0], 3),
        "p50": round(statistics.median(ordered), 3),
        "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
        "p99": round(ordered[math.ceil(len(ordered) * 0.99) - 1], 3),
        "max": round(ordered[-1], 3),
    }


class LiveObservation:
    """Observed wire delivery, not exchange latency or a strategy backtest."""

    def __init__(self, symbols: tuple[str, ...]):
        self.symbols = symbols
        self.counts = Counter()
        self.reasons = Counter()
        self.health = Counter()
        self.issues: set[str] = set()
        self.ready: dict | None = None
        self.ready_observed_at: datetime | None = None
        self.sequence = -1
        self.latencies = {symbol: defaultdict(list) for symbol in symbols}
        self.bars = {symbol: set() for symbol in symbols}
        self.decisions = {symbol: set() for symbol in symbols}
        self.latest_quote_provider_at: dict[str, datetime] = {}

    def accept(self, event: dict, *, observed_at: datetime) -> None:
        wire = MonitorWireEvent.model_validate(event)
        kind, payload, sequence = wire.event_type, wire.payload, wire.sequence
        if sequence != self.sequence + 1:
            self.issues.add("wire_sequence_gap")
        self.sequence = sequence
        self.counts[kind] += 1
        if kind not in _SAFE_OBSERVATION_TYPES:
            self.issues.add(f"unexpected_event_type:{kind}")
        if kind == "ready":
            self.ready = payload
            self.ready_observed_at = observed_at
            if (
                payload.get("status") != "live"
                or payload.get("qualified_cohorts") != 0
                or payload.get("cohort_hash") != "0" * 64
                or payload.get("readiness_receipt_id") is not None
            ):
                self.issues.add("unexpected_qualified_cohort")
        if kind in {"heartbeat", "provider_health"}:
            health = ProviderHealthEvent.model_validate(payload)
            if (health.provider, health.feed) != (_EXPECTED_PROVIDER, _EXPECTED_FEED):
                self.issues.add("unexpected_market_identity")
            self.health[f"{health.status.value}:{health.reason}"] += 1
            if health.status.value in {"stale", "failed", "reconnecting"}:
                self.issues.add("provider_interruptions")
        if kind == "fatal_error":
            self.issues.add("engine_fatal_error")
        if kind == "notification_request":
            self.issues.add("unexpected_notification")
        if kind not in _SAFE_OBSERVATION_TYPES:
            return
        if kind not in {"quote", "bar_finalized", "decision"}:
            return
        if kind == "quote":
            quote = MarketQuote.model_validate(payload)
            symbol = quote.symbol
            provider, feed = quote.provider, quote.feed
        elif kind == "bar_finalized":
            bar = MarketBar.model_validate(payload)
            symbol = bar.symbol
            provider, feed = bar.provider, bar.feed
        else:
            symbol = str(payload["symbol"])
            provider, feed = str(payload["provider"]), str(payload["feed"])
            _instant(str(payload["decision_time"]))
        if (provider, feed) != (_EXPECTED_PROVIDER, _EXPECTED_FEED):
            self.issues.add("unexpected_market_identity")
        if symbol not in self.symbols:
            self.issues.add("unexpected_symbol")
            return
        if kind == "quote":
            provider_at, received = quote.provider_time, quote.received_at
            self.latest_quote_provider_at[symbol] = provider_at
            processed = _instant(payload.get("processed_at") or payload["received_at"])
            for name, difference in {
                "provider_to_receive_ms": received - provider_at,
                "receive_to_processing_ms": processed - received,
                "provider_to_observer_ms": observed_at - provider_at,
                "processing_to_observer_ms": observed_at - processed,
            }.items():
                self.latencies[symbol][name].append(difference.total_seconds() * 1000)
            if processed < provider_at or processed < received or observed_at < processed:
                self.issues.add(f"processing_clock_invalid:{symbol}")
        elif kind == "bar_finalized":
            self.bars[symbol].add(bar.start)
        else:
            self.decisions[symbol].add(payload["decision_time"])
            self.reasons.update(payload.get("reasons", []))
            if payload["status"] != "abstain":
                self.issues.add("unexpected_actionable_decision")

    def report(self, *, exit_code: int | None, live_seconds: float, stderr_present: bool) -> dict:
        issues = set(self.issues)
        if self.ready is None:
            issues.add("engine_not_ready")
        if exit_code != 0:
            issues.add("engine_exit_failure")
        if stderr_present:
            issues.add("engine_stderr")
        if self.health.get("healthy:subscribed", 0) < 1:
            issues.add("missing_healthy_subscription")
        minimum_heartbeats = max(0, math.floor(live_seconds / 10) - 2)
        if self.health.get("healthy:heartbeat", 0) < minimum_heartbeats:
            issues.add("insufficient_healthy_heartbeats")
        assets = {}
        expected_end = (
            self.ready_observed_at + timedelta(seconds=live_seconds) if self.ready_observed_at is not None else None
        )
        for symbol in self.symbols:
            latencies = self.latencies[symbol]
            observed = latencies.get("provider_to_observer_ms", [])
            if not observed:
                issues.add(f"missing_quotes:{symbol}")
            elif max(observed) > 30_000 or (
                expected_end is not None
                and expected_end - self.latest_quote_provider_at[symbol] > timedelta(seconds=30)
            ):
                issues.add(f"stale_quotes:{symbol}")
            bars = sorted(self.bars[symbol])
            gaps = sum(
                max(0, int((end - start).total_seconds() / 60) - 1) for start, end in zip(bars, bars[1:], strict=False)
            )
            if gaps:
                issues.add(f"finalized_bar_gaps:{symbol}")
            if len(bars) < max(0, math.floor(live_seconds / 60) - 2):
                issues.add(f"insufficient_finalized_bars:{symbol}")
            if len(self.decisions[symbol]) < max(0, math.floor(live_seconds / 300) - 1):
                issues.add(f"insufficient_decision_windows:{symbol}")
            assets[symbol] = {
                **{name: _distribution(values) for name, values in latencies.items()},
                "finalized_minute_bars": len(bars),
                "first_bar_start": bars[0].isoformat() if bars else None,
                "last_bar_start": bars[-1].isoformat() if bars else None,
                "bar_gap_count": gaps,
                "decision_windows": len(self.decisions[symbol]),
            }
        return {
            "scope": "live_feed_and_abstention" if live_seconds >= 600 else "connectivity_only",
            "live_seconds": round(live_seconds, 3),
            "exit_code": exit_code,
            "ready": self.ready,
            "event_counts": dict(self.counts),
            "provider_health": dict(self.health),
            "decision_reasons": dict(self.reasons),
            "assets": assets,
            "issues": sorted(issues),
            "profitability": "not_assessed_no_qualified_entries",
            "note": "Clock offsets are retained; a negative provider-to-receive value is not negative network latency. "
            "No credentials, order calls, simulated fills, model promotion or profitability inference.",
        }


def _engine_kind(root: Path, engine: Path | None) -> str:
    if engine is None:
        return "source"
    resolved = engine.resolve()
    contents = resolved.parent.parent
    app = contents.parent
    if not (
        resolved.name == "nowcaster-engine"
        and resolved.parent.name == "Helpers"
        and contents.name == "Contents"
        and app.name == "Nowcaster.app"
    ):
        return "external"
    from scripts.engine_manifest import verify_manifest

    manifest_path = contents / "Resources" / "engine-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("packaged engine manifest is unavailable") from error
    if not verify_manifest(root, resolved, manifest):
        raise ValueError("packaged engine manifest verification failed")
    if sys.platform == "darwin":
        signature = subprocess.run(
            ["codesign", "--verify", "--strict", str(resolved)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if signature.returncode != 0:
            raise ValueError("packaged engine signature verification failed")
    return "packaged"


def run_probe(*, root: Path, output: Path, seconds: int, engine: Path | None = None) -> dict:
    from scripts.engine_manifest import file_hash, source_hashes
    from src.config.settings import Settings
    from src.strategies.types import canonical_hash

    if not 1 <= seconds <= 3600:
        raise ValueError("live validation must last between 1 and 3600 seconds")
    engine_kind = _engine_kind(root, engine)
    output.mkdir(parents=True, exist_ok=False)
    symbols = ("BTCUSDT", "ETHUSDT")
    observation = LiveObservation(symbols)
    started_at = datetime.now(UTC)
    started = time.monotonic()
    ready_at = None
    last_progress = started
    error_bytes = 0
    command = (
        [str(engine), "monitor", "run"]
        if engine
        else [sys.executable, "-m", "scripts.live_engine_entry", "monitor", "run"]
    )
    bootstrap = {
        "schema_version": 1,
        "session_id": output.name,
        "database_url": f"duckdb:///{output / 'monitor.duckdb'}",
        "stock_feed": "iex",
        "stocks": [],
        "crypto": symbols,
        "decision_interval": "5m",
        "config_hash": canonical_hash(Settings.load(root, load_environment_file=False).config_hash_payload()),
        "cohort_hash": "0" * 64,
    }
    process = subprocess.Popen(
        command,
        cwd=root,
        env=_validation_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "out")
    selector.register(process.stderr, selectors.EVENT_READ, "err")
    buffer = b""

    with (output / "observations.jsonl").open("x", encoding="utf-8") as ledger:

        def pump(timeout: float) -> None:
            nonlocal buffer, error_bytes, ready_at
            for key, _ in selector.select(timeout):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "err":
                    error_bytes += len(data)  # Do not copy internal errors or private context to the report.
                    continue
                buffer += data
                if len(buffer) > 1024 * 1024:
                    raise ValueError("live engine exceeded the bounded wire buffer")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    observed_at = datetime.now(UTC)
                    event = json.loads(line)
                    observation.accept(event, observed_at=observed_at)
                    ledger.write(json.dumps({"observed_at": observed_at.isoformat(), "event": event}) + "\n")
                    if event["event_type"] == "ready" and ready_at is None:
                        ready_at = time.monotonic()
                        print(
                            json.dumps({"phase": "ready", "startup_seconds": round(ready_at - started, 3)}), flush=True
                        )

        try:
            process.stdin.write((json.dumps(bootstrap) + "\n").encode())
            process.stdin.flush()
            while process.poll() is None:
                now = time.monotonic()
                if ready_at is not None and now - ready_at >= seconds:
                    break
                if ready_at is None and now - started >= 180:
                    observation.issues.add("startup_timeout")
                    break
                pump(0.25)
                if now - last_progress >= 60:
                    print(
                        json.dumps(
                            {
                                "phase": "observing",
                                "elapsed_seconds": round(now - started),
                                "event_counts": dict(observation.counts),
                            }
                        ),
                        flush=True,
                    )
                    ledger.flush()
                    last_progress = now
        except (ValueError, KeyError, TypeError, OSError):
            observation.issues.add("invalid_wire_or_io_failure")
        finally:
            stopping = time.monotonic()
            if process.poll() is None:
                try:
                    process.stdin.write(b'{"schema_version":1,"command":"shutdown"}\n')
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                process.stdin.close()
                while process.poll() is None and time.monotonic() - stopping < 15:
                    pump(0.1)
                if process.poll() is None:
                    observation.issues.add("forced_shutdown")
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
            while selector.get_map() and time.monotonic() - stopping < 22:
                pump(0.1)
            selector.close()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
    live_seconds = max(0, stopping - ready_at) if ready_at else 0
    if ready_at is not None and live_seconds < seconds:
        observation.issues.add("incomplete_observation_window")
    report = observation.report(
        exit_code=process.returncode,
        live_seconds=live_seconds,
        stderr_present=error_bytes > 0,
    )
    report.update(
        {
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(UTC).isoformat(),
            "startup_seconds": round(ready_at - started, 3) if ready_at else None,
            "requested_live_seconds": seconds,
            "engine": engine_kind,
            "executable_sha256": file_hash(engine) if engine else None,
            "source_tree_sha256": canonical_hash(source_hashes(root)),
            "output": str(output),
            "safety": {
                "broker_credentials_supplied": False,
                "environment_allowlisted": True,
                "environment_file_loading": False,
                "isolated_database": True,
                "order_submission": False,
            },
        }
    )
    (output / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--engine", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    if not (root / "config").is_dir():
        parser.error("run live validation from the Nowcaster project root")
    if not 1 <= args.seconds <= 3600:
        parser.error("--seconds must be between 1 and 3600")
    if args.output:
        output = args.output.resolve()
    else:
        (root / "build").mkdir(exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="live-validation-", dir=root / "build")) / "run"
    report = run_probe(
        root=root, output=output, seconds=args.seconds, engine=args.engine.resolve() if args.engine else None
    )
    print(json.dumps(report, sort_keys=True), flush=True)
    return 1 if report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
