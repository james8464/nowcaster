"""Public-data collection for an immutable, isolated paper experiment."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pandas as pd
import yaml
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from src.config.settings import StrategiesConfig
from src.ingestion.bars import atomic_write_bytes
from src.live_monitor.bars import FinalizedBarLedger, aggregate_finalized
from src.live_monitor.providers import BinanceSpotAdapter, ProviderSymbolMetadata, _binance_spot_tradable
from src.live_monitor.types import MarketBar, MarketQuote, MonitorHealth, ProviderHealthEvent
from src.research.holding_period_search import eligible_long_signals
from src.research.opportunity_audit import gap_safe_atr
from src.strategies.library import build_strategy_registry
from src.strategies.types import canonical_hash
from src.utils.provenance import research_source_hash
from src.utils.tls import verified_client_context

DEFAULT_DIRECTORY = Path.home() / "Library/Application Support/Nowcaster/ProspectiveStudies"


def study_source_hash(root: Path) -> str:
    scripts = [
        (p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in sorted((root / "scripts").rglob("*.py"))
        if "__pycache__" not in p.parts
    ]
    return canonical_hash({"research": research_source_hash(root), "scripts": scripts})


def load_registry(root: Path):
    # Load strategy definitions only: no environment, broker account or credentials.
    config = StrategiesConfig.model_validate(yaml.safe_load((root / "config/strategies.yaml").read_text()))
    return build_strategy_registry(config.enabled)


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, default=str) + "\n").encode()


def register_study(
    discovery: Path, directory: Path, *, root: Path, now: datetime | None = None, starts_at: datetime | None = None
):
    from src.research.prospective import ProspectiveLedger
    from src.research.prospective_types import StudyCandidate, StudyManifest

    now = now or datetime.now(UTC)
    starts_at = starts_at or now + timedelta(minutes=2)
    if starts_at <= now:
        raise ValueError("registration requires a future start")
    if directory.exists():
        raise FileExistsError("study directory already exists; resume it instead")
    payload = discovery.read_bytes()
    candidates = tuple(
        StudyCandidate.model_validate({k: v for k, v in row.items() if k in StudyCandidate.model_fields})
        for row in json.loads(payload)["candidates"]
    )
    registry = load_registry(root)
    for candidate in candidates:
        if registry.resolve(candidate.strategy_id).spec.definition_hash != candidate.strategy_definition_hash:
            raise ValueError("discovery strategy definition changed")
    parent = directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    # One registry for the retained study collection, guarded independently of collectors.
    with (parent / "campaigns.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        registry_path = parent / "campaigns.jsonl"
        records = (
            [json.loads(line) for line in registry_path.read_text().splitlines()] if registry_path.exists() else []
        )
        previous = None
        for index, record in enumerate(records, 1):
            expected = canonical_hash({k: v for k, v in record.items() if k != "record_hash"})
            if (
                record["study_number"] != index
                or record["predecessor_study_id"] != previous
                or expected != record["record_hash"]
            ):
                raise ValueError("campaign registry integrity failure")
            previous = record["study_id"]
        manifest = StudyManifest(
            study_number=len(records) + 1,
            registered_at=now,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=90),
            source_hash=study_source_hash(root),
            discovery_hash=hashlib.sha256(payload).hexdigest(),
            candidates=candidates,
            predecessor_study_id=previous,
        )
        directory.mkdir(exist_ok=False)
        atomic_write_bytes(directory / "discovery.json", payload)
        atomic_write_bytes(directory / "manifest.json", manifest.model_dump_json(indent=2).encode())
        record = dict(
            study_number=manifest.study_number,
            study_id=manifest.study_id,
            predecessor_study_id=previous,
            registered_at=now.isoformat(),
            directory=str(directory.resolve()),
        )
        record["record_hash"] = canonical_hash(record)
        # Register before opening a ledger: interrupted/failed campaigns remain counted.
        with registry_path.open("ab") as output:
            output.write((json.dumps(record, sort_keys=True) + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
        with ProspectiveLedger(directory / "ledger.sqlite", manifest) as ledger:
            write_summary(directory, ledger.summary(now=now))
        return manifest


def verify_study(directory: Path, root: Path):
    from src.research.prospective_types import StudyManifest

    manifest = StudyManifest.model_validate_json((directory / "manifest.json").read_bytes())
    if manifest.source_hash != study_source_hash(root):
        raise ValueError("frozen source changed; retain this study and register a successor")
    if manifest.discovery_hash != hashlib.sha256((directory / "discovery.json").read_bytes()).hexdigest():
        raise ValueError("frozen discovery changed")
    if not (directory / "ledger.sqlite").is_file():
        raise ValueError("registered ledger is missing; refuse to reset evidence")
    campaign_path = directory.parent / "campaigns.jsonl"
    if not campaign_path.is_file():
        raise ValueError("retained campaign registry is missing")
    records = [json.loads(line) for line in campaign_path.read_text().splitlines()]
    predecessor = None
    matched = False
    for number, record in enumerate(records, 1):
        if (
            record["study_number"] != number
            or record["predecessor_study_id"] != predecessor
            or canonical_hash({k: v for k, v in record.items() if k != "record_hash"}) != record["record_hash"]
        ):
            raise ValueError("campaign registry integrity failure")
        if record["study_id"] == manifest.study_id and record["directory"] == str(directory.resolve()):
            matched = True
        predecessor = record["study_id"]
    if not matched:
        raise ValueError("manifest does not belong to the retained campaign registry")
    registry = load_registry(root)
    for candidate in manifest.candidates:
        if registry.resolve(candidate.strategy_id).spec.definition_hash != candidate.strategy_definition_hash:
            raise ValueError("frozen strategy definition changed")
    return manifest, registry


def write_summary(directory: Path, summary: dict) -> None:
    atomic_write_bytes(directory / "summary.json", _json_bytes(summary))
    account_lines = []
    for row in summary.get("candidates", []):
        account_lines.extend(
            [
                f"## {row.get('symbol')} — {row.get('candidate_id')}",
                "",
                f"Historical screen passed: {row.get('historical_screen_passed')}. "
                f"Current assessment: {row.get('status')}.",
                f"Paper cash: {row.get('cash')} USDT. Marked account value: {row.get('equity')} USDT.",
                f"Net P&L: {row.get('net_pnl')} USDT. Stressed P&L: {row.get('stressed_pnl')} USDT.",
                f"Decisions: {row.get('decisions')}; fills: {row.get('fills')}; "
                f"closed trades: {row.get('closed_trades')}.",
                f"Open quantity: {row.get('position_quantity')}; stale valuation: {row.get('valuation_stale')}.",
                "",
            ]
        )
    lines = [
        "# Live paper study",
        "",
        "Simulated money only. No orders are sent.",
        "",
        f"Updated: {summary.get('updated_at')}",
        f"Status: {summary.get('status')}",
        f"Fixed window: {summary.get('starts_at')} to {summary.get('ends_at')}",
        "",
        "Each candidate is an independent 10,000 USDT paper account (USD approximation); do not add balances.",
        "",
        "Historical screening may have failed. Early gains do not establish an advantage.",
        "Missing feed time and interrupted positions remain part of the evidence.",
        "",
        f"Collector: {summary.get('collector', {}).get('state', 'registered')}.",
        "",
        *account_lines,
        "Full account outcomes, modeled costs, coverage and limitations:",
        "",
        "```json",
        json.dumps(summary, indent=2, default=str),
        "```",
        "",
    ]
    atomic_write_bytes(directory / "report.md", "\n".join(lines).encode())


class StudyProcessor:
    """Keep REST context apart from strictly complete, promptly received live hours."""

    def __init__(self, manifest, registry, ledger):
        self.manifest, self.registry, self.ledger = manifest, registry, ledger
        self.hours = {}
        self.minutes = FinalizedBarLedger(maximum_bars=240)
        self.emitted = set()

    def seed(self, bars):
        for bar in bars:
            if bar.interval != "1h":
                raise ValueError("warmup must contain finalized hourly context")
            # First-seen context and observed live history cannot be rewritten by repair.
            self.hours.setdefault((bar.symbol, bar.start), bar)

    def reset_live(self):
        self.minutes = FinalizedBarLedger(maximum_bars=240)

    def on_bar(self, bar: MarketBar, *, now: datetime):
        if (bar.provider, bar.feed, bar.interval) != ("binance", "spot", "1m"):
            return
        if now < bar.available_at or now < bar.received_at:
            self.ledger.record_gap(at=now, reason="bar_clock_ahead")
            self.reset_live()
            return
        if bar.repair_verified or bar.revision != 0 or not timedelta(0) <= now - bar.end <= timedelta(seconds=5):
            return
        if self.minutes.accept(bar).status != "accepted":
            return
        for hour in aggregate_finalized(self.minutes.bars, "1h"):
            key = (hour.symbol, hour.end)
            if key in self.emitted:
                continue
            self.emitted.add(key)
            self.hours[(hour.symbol, hour.start)] = hour
            if not self.manifest.starts_at < hour.end <= now <= hour.end + timedelta(seconds=5):
                continue
            if now >= self.manifest.ends_at:
                continue
            context = sorted(
                (item for (symbol, _), item in self.hours.items() if symbol == hour.symbol and item.end <= hour.end),
                key=lambda item: item.start,
            )
            # Bounded context is much longer than every registered candidate's warmup.
            context = context[-1000:]
            frame = pd.DataFrame(
                [
                    dict(
                        provider="binance",
                        feed="spot",
                        symbol=b.symbol,
                        interval="1h",
                        open_timestamp=b.start,
                        close_timestamp=b.end,
                        available_at=b.end,
                        open=float(b.open),
                        high=float(b.high),
                        low=float(b.low),
                        close=float(b.close),
                        volume=float(b.volume),
                        finalized=True,
                        revision=1,
                    )
                    for b in context
                ]
            )
            atr = float(gap_safe_atr(frame).iloc[-1])
            if not math.isfinite(atr) or atr <= 0:
                continue
            for candidate in self.manifest.candidates:
                if candidate.symbol != hour.symbol:
                    continue
                signals = eligible_long_signals(frame, candidate.model_dump(), self.registry)
                if signals.empty or int(signals.iloc[-1]["signal"]) != 1:
                    continue
                self.ledger.record_signal(
                    candidate.candidate_id,
                    decision_at=now,
                    bar_end=hour.end,
                    reference_price=hour.close,
                    atr=Decimal(str(atr)),
                    signal_id=canonical_hash([candidate.candidate_id, hour.end.isoformat()]),
                )
            # Drop expired context and identities without changing prospective evidence.
            cutoff = hour.end - timedelta(hours=1000)
            self.hours = {k: v for k, v in self.hours.items() if v.end >= cutoff}
            self.emitted = {k for k in self.emitted if k[1] >= hour.end - timedelta(hours=2)}


class PublicBinanceTransport:
    """Dedicated public HTTP/WebSocket transport, without account configuration."""

    def __init__(self):
        self.metadata = {}

    async def seed(self, symbols, now):
        bars, rules = [], {}
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(
                "https://api.binance.com/api/v3/exchangeInfo", params={"symbols": json.dumps(list(symbols))}
            )
            response.raise_for_status()
            rows = response.json()["symbols"]
            for row in rows:
                symbol = row["symbol"]
                if symbol not in symbols or not _binance_spot_tradable(row):
                    continue
                filters = {item["filterType"]: item for item in row["filters"]}
                tick = Decimal(filters["PRICE_FILTER"]["tickSize"])
                lot = Decimal(filters["LOT_SIZE"]["stepSize"])
                notional = Decimal(filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))["minNotional"])
                if any(not v.is_finite() or v <= 0 for v in (tick, lot, notional)):
                    raise ValueError("invalid spot lot/minimum-notional metadata")
                self.metadata[symbol] = ProviderSymbolMetadata(symbol, tick, True, False, False)
                rules[symbol] = (lot, notional)
            if set(rules) != set(symbols):
                raise ValueError("spot metadata unavailable")
            end = now.replace(minute=0, second=0, microsecond=0)
            for symbol in symbols:
                response = await client.get(
                    "https://api.binance.com/api/v3/klines",
                    params={
                        "symbol": symbol,
                        "interval": "1h",
                        "limit": 1000,
                        "endTime": int(end.timestamp() * 1000) - 1,
                    },
                )
                response.raise_for_status()
                for raw in response.json():
                    if not isinstance(raw, list) or len(raw) != 12:
                        raise ValueError("invalid hourly REST candle")
                    opened = datetime.fromtimestamp(int(raw[0]) / 1000, UTC)
                    closed = datetime.fromtimestamp((int(raw[6]) + 1) / 1000, UTC)
                    if closed > end or closed - opened != timedelta(hours=1):
                        raise ValueError("REST context includes incomplete hour")
                    bars.append(
                        MarketBar(
                            provider="binance",
                            feed="spot",
                            symbol=symbol,
                            interval="1h",
                            start=opened,
                            end=closed,
                            available_at=now,
                            received_at=now,
                            open=raw[1],
                            high=raw[2],
                            low=raw[3],
                            close=raw[4],
                            volume=raw[5],
                            finalized=True,
                            revision=0,
                            repair_verified=True,
                        )
                    )
        return bars, rules

    async def stream(self, symbols):
        adapter = BinanceSpotAdapter(metadata=self.metadata)
        # Only ticker quotes and closed minute candles; independent of alert/broker streams.
        subscription = {
            "method": "SUBSCRIBE",
            "params": [f"{s.lower()}@{kind}" for s in symbols for kind in ("ticker", "kline_1m")],
            "id": 1,
        }
        async with connect(
            "wss://stream.binance.com:9443/ws",
            ssl=verified_client_context(),
            open_timeout=10,
            close_timeout=1,
            ping_interval=20,
            ping_timeout=20,
            max_size=64 * 1024,
        ) as socket:
            await socket.send(json.dumps(subscription))
            async for raw in socket:
                for event in adapter.decode(raw, received_at=datetime.now(UTC)):
                    yield event


async def run_study(
    directory: Path, *, root: Path, duration_seconds: float = 21600, heartbeat_seconds: float = 60, transport=None
):
    from src.research.prospective import ProspectiveLedger

    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration must be positive and finite")
    if not math.isfinite(heartbeat_seconds) or not 0 < heartbeat_seconds <= 60:
        raise ValueError("heartbeat interval must be positive and at most 60 seconds")
    manifest, registry = verify_study(directory, root)
    feed = transport if transport is not None else PublicBinanceTransport()
    symbols = tuple(candidate.symbol for candidate in manifest.candidates)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration_seconds
    with ProspectiveLedger(directory / "ledger.sqlite", manifest) as ledger:
        processor = StudyProcessor(manifest, registry, ledger)
        health = dict(
            state="warming",
            pid=os.getpid(),
            source_root=str(root.resolve()),
            last_feed_event_at=None,
            last_error=None,
            context_refreshes=0,
        )

        def publish(at):
            result = ledger.summary(now=at)
            result["collector"] = dict(health)
            write_summary(directory, result)
            return result

        ledger.record_gap(at=datetime.now(UTC), reason="collector_started_or_resumed")
        publish(datetime.now(UTC))
        next_report = loop.time() + heartbeat_seconds
        last_event = loop.time()
        stalled = False
        pending = None
        repair = None
        iterator = None
        metadata = None
        stage = "seed"
        try:
            while loop.time() < deadline:
                now = datetime.now(UTC)
                if now >= manifest.ends_at:
                    ended = ledger.summary(now=now)
                    if all(Decimal(row["position_quantity"]) == 0 for row in ended["candidates"]):
                        break
                if pending is None:
                    if stage == "seed":
                        pending = asyncio.create_task(feed.seed(symbols, now))
                    else:
                        iterator = feed.stream(symbols).__aiter__() if iterator is None else iterator
                        pending = asyncio.create_task(anext(iterator))
                wait = max(0, min(1, deadline - loop.time(), next_report - loop.time()))
                tasks = {pending} if repair is None else {pending, repair}
                ready, _ = await asyncio.wait(tasks, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
                now = datetime.now(UTC)
                if repair is not None and repair in ready:
                    completed_repair, repair = repair, None
                    try:
                        context, metadata = completed_repair.result()
                        processor.seed(context)
                        health["context_refreshes"] += 1
                    except (httpx.HTTPError, OSError, TimeoutError) as error:
                        health["last_error"] = f"context_refresh:{type(error).__name__}"
                if loop.time() >= next_report:
                    publish(now)
                    next_report = loop.time() + heartbeat_seconds
                if loop.time() - last_event > 30 and not stalled:
                    ledger.record_gap(at=now, reason="feed_unavailable_over_30_seconds")
                    processor.reset_live()
                    stalled = True
                    health["state"] = "stalled"
                if pending not in ready:
                    continue
                task, pending = pending, None
                try:
                    event = task.result()
                    if stage == "seed":
                        history, metadata = event
                        processor.seed(history)
                        stage = "stream"
                    elif isinstance(event, MarketQuote):
                        lot, minimum = metadata[event.symbol]
                        ledger.on_quote(event, now=now, lot_step=lot, min_notional=minimum)
                    elif isinstance(event, MarketBar):
                        processor.on_bar(event, now=now)
                        if (
                            event.end.minute == 0
                            and timedelta(0) <= now - event.end <= timedelta(seconds=5)
                            and repair is None
                        ):
                            repair = asyncio.create_task(feed.seed(symbols, now))
                    elif isinstance(event, ProviderHealthEvent) and event.status in {
                        MonitorHealth.RECONNECTING,
                        MonitorHealth.FAILED,
                        MonitorHealth.STALE,
                    }:
                        ledger.record_gap(at=now, reason=event.reason)
                        processor.reset_live()
                    health["state"] = "observing" if stage == "stream" else "warming"
                    if isinstance(event, ProviderHealthEvent) and event.status != MonitorHealth.HEALTHY:
                        health["state"] = "reconnecting"
                    health["last_feed_event_at"] = now.isoformat()
                    last_event, stalled = loop.time(), False
                except (httpx.HTTPError, OSError, StopAsyncIteration, TimeoutError, WebSocketException) as error:
                    ledger.record_gap(at=now, reason=f"public_feed_unavailable:{type(error).__name__}")
                    processor.reset_live()
                    if iterator is not None:
                        await iterator.aclose()
                    iterator = None
                    stage = "seed"
                    health["state"] = "reconnecting"
                    health["last_error"] = type(error).__name__
                    # Cancellable bounded backoff; heartbeat deadline still respected.
                    await asyncio.sleep(min(1, max(0, deadline - loop.time())))
        except Exception as error:
            health["state"] = "failed"
            health["last_error"] = type(error).__name__
            ledger.record_gap(at=datetime.now(UTC), reason=f"collector_failed:{type(error).__name__}")
            raise
        finally:
            if repair is not None:
                repair.cancel()
                with suppress(asyncio.CancelledError):
                    await repair
            if pending is not None:
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
            if iterator is not None:
                await iterator.aclose()
            ledger.record_gap(at=datetime.now(UTC), reason="collector_stopped_observation_interrupted")
            if health["state"] != "failed":
                health["state"] = "stopped"
            summary = publish(datetime.now(UTC))
        return summary
