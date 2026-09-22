"""Explicitly started, credential-free public spot research collection.

Only recent closed candles are collected: there is deliberately no historical
backfill here. HTTP endpoints follow Binance's public Spot market-data API.
"""

from __future__ import annotations

import fcntl
import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

from src.research.day_trader_context import (
    CalendarSnapshot,
    ContextObservation,
    DayTraderContextProtocol,
    extract_context,
)
from src.research.day_trader_decision import (
    CONTEXT_REPORTS_FILE,
    CONTEXT_SUMMARY_FILE,
    DecisionContextReport,
    gate_suggestion,
    load_context_reports,
    retain_context_reports,
)
from src.research.day_trader_lifecycle import (
    LifecycleLedger,
    LifecycleObservation,
    PaperLifecycle,
    advance_lifecycle,
    read_lifecycle_history,
)
from src.research.live_paper_signals import LiveSignalEvent, LiveSignalState, SignalEventLedger, should_publish
from src.research.round_two_contracts import RoundObservation, RoundStatus, _utc
from src.research.round_two_quality import append_observations, load_observations, summarize_quality
from src.research.round_two_registry import (
    _write_first_manifest,
    append_jsonl_fsync,
    jsonl_writer_lock,
    load_round_protocol,
)
from src.research.round_two_runtime import (
    SUMMARY_FILE,
    _bind_evaluation,
    _candidate_snapshot,
    _native_round_payload,
    _provider_health,
    _recent_candidate_results,
    _strategy_registry,
    _write_atomic_json,
)
from src.research.round_two_walkforward import CandidateResult
from src.research.trend_advisor import AdvisorRoundReport, TrendAdvisorSuggestion, advise
from src.strategies.types import canonical_hash
from src.utils.tls import verified_client_context

STATE_FILE = "live-paper-signal-state.json"
CURSOR_FILE = "live-paper-signal-cursor.json"
STOP_FILE = "live-paper-signal-stop.json"
CONTEXT_OBSERVATIONS_FILE = "day-trader-context-observations.jsonl"
CALENDAR_FILE = "day-trader-calendar.jsonl"
PUBLICATIONS_FILE = "paper-publications.jsonl"


def _context_observations(directory, observations):
    """Use enriched provenance only when it exactly matches the retained bar."""
    path = directory / CONTEXT_OBSERVATIONS_FILE
    rich = {}
    if path.exists():
        data = path.read_bytes()
        if data and not data.endswith(b"\n"):
            raise ValueError("unterminated context observation")
        for line in data.splitlines():
            row = ContextObservation.model_validate_json(line)
            if not row.finalized:
                raise ValueError("retained context observation is not final")
            if row.source_key in rich:
                raise ValueError("duplicate context observation")
            rich[row.source_key] = row
    selected = []
    for row in observations:
        enriched = rich.get(row.source_key)
        if enriched is not None:
            if enriched.model_dump(include=set(RoundObservation.model_fields)) != row.model_dump():
                raise ValueError("context observation identity mismatch")
            selected.append(enriched)
        else:
            selected.append(row)
    return tuple(selected)


def _calendar_at(directory, now):
    """Read retained, explicitly covered local calendar revisions as-of now."""
    path = directory / CALENDAR_FILE
    if not path.exists():
        return None
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise ValueError("unterminated calendar snapshot")
    visible, identities = [], {}
    for line in data.splitlines():
        snapshot = CalendarSnapshot.model_validate_json(line)
        if snapshot.available_at > now:
            continue
        key = (snapshot.source, snapshot.revision)
        if key in identities and identities[key] != snapshot.identity_hash:
            raise ValueError("conflicting calendar revision")
        identities[key] = snapshot.identity_hash
        visible.append(snapshot)
    if len({item.source for item in visible}) > 1:
        raise ValueError("conflicting calendar sources")
    return max(visible, key=lambda item: (item.available_at, item.published_at), default=None)


def _calendar_exclusions(snapshot, settings, now):
    if snapshot is None:
        return ("calendar_missing",)
    expiry = min(snapshot.valid_until, snapshot.published_at + timedelta(seconds=settings.maximum_calendar_age_seconds))
    if snapshot.available_at > now:
        return ("calendar_unavailable",)
    if now >= expiry:
        return ("calendar_stale",)
    if snapshot.coverage_starts_at > now - timedelta(
        minutes=settings.blackout_after_minutes
    ) or snapshot.coverage_ends_at < now + timedelta(minutes=settings.blackout_before_minutes):
        return ("calendar_coverage_missing",)
    return ()


