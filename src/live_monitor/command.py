from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing, suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, TextIO

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from src.config.settings import Settings
from src.database.engine import Database
from src.live_monitor.control_input import control_lines
from src.live_monitor.engine import LiveMonitorEngine
from src.live_monitor.evidence import (
    SealedCohortResolver,
    load_active_readiness_receipt,
    load_contextual_live_evidence,
    load_decision_history,
    load_sealed_cohorts,
    select_monitor_cohorts,
    selected_cohort_hash,
)
from src.live_monitor.providers import (
    AlpacaMarketDataAdapter,
    BinanceSpotAdapter,
    ProviderSymbolMetadata,
    expected_repair_starts,
    load_alpaca_repair_bars,
    load_alpaca_symbol_metadata,
    load_binance_depth_snapshot,
    load_binance_repair_bars,
    load_binance_symbol_metadata,
)
from src.live_monitor.readiness import invalidate_readiness_for_drift
from src.live_monitor.repository import LiveMonitorRepository
from src.live_monitor.timing import CausalClock, prepare_market_event
from src.live_monitor.types import (
    BarIntervalValue,
    MarketBar,
    MarketEvent,
    MarketStatusEvent,
    MonitorHealth,
    MonitorWireEvent,
    ProviderHealthEvent,
)
from src.models.drift import DEFAULT_DRIFT_POLICY_HASH


class MonitorRuntimeError(RuntimeError):
    """Safe terminal boundary; internal exception details never enter the wire protocol."""


class MonitorBootstrap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    session_id: str = Field(min_length=1, max_length=128)
    database_url: str = Field(min_length=1, max_length=2_048)
    stock_feed: Literal["iex", "sip"] = "iex"
    stocks: tuple[str, ...] = ()
    crypto: tuple[str, ...] = ()
    decision_interval: BarIntervalValue = "5m"
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cohort_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    alpaca_key_id: SecretStr | None = None
    alpaca_secret: SecretStr | None = None

    @field_validator("stocks", "crypto")
    @classmethod
    def bounded_watchlist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(item.strip().upper() for item in value if item.strip()))
        if len(normalized) > 200 or any(len(item) > 32 for item in normalized):
            raise ValueError("watchlist exceeds the supported symbol limit")
        return normalized