def import_calendar_snapshot(directory: Path, source: Path, *, now: datetime | None = None) -> CalendarSnapshot:
    """Explicit local import; actual receipt time prevents backdated availability."""
    directory, now = Path(directory), _utc(now or _now(), "calendar import time")
    protocol = load_round_protocol(directory)
    settings = DayTraderContextProtocol(round_protocol=protocol)
    with Path(source).open("rb") as stream:
        payload = stream.read(1048577)
    if len(payload) > 1048576:
        raise ValueError("calendar input exceeds size limit")
    supplied = CalendarSnapshot.model_validate_json(payload)
    if supplied.available_at > now or supplied.published_at > now:
        raise ValueError("calendar import cannot contain future availability")
    snapshot = CalendarSnapshot.model_validate({**supplied.model_dump(), "available_at": now})
    if _calendar_exclusions(snapshot, settings, now):
        raise ValueError("calendar import requires current covered evidence")
    path = directory / CALENDAR_FILE
    with jsonl_writer_lock(path):
        data = path.read_bytes() if path.exists() else b""
        if data and not data.endswith(b"\n"):
            raise ValueError("unterminated calendar history")
        retained = tuple(CalendarSnapshot.model_validate_json(line) for line in data.splitlines())
        for old in retained:
            if old.source != snapshot.source:
                raise ValueError("calendar source changed")
            if old.revision == snapshot.revision:
                if old.model_dump(exclude={"available_at"}) != snapshot.model_dump(exclude={"available_at"}):
                    raise ValueError("calendar revision conflict")
                return old
        if retained and (
            snapshot.published_at < max(row.published_at for row in retained)
            or now < max(row.available_at for row in retained)
        ):
            raise ValueError("calendar revision or receipt regressed")
        append_jsonl_fsync(path, [snapshot.model_dump(mode="json")], writer_lock_held=True)
    receipt = dict(
        protocol_hash=protocol.identity_hash,
        received_at=now.isoformat(),
        input_hash=canonical_hash(supplied.model_dump(mode="json")),
        calendar_hash=snapshot.identity_hash,
    )
    receipt_path = directory / "day-trader-calendar-imports.jsonl"
    with jsonl_writer_lock(receipt_path):
        append_jsonl_fsync(receipt_path, [receipt], writer_lock_held=True)
    return snapshot


def _calendar_health(directory, protocol, ledger, now):
    """Retain outages/recovery; process recreation cannot reset continuous warm-up."""
    settings = DayTraderContextProtocol(round_protocol=protocol)
    try:
        snapshot = _calendar_at(directory, now)
        reasons = _calendar_exclusions(snapshot, settings, now)
    except (OSError, ValueError):
        snapshot, reasons = None, ("calendar_invalid",)
    events = ledger.events()
    calendar_events = [
        event for event in events if event.detail in {"calendar_unavailable", "calendar_reconnect_warmup"}
    ]
    last = calendar_events[-1] if calendar_events else None
    if reasons:
        if last is None or last.detail != "calendar_unavailable":
            ledger.append(LiveSignalEvent(kind="gap", at=now, detail="calendar_unavailable"))
        return reasons
    if last is not None and last.detail == "calendar_unavailable":
        last = LiveSignalEvent(kind="reconnect", at=now, detail="calendar_reconnect_warmup")
        ledger.append(last)
    if last is not None and now - last.at < timedelta(minutes=protocol.warmup_minutes):
        return ("calendar_reconnect_warmup",)
    return ()


def _now() -> datetime:
    return datetime.now(UTC)


class SpotFeed(Protocol):
    def observations(self, symbols: tuple[str, ...]) -> tuple[RoundObservation, ...]: ...


def _public_json(path: str, params: dict) -> object:
    # Fixed host and allowlisted read-only paths; no environment/auth configuration.
    if path not in {"/api/v3/klines", "/api/v3/ticker/bookTicker", "/api/v3/time"}:
        raise ValueError("unsupported public endpoint")
    with urlopen(
        "https://data-api.binance.vision" + path + "?" + urlencode(params),
        timeout=4,
        context=verified_client_context(),
    ) as response:
        payload = response.read(262145)
    if len(payload) > 262144:
        raise ValueError("oversized public response")
    return json.loads(payload)


def _public_quote(symbol: str) -> dict:
    """One bounded public ticker frame; E is the provider event timestamp.

    Spot @ticker includes best bid/ask and quantities as well as event time.
    The unrelated 24-hour statistics and statistics close time are not used.
    """
    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect

    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise ValueError("unsupported quote symbol")
    try:
        with connect(
            f"wss://stream.binance.com:9443/ws/{symbol.lower()}@ticker",
            ssl=verified_client_context(),
            open_timeout=5,
            close_timeout=1,
            max_size=16384,
            proxy=None,
        ) as socket:
            return json.loads(socket.recv(timeout=3))
    except WebSocketException as error:
        raise OSError("public quote unavailable") from error


class FinalizedSpotFeed:
    """Poll the latest closed one-minute candle and a contemporaneous public quote."""

    def __init__(
        self, *, fetch_json: Callable = _public_json, fetch_quote: Callable = _public_quote, clock: Callable = _now
    ):
        self.fetch_json, self.fetch_quote, self.clock = fetch_json, fetch_quote, clock

    def observations(self, symbols: tuple[str, ...]) -> tuple[RoundObservation, ...]:
        rows = []
        server = self.fetch_json("/api/v3/time", {})
        if not isinstance(server, dict) or type(server.get("serverTime")) is not int:
            raise ValueError("invalid server clock")
        server_at = datetime.fromtimestamp(server["serverTime"] / 1000, UTC)
        for symbol in symbols:
            if symbol not in {"BTCUSDT", "ETHUSDT"}:
                raise ValueError("unsupported symbol")
            candles = self.fetch_json("/api/v3/klines", {"symbol": symbol, "interval": "1m", "limit": 2})
            candle_received = _utc(self.clock(), "receipt")
            if not isinstance(candles, list) or len(candles) > 2:
                raise ValueError("invalid candle response")
            finalized = []
            for candle in candles:
                if not isinstance(candle, list) or len(candle) != 12:
                    raise ValueError("invalid candle shape")
                opened, closed = candle[0], candle[6]
                if type(opened) is not int or type(closed) is not int or opened % 60000 or closed != opened + 59999:
                    raise ValueError("invalid candle interval")
                boundary = datetime.fromtimestamp((closed + 1) / 1000, UTC)
                if boundary <= min(candle_received, server_at):
                    finalized.append((boundary, candle))
            if not finalized:
                continue
            boundary, candle = max(finalized, key=lambda item: item[0])
            # Old candles remain absent, never re-labelled as fresh on receipt.
            if candle_received - boundary >= timedelta(seconds=15):
                continue
            quote = self.fetch_quote(symbol)
            received = _utc(self.clock(), "receipt")
            if received < candle_received:
                raise ValueError("clock regression")
            if (
                not isinstance(quote, dict)
                or quote.get("s") != symbol
                or quote.get("e") != "24hrTicker"
                or type(quote.get("E")) is not int
            ):
                raise ValueError("invalid timestamped quote identity")
            if not 0 <= quote["E"] < 253402300799000:
                raise ValueError("quote timestamp out of range")
            provider_at = datetime.fromtimestamp(quote["E"] / 1000, UTC)
            if not timedelta(0) <= received - provider_at < timedelta(seconds=15):
                raise ValueError("quote event is stale or in the future")
            for field in ("b", "a", "B", "A"):
                try:
                    value = Decimal(str(quote.get(field)))
                except InvalidOperation as error:
                    raise ValueError("invalid quote number") from error
                if not value.is_finite() or value <= 0:
                    raise ValueError("invalid quote price or size")
            rows.append(
                ContextObservation(
                    provider="binance",
                    feed="spot",
                    symbol=symbol,
                    provider_at=boundary,
                    received_at=received,
                    available_at=received,
                    source_key=f"binance:spot:{symbol}:1m:{candle[0]}",
                    open=candle[1],
                    high=candle[2],
                    low=candle[3],
                    close=candle[4],
                    volume=candle[5],
                    bid=quote["b"],
                    ask=quote["a"],
                    bid_size=quote["B"],
                    ask_size=quote["A"],
                    quote_provider_at=provider_at,
                    quote_received_at=received,
                    quote_available_at=received,
                    quote_source_key=f"binance:spot:ticker:{symbol}:{quote['E']}:{received.isoformat()}",
                )
            )
        return tuple(rows)