class MonitorControl(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    command: Literal["shutdown", "notification_delivered", "track_fill"]
    event_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    setup_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    actual_fill: Decimal | None = Field(default=None, gt=0)


def parse_control(line: str) -> MonitorControl:
    if len(line.encode()) > 4 * 1024:
        raise ValueError("monitor control exceeds maximum size")
    try:
        control = MonitorControl.model_validate_json(line)
    except ValidationError as error:
        raise ValueError("monitor control is invalid") from error
    if control.command == "notification_delivered" and control.event_id is None:
        raise ValueError("notification receipt requires an event identity")
    if control.command == "track_fill" and (control.setup_id is None or control.actual_fill is None):
        raise ValueError("fill tracking requires a setup identity and price")
    return control


def parse_bootstrap(line: str) -> MonitorBootstrap:
    if len(line.encode()) > 64 * 1024:
        raise ValueError("bootstrap exceeds maximum size")
    try:
        return MonitorBootstrap.model_validate_json(line)
    except ValidationError as error:
        raise ValueError("monitor bootstrap is invalid") from error


def _emit(event: MonitorWireEvent) -> str:
    return event.model_dump_json()


def _transport_health_after(current: bool, event: MarketEvent) -> bool:
    if not isinstance(event, ProviderHealthEvent):
        return True
    if event.status is MonitorHealth.HEALTHY:
        return True
    if event.status in {MonitorHealth.RECONNECTING, MonitorHealth.STALE, MonitorHealth.FAILED, MonitorHealth.STOPPED}:
        return False
    return current


def replay_events(
    bootstrap: MonitorBootstrap,
    *,
    replay: Path,
    provider: Literal["alpaca", "binance"],
) -> tuple[MonitorWireEvent, ...]:
    engine = LiveMonitorEngine(session_id=bootstrap.session_id, decision_interval=bootstrap.decision_interval)
    replay_time = datetime(2026, 8, 26, 14, 1, 2, tzinfo=UTC)
    result = [engine.emit("ready", {"status": "replay"}, emitted_at=replay_time)]
    if provider == "alpaca":
        if bootstrap.alpaca_key_id is None or bootstrap.alpaca_secret is None:
            raise ValueError("Alpaca credentials are required")
        adapter = AlpacaMarketDataAdapter(
            feed=bootstrap.stock_feed,
            key_id=bootstrap.alpaca_key_id.get_secret_value(),
            secret=bootstrap.alpaca_secret.get_secret_value(),
        )
    else:
        adapter = BinanceSpotAdapter()
    for line in replay.read_text(encoding="utf-8").splitlines():
        for market_event in adapter.decode(line, received_at=replay_time):
            result.extend(engine.accept_market_event(market_event))
    return tuple(result)


async def _merged_live_events(
    bootstrap: MonitorBootstrap,
    metadata: dict[tuple[str, str], ProviderSymbolMetadata],
    stop_event: asyncio.Event | None = None,
    initial_last_bar_end: dict[tuple[str, str, str], datetime] | None = None,
    capture_verified_depth: bool = False,
    clock: CausalClock | None = None,
) -> AsyncIterator[MarketEvent]:
    runtime_clock = clock or CausalClock()
    queue: asyncio.Queue[MarketEvent] = asyncio.Queue(maxsize=1_024)
    last_seen: dict[tuple[str, str], datetime] = {}
    transport_healthy: dict[tuple[str, str], bool] = {}
    last_bar_end = dict(initial_last_bar_end or {})
    continuity_blocked: set[tuple[str, str, str]] = set()
    stale_reported: set[tuple[str, str]] = set()

    async def produce(stream: AsyncIterator[MarketEvent]) -> None:
        async for event in stream:
            if not isinstance(event, ProviderHealthEvent):
                provider_scope = (event.provider, event.feed)
                last_seen[provider_scope] = runtime_clock.now()
                transport_healthy[provider_scope] = _transport_health_after(
                    transport_healthy.get(provider_scope, False), event
                )
                stale_reported.discard(provider_scope)
            else:
                provider_scope = (event.provider, event.feed)
                transport_healthy[provider_scope] = _transport_health_after(
                    transport_healthy.get(provider_scope, False), event
                )
                if event.status == "healthy" and any(
                    scope[:2] == (event.provider, event.feed) for scope in continuity_blocked
                ):
                    continue
            if isinstance(event, MarketBar):
                scope = (event.provider, event.feed, event.symbol)
                previous_end = last_bar_end.get(scope)
                try:
                    expected_missing = (
                        expected_repair_starts(event.provider, event.feed, previous_end, event.start)
                        if previous_end is not None and event.start > previous_end
                        else ()
                    )
                except ValueError:
                    continuity_blocked.add(scope)
                    await queue.put(
                        ProviderHealthEvent(
                            provider=event.provider,
                            feed=event.feed,
                            status="stale",
                            reason="gap_repair_window_exceeded",
                            occurred_at=runtime_clock.now(),
                        )
                    )
                    continue
                if expected_missing:
                    now = runtime_clock.now()
                    await queue.put(
                        ProviderHealthEvent(
                            provider=event.provider,
                            feed=event.feed,
                            status="reconnecting",
                            reason="gap_repair_started",
                            occurred_at=now,
                        )
                    )
                    try:
                        if event.provider == "alpaca":
                            if bootstrap.alpaca_key_id is None or bootstrap.alpaca_secret is None:
                                raise ValueError("Alpaca credentials are required for gap repair")
                            repaired = await asyncio.to_thread(
                                load_alpaca_repair_bars,
                                event.symbol,
                                start=previous_end,
                                end=event.start,
                                feed=event.feed,
                                key_id=bootstrap.alpaca_key_id.get_secret_value(),
                                secret=bootstrap.alpaca_secret.get_secret_value(),
                            )
                        else:
                            repaired = await asyncio.to_thread(
                                load_binance_repair_bars,
                                event.symbol,
                                start=previous_end,
                                end=event.start,
                            )
                    except ValueError:
                        continuity_blocked.add(scope)
                        await queue.put(
                            ProviderHealthEvent(
                                provider=event.provider,
                                feed=event.feed,
                                status="stale",
                                reason="gap_repair_failed",
                                occurred_at=runtime_clock.now(),
                            )
                        )
                        # Do not advance or publish this bar. The next finalized bar
                        # retries from the same durable watermark and health stays stale.
                        continue
                    else:
                        for repaired_bar in repaired:
                            await queue.put(repaired_bar)
                        continuity_blocked.discard(scope)
                        last_bar_end[scope] = event.end
                        await queue.put(
                            ProviderHealthEvent(
                                provider=event.provider,
                                feed=event.feed,
                                status="healthy",
                                reason="gap_repair_complete_delayed_observation",
                                occurred_at=runtime_clock.now(),
                            )
                        )
                        await queue.put(event)
                        continue
                last_bar_end[scope] = max(event.end, previous_end or event.end)
                if (
                    capture_verified_depth
                    and event.provider == "binance"
                    and event.finalized
                    and not event.repair_verified
                    and (previous_end is None or event.end > previous_end)
                ):
                    # At most one bounded full book per finalized minute/symbol; deltas never stand in for depth.
                    with suppress(ValueError):
                        snapshot = await asyncio.to_thread(load_binance_depth_snapshot, event.symbol)
                        await queue.put(snapshot)
            await queue.put(event)

    tasks: list[asyncio.Task[None]] = []
    if bootstrap.stocks:
        if bootstrap.alpaca_key_id is None or bootstrap.alpaca_secret is None:
            raise ValueError("Alpaca credentials are required for stock monitoring")
        alpaca = AlpacaMarketDataAdapter(
            feed=bootstrap.stock_feed,
            key_id=bootstrap.alpaca_key_id.get_secret_value(),
            secret=bootstrap.alpaca_secret.get_secret_value(),
            metadata={symbol: metadata[("alpaca", symbol)] for symbol in bootstrap.stocks},
        )
        last_seen[("alpaca", bootstrap.stock_feed)] = runtime_clock.now()
        transport_healthy[("alpaca", bootstrap.stock_feed)] = False
        tasks.append(
            asyncio.create_task(
                produce(
                    alpaca.stream(
                        f"wss://stream.data.alpaca.markets/v2/{bootstrap.stock_feed}",
                        bootstrap.stocks,
                    )
                )
            )
        )
    if bootstrap.crypto:
        last_seen[("binance", "spot")] = runtime_clock.now()
        transport_healthy[("binance", "spot")] = False
        tasks.append(
            asyncio.create_task(
                produce(
                    BinanceSpotAdapter(
                        metadata={symbol: metadata[("binance", symbol)] for symbol in bootstrap.crypto}
                    ).stream("wss://stream.binance.com:9443/ws", bootstrap.crypto)
                )
            )
        )
    if not tasks:
        raise ValueError("at least one watchlist symbol is required")

    async def next_queued_event() -> MarketEvent | None:
        queued = asyncio.create_task(queue.get())
        try:
            done, _ = await asyncio.wait(
                (queued, *tasks),
                timeout=5,
                return_when=asyncio.FIRST_COMPLETED,
            )
            completed_producers = tuple(task for task in tasks if task.done())
            for task in completed_producers:
                if task.cancelled():
                    if stop_event is None or not stop_event.is_set():
                        raise RuntimeError("live provider task stopped unexpectedly")
                    continue
                error = task.exception()
                if error is not None:
                    raise error
            if queued in done:
                return queued.result()
            if completed_producers and (stop_event is None or not stop_event.is_set()):
                raise RuntimeError("live provider stream ended unexpectedly")
            return None
        finally:
            if not queued.done():
                queued.cancel()
                await asyncio.gather(queued, return_exceptions=True)

    try:
        last_heartbeat = datetime.min.replace(tzinfo=UTC)
        while stop_event is None or not stop_event.is_set():
            event = await next_queued_event()
            if event is not None:
                yield await prepare_market_event(event, clock=runtime_clock.now)
            now = runtime_clock.now()
            heartbeat_due = now - last_heartbeat >= timedelta(seconds=10)
            for (provider, feed), observed_at in tuple(last_seen.items()):
                if now - observed_at > timedelta(seconds=30) and (provider, feed) not in stale_reported:
                    stale_reported.add((provider, feed))
                    yield ProviderHealthEvent(
                        provider=provider,
                        feed=feed,
                        status="stale",
                        reason="no_recent_market_data",
                        occurred_at=now,
                    )
                if heartbeat_due:
                    blocked = any(scope[:2] == (provider, feed) for scope in continuity_blocked)
                    status = (
                        "stale"
                        if blocked or now - observed_at > timedelta(seconds=30)
                        else "healthy"
                        if transport_healthy.get((provider, feed), False)
                        else "reconnecting"
                    )
                    yield ProviderHealthEvent(
                        provider=provider,
                        feed=feed,
                        status=status,
                        reason="heartbeat",
                        occurred_at=now,
                    )
            if heartbeat_due:
                last_heartbeat = now
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_live(
    bootstrap: MonitorBootstrap,
    *,
    control_stream: TextIO | None = None,
    clock: CausalClock | None = None,
) -> None:
    runtime_clock = clock or CausalClock()
    database = Database.from_url(bootstrap.database_url)
    database.initialize()
    settings = Settings.load(Path.cwd())
    cohorts = load_sealed_cohorts(database, settings.strategies.enabled)
    selected = select_monitor_cohorts(
        cohorts,
        stocks=bootstrap.stocks,
        crypto=bootstrap.crypto,
        interval=bootstrap.decision_interval,
        stock_feed=bootstrap.stock_feed,
    )
    watchlist = set(bootstrap.stocks) | set(bootstrap.crypto)
    provider_feeds = ({("alpaca", bootstrap.stock_feed)} if bootstrap.stocks else set()) | (
        {("binance", "spot")} if bootstrap.crypto else set()
    )
    selected_hash = selected_cohort_hash(selected)
    if bootstrap.cohort_hash != selected_hash:
        raise ValueError("selected cohort identity does not match the native bootstrap")
    readiness = load_active_readiness_receipt(database, cohorts=selected, now=runtime_clock.now())
    if selected and readiness is None:
        raise ValueError("an active unexpired readiness receipt is required")
    metadata: dict[tuple[str, str], ProviderSymbolMetadata] = {}
    if bootstrap.stocks:
        if bootstrap.alpaca_key_id is None or bootstrap.alpaca_secret is None:
            raise ValueError("Alpaca credentials are required for stock monitoring")
        alpaca_metadata = await asyncio.to_thread(
            load_alpaca_symbol_metadata,
            bootstrap.stocks,
            key_id=bootstrap.alpaca_key_id.get_secret_value(),
            secret=bootstrap.alpaca_secret.get_secret_value(),
        )
        metadata.update({("alpaca", symbol): item for symbol, item in alpaca_metadata.items()})
    if bootstrap.crypto:
        binance_metadata = await asyncio.to_thread(load_binance_symbol_metadata, bootstrap.crypto)
        metadata.update({("binance", symbol): item for symbol, item in binance_metadata.items()})
    repository = LiveMonitorRepository(database, clock=runtime_clock.now)
    contextual_live_evidence = load_contextual_live_evidence(
        database,
        selected,
        now=runtime_clock.now(),
    )
    repository.start_session(
        bootstrap.session_id,
        config_hash=bootstrap.config_hash,
        cohort_hash=selected_hash,
    )
    metadata_at = runtime_clock.now()
    for (provider, symbol), item in metadata.items():
        repository.record_market_event(
            bootstrap.session_id,
            MarketStatusEvent(
                provider=provider,
                feed=bootstrap.stock_feed if provider == "alpaca" else "spot",
                symbol=symbol,
                kind="status",
                status="instrument_rules",
                provider_time=metadata_at,
                received_at=metadata_at,
                details={
                    "tradable": item.tradable,
                    "filters": list(item.filters),
                    "shortable": item.shortable,
                    "easy_to_borrow": item.easy_to_borrow,
                },
            ),
        )
    drift_invalidator = None
    if readiness is not None:

        def drift_invalidator(cohort_hash: str, evidence_hash: str, at: datetime) -> None:
            invalidate_readiness_for_drift(
                database,
                cohort_hash=cohort_hash,
                drift_evidence_hash=evidence_hash,
                drift_policy_hash=DEFAULT_DRIFT_POLICY_HASH,
                invalidated_at=at,
            )

    engine = LiveMonitorEngine(
        session_id=bootstrap.session_id,
        config_hash=bootstrap.config_hash,
        decision_interval=bootstrap.decision_interval,
        evidence_resolver=(
            SealedCohortResolver(
                selected,
                asset_metadata={key: (value.shortable, value.easy_to_borrow) for key, value in metadata.items()},
                contextual_evidence=contextual_live_evidence,
                contextual_loader=lambda instant: load_contextual_live_evidence(database, selected, now=instant),
            )
            if selected
            else None
        ),
        persistence=repository,
        processing_clock=runtime_clock.now,
        readiness_cohort_hash=selected_hash if readiness is not None else None,
        readiness_invalidator=drift_invalidator,
        minimum_effective_calibration_observations=Decimal(
            settings.deep_research.minimum_effective_calibration_observations
        ),
        maximum_brier_score=Decimal(str(settings.deep_research.maximum_brier_score)),
        maximum_calibration_error=Decimal(str(settings.deep_research.maximum_calibration_error)),
    )
    for cohort in selected:
        engine.seed_history(load_decision_history(database, cohort))
    recovered_setups = repository.recover_active(
        provider_feeds=provider_feeds,
        symbols=watchlist,
        interval=bootstrap.decision_interval,
        config_hash=bootstrap.config_hash,
        cohort_ids={item.cohort_id for item in selected},
        now=runtime_clock.now(),
    )
    for recovered in recovered_setups:
        engine.restore_setup(recovered.plan, state=recovered.state, actual_fill=recovered.actual_fill)
    stop_event = asyncio.Event()
    control_failure: Exception | None = None

    async def read_controls() -> None:
        if control_stream is None:
            return
        async for line in control_lines(control_stream):
            if stop_event.is_set():
                return
            try:
                control = parse_control(line)
                now = runtime_clock.now()
                if control.command == "shutdown":
                    stop_event.set()
                    return
                if control.command == "notification_delivered" and control.event_id is not None:
                    repository.record_notification_receipt(event_id=control.event_id)
                elif control.setup_id is not None and control.actual_fill is not None:
                    for wire_event in engine.track_setup(control.setup_id, actual_fill=control.actual_fill, at=now):
                        print(_emit(wire_event), flush=True)
                print(
                    _emit(
                        engine.emit(
                            "control_ack",
                            {"command": control.command, "accepted": True},
                            emitted_at=now,
                        )
                    ),
                    flush=True,
                )
            except ValueError:
                print(
                    _emit(
                        engine.emit(
                            "control_ack",
                            {"command": "invalid", "accepted": False},
                            emitted_at=runtime_clock.now(),
                        )
                    ),
                    flush=True,
                )
        stop_event.set()

    async def consume_controls() -> None:
        nonlocal control_failure
        try:
            await read_controls()
        except Exception as error:
            control_failure = error
            stop_event.set()

    control_task = asyncio.create_task(consume_controls())
    failure_reported = False

    def report_failure() -> None:
        nonlocal failure_reported
        if not failure_reported:
            failure_reported = True
            try:
                failure_at = runtime_clock.now()
            except ValueError:
                failure_at = runtime_clock.watermark
            if failure_at is None:
                raise RuntimeError("live clock failed before establishing a causal watermark")
            print(
                _emit(
                    engine.emit(
                        "fatal_error",
                        {"reason": "internal_monitor_failure", "status": "failed"},
                        emitted_at=failure_at,
                    )
                ),
                flush=True,
            )

    try:
        print(
            _emit(
                engine.emit(
                    "ready",
                    {
                        "status": "live",
                        "qualified_cohorts": len(selected),
                        "cohort_hash": selected_hash,
                        "readiness_receipt_id": readiness.receipt_id if readiness is not None else None,
                    },
                    emitted_at=runtime_clock.now(),
                )
            ),
            flush=True,
        )
        for recovered in recovered_setups:
            print(
                _emit(
                    engine.emit(
                        "setup_snapshot",
                        {
                            **recovered.plan.model_dump(mode="json"),
                            "state": recovered.state.value,
                            "actual_fill": str(recovered.actual_fill) if recovered.actual_fill is not None else None,
                        },
                        emitted_at=runtime_clock.now(),
                    )
                ),
                flush=True,
            )
        scopes = {
            *(("alpaca", bootstrap.stock_feed, symbol) for symbol in bootstrap.stocks),
            *(("binance", "spot", symbol) for symbol in bootstrap.crypto),
        }
        watermarks = repository.latest_finalized_ends(scopes)
        async with aclosing(
            _merged_live_events(
                bootstrap,
                metadata,
                stop_event,
                initial_last_bar_end=watermarks,
                capture_verified_depth=True,
                clock=runtime_clock,
            )
        ) as market_events:
            async for event in market_events:
                if control_failure is not None:
                    raise MonitorRuntimeError("monitor control failed")
                if stop_event.is_set():
                    break
                for wire_event in engine.accept_market_event(event):
                    print(_emit(wire_event), flush=True)
    except Exception:
        report_failure()
    finally:
        stop_event.set()
        control_task.cancel()
        await asyncio.gather(control_task, return_exceptions=True)
        if control_failure is not None:
            report_failure()
        try:
            try:
                terminal_at = runtime_clock.now()
            except ValueError:
                terminal_at = runtime_clock.watermark
            if terminal_at is None:
                raise RuntimeError("live clock has no terminal causal watermark")
            repository.finish_session(
                bootstrap.session_id,
                reason="internal_monitor_failure" if failure_reported else "monitor_stopped",
                ended_at=terminal_at,
            )
        except Exception:
            report_failure()
        finally:
            try:
                database.dispose()
            except Exception:
                report_failure()
    if failure_reported:
        raise MonitorRuntimeError("monitor stopped after an internal failure") from None


__all__ = [
    "MonitorBootstrap",
    "MonitorControl",
    "MonitorRuntimeError",
    "parse_bootstrap",
    "parse_control",
    "replay_events",
    "run_live",
]