@contextmanager
def _service_lock(directory: Path) -> Iterator[None]:
    with (directory / "live-paper-signal.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("live paper signal service already running") from error
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _retained_state(directory: Path, protocol_hash: str) -> LiveSignalState | None:
    path = directory / STATE_FILE
    state = LiveSignalState.model_validate_json(path.read_text()) if path.exists() else None
    if state is not None and state.protocol_hash != protocol_hash:
        raise ValueError("live state protocol mismatch")
    return state


def _validate_context_history(directory, protocol):
    if any(
        (directory / name).exists()
        for name in (CONTEXT_REPORTS_FILE, CONTEXT_SUMMARY_FILE, "day-trader-context-manifest.json")
    ):
        settings = DayTraderContextProtocol(round_protocol=protocol)
        return load_context_reports(
            directory, protocol_hash=protocol.identity_hash, context_protocol_hash=settings.identity_hash
        )
    return ()


def _advance_retained_lifecycles(directory, protocol, now):
    """Project later retained evidence into hypotheses, never into live decisions.

    Reading retained bars rather than just this poll's novel batch makes an
    interrupted write recoverable. The lifecycle ledger deduplicates revisions;
    a missed minute expires evidence instead of inferring a price crossing.
    """
    if not (directory / "paper-lifecycles.jsonl").exists():
        return
    ledger = LifecycleLedger(directory, protocol_hash=protocol.identity_hash)
    reports = _validate_context_history(directory, protocol)
    observations = _context_observations(directory, load_observations(directory))
    for lifecycle in ledger.latest():
        if lifecycle.completed_at is not None:
            continue
        previous = lifecycle.last_observation
        after = previous.bar.provider_at if previous and previous.bar else lifecycle.created_at
        bars = sorted(
            (
                row
                for row in observations
                if row.symbol == lifecycle.origin_report.suggestion.symbol
                and row.provider_at > after
                and row.available_at <= now
            ),
            key=lambda row: row.provider_at,
        )
        if bars:
            # More than one unseen minute is necessarily stale under this live
            # protocol. Resolve the earliest missed observation conservatively.
            bar = bars[0]
            matching = [
                report
                for report in reports
                if report.suggestion.candidate_hash == lifecycle.origin_report.suggestion.candidate_hash
                and report.suggestion.symbol == bar.symbol
                and bar.provider_at <= report.evaluated_at <= now
            ]
            context = max(matching, key=lambda item: item.evaluated_at, default=None)
            finalized = getattr(bar, "finalized", True)
            projected = RoundObservation.model_validate(bar.model_dump(include=set(RoundObservation.model_fields)))
            observation = LifecycleObservation(
                bar=projected, finalized=finalized, evaluated_at=now, context_report=context
            )
        else:
            expected = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
            if now <= expected + timedelta(seconds=15):
                continue
            observation = LifecycleObservation(evaluated_at=now)
        advanced = advance_lifecycle(lifecycle, observation)
        if advanced != lifecycle:
            ledger.append(advanced)


def _publication_records(directory, protocol, now):
    """The durable publication journal is the commit point for all projections."""
    path = directory / PUBLICATIONS_FILE
    if not path.exists():
        return ()
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise ValueError("unterminated publication journal")
    reports = {item.report_hash: item for item in _validate_context_history(directory, protocol)}
    records, seen = [], set()
    for line in data.splitlines():
        item = PaperLifecycle.model_validate_json(line)
        if (
            item.revision != 0
            or item.created_at > now
            or item.origin_report.protocol_hash != protocol.identity_hash
            or reports.get(item.origin_report.report_hash) != item.origin_report
            or item.origin_report.report_hash in seen
        ):
            raise ValueError("publication journal identity mismatch")
        if records and item.created_at < records[-1].created_at:
            raise ValueError("publication journal clock regression")
        seen.add(item.origin_report.report_hash)
        records.append(item)
    return tuple(records)


def _reconcile_publications(directory, protocol, signals, now):
    """Recover exact committed evidence, without scoring or extending its expiry."""
    records = _publication_records(directory, protocol, now)
    events = signals.events()
    identities = {"lifecycle:" + item.lifecycle_hash for item in records}
    if any(
        event.kind == "published"
        and event.detail
        and event.detail.startswith("lifecycle:")
        and event.detail not in identities
        for event in events
    ):
        raise ValueError("publication event has no committed evidence")
    if not records:
        return None
    lifecycles = LifecycleLedger(directory, protocol_hash=protocol.identity_hash)
    # Validate the complete chain before recovering another projection.
    retained_hashes = {item.record_hash for item in lifecycles.events()}
    for item in records:
        expected = LiveSignalEvent(
            kind="published",
            at=item.created_at,
            posture="long_research",
            candidate_hash=item.origin_report.suggestion.candidate_hash,
            detail="lifecycle:" + item.lifecycle_hash,
        )
        matching = [event for event in events if event.kind == "published" and event.detail == expected.detail]
        if matching and matching != [expected]:
            raise ValueError("publication projection identity mismatch")
        if not matching:
            signals.append(expected)
        if item.record_hash not in retained_hashes:
            lifecycles.append(item)
    return records[-1]


def _validate_published_state(directory, protocol, state, now):
    """Read-only identity check; only the writer may repair incomplete projections."""
    records = _publication_records(directory, protocol, now)
    if not records or records[-1].origin_report.suggestion != state.suggestion:
        raise ValueError("state has no exact committed publication")
    if records[-1].created_at > state.updated_at:
        raise ValueError("state predates publication")
    manifest = json.loads((directory / "signal-events-manifest.json").read_text())
    if manifest != {"format": "live-paper-signal-events-v1", "protocol_hash": protocol.identity_hash}:
        raise ValueError("signal event protocol mismatch")
    raw = (directory / "signal-events.jsonl").read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("unterminated signal event evidence")
    events = tuple(LiveSignalEvent.model_validate_json(line) for line in raw.splitlines())
    lifecycles = read_lifecycle_history(directory, protocol_hash=protocol.identity_hash)
    origins = {item.record_hash for item in lifecycles if item.revision == 0}
    identities = {"lifecycle:" + item.lifecycle_hash for item in records}
    if any(
        event.at > now
        or (
            event.kind == "published"
            and event.detail
            and event.detail.startswith("lifecycle:")
            and event.detail not in identities
        )
        for event in events
    ):
        raise ValueError("unbound publication event")
    for item in records:
        expected = LiveSignalEvent(
            kind="published",
            at=item.created_at,
            posture="long_research",
            candidate_hash=item.origin_report.suggestion.candidate_hash,
            detail="lifecycle:" + item.lifecycle_hash,
        )
        if [event for event in events if event.kind == "published" and event.detail == expected.detail] != [expected]:
            raise ValueError("publication event missing or mismatched")
        if item.record_hash not in origins:
            raise ValueError("initial lifecycle missing or mismatched")


def read_live_signal_status(directory: Path, *, now: datetime | None = None) -> LiveSignalState:
    """Read-only freshness projection; reading never makes old evidence current."""
    directory, now = Path(directory), _utc(now or _now(), "status timestamp")
    protocol = load_round_protocol(directory)
    state = _retained_state(directory, protocol.identity_hash)
    if state is None:
        return LiveSignalState(kind="stopped", protocol_hash=protocol.identity_hash, updated_at=now)
    if now < state.updated_at:
        return LiveSignalState(
            kind="failed", protocol_hash=protocol.identity_hash, updated_at=now, reasons=("clock_regression",)
        )
    try:
        retained_context = _validate_context_history(directory, protocol)
    except (OSError, ValueError):
        return LiveSignalState(
            kind="abstaining",
            protocol_hash=protocol.identity_hash,
            updated_at=state.updated_at,
            evaluated_at=state.evaluated_at,
            reasons=("context_evidence_unavailable",),
        )
    if state.kind not in {"stopped", "failed"} and (
        now - state.updated_at >= timedelta(seconds=min(15, protocol.maximum_observation_age_seconds))
        or (state.suggestion is not None and now >= state.suggestion.expires_at)
    ):
        return LiveSignalState(
            kind="stale",
            protocol_hash=protocol.identity_hash,
            updated_at=state.updated_at,
            evaluated_at=state.evaluated_at,
            reasons=("evidence_expired",),
        )
    if state.kind == "published":
        try:
            summary = json.loads((directory / CONTEXT_SUMMARY_FILE).read_text())
            settings = DayTraderContextProtocol(round_protocol=protocol)
            if summary["protocol_hash"] != protocol.identity_hash or (
                summary["context_protocol_hash"] != settings.identity_hash
            ):
                raise ValueError("context summary identity mismatch")
            decisions = tuple(DecisionContextReport.model_validate(item) for item in summary["reports"])
            matching = next(item for item in decisions if item.suggestion == state.suggestion)
            if matching.context is None or matching.context.context_protocol_hash != settings.identity_hash:
                raise ValueError("context settings mismatch")
            if matching not in retained_context:
                raise ValueError("context report not retained")
            if gate_suggestion(matching.advisor, matching.context, now).suggestion.posture != "long_research":
                raise ValueError("context no longer eligible")
        except (OSError, ValueError, KeyError, TypeError, StopIteration):
            return LiveSignalState(
                kind="abstaining",
                protocol_hash=protocol.identity_hash,
                updated_at=state.updated_at,
                evaluated_at=state.evaluated_at,
                reasons=("context_evidence_unavailable",),
            )
        try:
            _validate_published_state(directory, protocol, state, now)
        except (OSError, ValueError, KeyError, TypeError):
            return LiveSignalState(
                kind="abstaining",
                protocol_hash=protocol.identity_hash,
                updated_at=state.updated_at,
                evaluated_at=state.evaluated_at,
                reasons=("publication_evidence_unavailable",),
            )
    return state


def request_stop(directory: Path) -> None:
    directory = Path(directory)
    protocol = load_round_protocol(directory)
    _write_atomic_json(directory / STOP_FILE, {"protocol_hash": protocol.identity_hash})


class LivePaperSignalRunner:
    def __init__(self, feed: SpotFeed | None = None, *, clock: Callable = _now):
        self.feed, self.clock = feed or FinalizedSpotFeed(clock=clock), clock

    def run_once(self, directory: Path) -> LiveSignalState:
        directory = Path(directory)
        load_round_protocol(directory)  # Refuse unregistered locations before any write.
        with _service_lock(directory):
            return self._run_once(directory)

    def _run_once(self, directory: Path) -> LiveSignalState:
        protocol = load_round_protocol(directory)
        ledger = SignalEventLedger(directory, protocol_hash=protocol.identity_hash)
        previous = _retained_state(directory, protocol.identity_hash)
        now = _utc(self.clock(), "evaluation timestamp")

        def save(kind, reasons=(), suggestion=None, evaluated_at=None):
            try:
                _advance_retained_lifecycles(directory, protocol, now)
            except (ValueError, OSError):
                kind, reasons, suggestion = "failed", ("lifecycle_evidence_unavailable",), None
            state = LiveSignalState(
                kind=kind,
                protocol_hash=protocol.identity_hash,
                updated_at=now,
                evaluated_at=evaluated_at,
                reasons=tuple(reasons)[:16],
                suggestion=suggestion,
            )
            _write_atomic_json(directory / STATE_FILE, state.model_dump(mode="json"))
            return state

        if previous is not None and now < previous.updated_at:
            return save("failed", ("clock_regression",))
        if (directory / STOP_FILE).exists():
            if json.loads((directory / STOP_FILE).read_text()) != {"protocol_hash": protocol.identity_hash}:
                raise ValueError("stop control protocol mismatch")
            ledger.append(LiveSignalEvent.stopped(now=now, reason="user_stopped"))
            return save("stopped", ("user_stopped",))
        try:
            _validate_context_history(directory, protocol)
        except (OSError, ValueError):
            return save("failed", ("context_evidence_unavailable",))
        try:
            publication = _reconcile_publications(directory, protocol, ledger, now)
            if publication is not None and (
                previous is None
                or previous.updated_at < publication.created_at
                or (previous.updated_at == publication.created_at and previous.kind == "failed")
            ):
                suggestion = publication.origin_report.suggestion
                fresh = now < suggestion.expires_at
                previous = save(
                    "published" if fresh else "stale",
                    () if fresh else ("evidence_expired",),
                    suggestion=suggestion if fresh else None,
                    evaluated_at=publication.created_at,
                )
            _advance_retained_lifecycles(directory, protocol, now)
        except (OSError, ValueError):
            return save("failed", ("lifecycle_evidence_unavailable",))
        if previous is None or previous.kind == "stopped":
            ledger.append(LiveSignalEvent.started(now=now))
        try:
            fetched = tuple(self.feed.observations(protocol.symbols))
            now_after = _utc(self.clock(), "evaluation timestamp")
            if now_after < now:
                return save("failed", ("clock_regression",))
            now = now_after
        except (OSError, ValueError, KeyError, TypeError, TimeoutError):
            ledger.append(LiveSignalEvent(kind="provider_health", at=now, detail="provider_unavailable"))
            return save("failed", ("provider_unavailable",))
        retained = load_observations(directory)
        existing = {row.source_key: row for row in retained}
        novel = []
        novel_context = []
        try:
            for fetched_row in fetched:
                enriched = (
                    ContextObservation.model_validate(fetched_row.model_dump())
                    if isinstance(fetched_row, ContextObservation)
                    else None
                )
                row = RoundObservation.model_validate(
                    fetched_row.model_dump(include=set(RoundObservation.model_fields))
                )
                row.validate_for(protocol)
                if (
                    (enriched is not None and not enriched.finalized)
                    or row.close is None
                    or row.provider_error is not None
                    or row.available_at > now
                    or now - row.provider_at >= timedelta(seconds=min(15, protocol.maximum_observation_age_seconds))
                ):
                    raise ValueError("invalid observation timing or finality")
                old = existing.get(row.source_key)
                if old is not None:
                    # Re-polling does not revise the first receipt or its quote.
                    fields = {"received_at", "available_at", "bid", "ask"}
                    if old.model_dump(exclude=fields) != row.model_dump(exclude=fields):
                        raise ValueError("conflicting finalized candle")
                    continue
                latest = max((item.provider_at for item in retained if item.symbol == row.symbol), default=None)
                if latest is not None and row.provider_at <= latest:
                    raise ValueError("late observation")
                novel.append(row)
                if enriched is not None:
                    novel_context.append(enriched)
            append_observations(directory, protocol, novel)
            # Rich inputs are retained separately to preserve the existing
            # RoundObservation contract used by walk-forward quality checks.
            if novel_context:
                path = directory / CONTEXT_OBSERVATIONS_FILE
                with jsonl_writer_lock(path):
                    append_jsonl_fsync(
                        path, [row.model_dump(mode="json") for row in novel_context], writer_lock_held=True
                    )
        except (ValueError, AttributeError):
            ledger.append(LiveSignalEvent(kind="gap", at=now, detail="invalid_observation"))
            return save("abstaining", ("invalid_observation",))
        # The display state can become stale/warming during empty successful
        # polls. Recover from retained interruption evidence, never that projection.
        interrupted = False
        for event in ledger.events():
            if event.kind == "provider_health" and event.detail == "provider_unavailable":
                interrupted = True
            elif event.kind == "reconnect":
                interrupted = False
        if interrupted and novel:
            ledger.append(LiveSignalEvent(kind="reconnect", at=now, detail="reconnect_warmup"))
        observations = load_observations(directory)
        quality = summarize_quality(observations, protocol)
        reasons = set(quality.reasons_for(now))
        health = _provider_health(protocol, observations, quality, now)
        reasons.update(health.exclusions)
        reasons.update(getattr(self.feed, "context_exclusions", ()))
        calendar_reasons = _calendar_health(directory, protocol, ledger, now)
        reasons.update(calendar_reasons)
        retained_events = ledger.events()
        markers = [event.at for event in retained_events if event.kind in {"started", "reconnect", "gap"}]
        if markers and now - max(markers) < timedelta(minutes=protocol.warmup_minutes):
            reasons.add("reconnect_warmup")
        recovering = any(event.kind == "reconnect" for event in retained_events)
        if interrupted and not novel:
            reasons.add("reconnect_warmup")
        if "calendar_reconnect_warmup" in reasons:
            return save("warming", ("calendar_reconnect_warmup", *sorted(reasons - {"calendar_reconnect_warmup"})))
        if recovering and {"reconnect_warmup", "continuity_warmup"} & reasons:
            # Keep collecting evidence, but do not evaluate or publish until a
            # full continuous window has elapsed after recovery (and later gaps).
            return save("stale" if "observation_stale" in reasons else "warming", sorted(reasons))
        if not novel:
            if reasons:
                return save("stale" if "observation_stale" in reasons else "warming", sorted(reasons))
            return read_live_signal_status(directory, now=now)
        cursor_path = directory / CURSOR_FILE
        cursor = (
            json.loads(cursor_path.read_text())
            if cursor_path.exists()
            else {"protocol_hash": protocol.identity_hash, "keys": []}
        )
        if cursor.get("protocol_hash") != protocol.identity_hash:
            raise ValueError("evaluation cursor protocol mismatch")
        latest_keys = [
            max((row for row in observations if row.symbol == symbol), key=lambda row: row.provider_at).source_key
            for symbol in protocol.symbols
            if any(row.symbol == symbol for row in observations)
        ]
        if latest_keys == cursor["keys"]:
            return read_live_signal_status(directory, now=now)
        # Reserve once before evaluation: a crash can skip, never repeat, a decision.
        _write_atomic_json(cursor_path, {"protocol_hash": protocol.identity_hash, "keys": latest_keys})
        ledger.append(LiveSignalEvent(kind="provider_health", at=now, detail=health.state))
        try:
            report = self._report(directory, protocol, observations, quality, health, now, reasons)
        except (ValueError, OSError):
            ledger.append(LiveSignalEvent(kind="abstaining", at=now, detail="retained_evidence_unavailable"))
            return save("failed", ("retained_evidence_unavailable",))
        ledger.append(LiveSignalEvent(kind="evaluated", at=now, detail="finalized_observation_evaluated"))
        evaluated_at = now
        now = _utc(self.clock(), "publication timestamp")
        if now < evaluated_at:
            return save("failed", ("clock_regression",))
        if any(
            now - row.provider_at >= timedelta(seconds=min(15, protocol.maximum_observation_age_seconds))
            for row in novel
        ):
            return save("stale", ("evidence_expired",), evaluated_at=evaluated_at)
        suggestions = [item for item in report.trend_advisor if item.posture == "long_research"]
        if suggestions and all(now >= item.expires_at for item in suggestions):
            return save("stale", ("evidence_expired",), evaluated_at=evaluated_at)
        suggestions = [item for item in suggestions if now < item.expires_at]
        if not reasons and suggestions:
            suggestion = suggestions[0]
            if should_publish(previous.suggestion if previous else None, suggestion, now):
                try:
                    contexts = _validate_context_history(directory, protocol)
                    context = next(item for item in reversed(contexts) if item.suggestion == suggestion)
                    lifecycle = PaperLifecycle.from_report(context, created_at=now)
                    LifecycleLedger(directory, protocol_hash=protocol.identity_hash).events()
                    committed = _publication_records(directory, protocol, now)
                except (ValueError, OSError, StopIteration):
                    return save("failed", ("lifecycle_evidence_unavailable",), evaluated_at=now)
                if not any(item.origin_report.report_hash == context.report_hash for item in committed):
                    path = directory / PUBLICATIONS_FILE
                    with jsonl_writer_lock(path):
                        append_jsonl_fsync(path, [lifecycle.model_dump(mode="json")], writer_lock_held=True)
                _reconcile_publications(directory, protocol, ledger, now)
            return save("published", suggestion=suggestion, evaluated_at=now)
        reasons.update(reason for item in report.trend_advisor for reason in item.reasons)
        ledger.append(LiveSignalEvent(kind="abstaining", at=now, posture="stand_aside", detail="research_gates_unmet"))
        kind = (
            "warming"
            if {"reconnect_warmup", "continuity_warmup", "insufficient_trend_history"} & reasons
            else "abstaining"
        )
        return save(kind, sorted(reasons), evaluated_at=now)

    @staticmethod
    def _report(directory, protocol, observations, quality, health, now, exclusions):
        registry = _strategy_registry()
        results = _recent_candidate_results(directory)
        if any(item.status == RoundStatus.EXPERIMENTAL_PAPER_ONLY for item in results):
            if not (directory / "evaluation-manifest.json").exists():
                raise ValueError("evaluation manifest missing")
            _bind_evaluation(directory, protocol, registry)
        suggestions = []
        contexts = []
        settings = DayTraderContextProtocol(round_protocol=protocol)
        context_rows = _context_observations(directory, observations)
        calendar = _calendar_at(directory, now)
        for candidate in protocol.candidates[:100]:
            result = next((item for item in results if item.candidate == candidate), None)
            if result is None or exclusions:
                result = CandidateResult(
                    candidate=candidate, status=RoundStatus.INSUFFICIENT_DATA, reasons=("live_collection_gate",)
                )
            suggestion = advise(protocol, result, quality, observations, decision_at=now, registry=registry)
            # Receipt-time freshness cannot extend the lifetime of a provider
            # event. Carry the stricter deadline in the shared suggestion itself
            # so the report, persisted state and downstream consumers agree.
            latest_by_symbol = [
                max(
                    (row.provider_at for row in observations if row.symbol == symbol and row.available_at <= now),
                    default=None,
                )
                for symbol in protocol.symbols
            ]
            if all(stamp is not None for stamp in latest_by_symbol):
                source_deadline = min(latest_by_symbol) + timedelta(
                    seconds=min(15, protocol.maximum_observation_age_seconds)
                )
                fields = suggestion.model_dump()
                fields["expires_at"] = min(suggestion.expires_at, source_deadline)
                if fields["expires_at"] <= now and suggestion.posture == "long_research":
                    fields.update(
                        posture="stand_aside",
                        entry_low=None,
                        entry_high=None,
                        invalidation=None,
                        target=None,
                        reasons=("evidence_expired",),
                    )
                suggestion = TrendAdvisorSuggestion.model_validate(fields)
            symbol_rows = tuple(row for row in context_rows if row.symbol == suggestion.symbol)
            context = extract_context(settings, symbol_rows, now, calendar) if symbol_rows else None
            decision = gate_suggestion(suggestion, context, now)
            contexts.append(decision)
            suggestions.append(decision.suggestion)
        retain_context_reports(
            directory,
            tuple(contexts),
            protocol_hash=protocol.identity_hash,
            context_protocol_hash=settings.identity_hash,
        )
        _write_atomic_json(
            directory / CONTEXT_SUMMARY_FILE,
            {
                "protocol_hash": protocol.identity_hash,
                "context_protocol_hash": settings.identity_hash,
                "reports": [item.model_dump(mode="json") for item in contexts],
            },
        )
        identity = (
            json.dumps(
                {"protocol_hash": protocol.identity_hash, "policy_hash": suggestions[0].policy_hash},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        manifest = directory / "trend-advisor-manifest.json"
        if not _write_first_manifest(manifest, identity.encode()) and manifest.read_text() != identity:
            raise ValueError("advisor policy changed; register a new round")
        decisions = directory / "trend-advisor-decisions.jsonl"
        with jsonl_writer_lock(decisions):
            if decisions.exists():
                data = decisions.read_bytes()
                if data and not data.endswith(b"\n"):
                    raise ValueError("unterminated advisor decision")
                for line in data.splitlines():
                    retained = TrendAdvisorSuggestion.model_validate_json(line)
                    if retained.protocol_hash != protocol.identity_hash:
                        raise ValueError("advisor decision protocol mismatch")
            append_jsonl_fsync(decisions, [item.model_dump(mode="json") for item in suggestions], writer_lock_held=True)
        status = (
            RoundStatus.EXPERIMENTAL_PAPER_ONLY
            if any(item.posture == "long_research" for item in suggestions)
            else RoundStatus.INSUFFICIENT_DATA
            if not results
            else RoundStatus.REJECTED
        )
        report = AdvisorRoundReport(
            round_id=protocol.round_id,
            protocol_hash=protocol.identity_hash,
            status=status,
            reasons=("live_paper_research",),
            provider_health=health,
            candidates=tuple(_candidate_snapshot(item) for item in results),
            trend_advisor=tuple(suggestions),
        )
        _write_atomic_json(directory / SUMMARY_FILE, _native_round_payload(report))
        return report

    def start(self, directory: Path, *, poll_seconds: float = 5) -> LiveSignalState:
        """Foreground process, supervised by the app; no daemon or detached spawn."""
        directory = Path(directory)
        protocol = load_round_protocol(directory)
        if not 1 <= poll_seconds <= 60:
            raise ValueError("poll interval must be between 1 and 60 seconds")
        with _service_lock(directory):
            (directory / STOP_FILE).unlink(missing_ok=True)
            ledger = SignalEventLedger(directory, protocol_hash=protocol.identity_hash)
            ledger.append(LiveSignalEvent(kind="reconnect", at=self.clock(), detail="process_start_warmup"))
            try:
                while True:
                    state = self._run_once(directory)
                    if state.kind == "stopped":
                        return state
                    time.sleep(poll_seconds)
            except KeyboardInterrupt:
                request_stop(directory)
                return self._run_once(directory)


__all__ = ["FinalizedSpotFeed", "LivePaperSignalRunner", "read_live_signal_status", "request_stop"]
