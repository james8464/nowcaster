from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from src.config.settings import Settings
from src.database.engine import Database
from src.live_monitor.engine import LiveMonitorEngine
from src.live_monitor.evidence import SealedCohortResolver, load_decision_history, load_sealed_cohorts
from src.live_monitor.providers import AlpacaMarketDataAdapter, BinanceSpotAdapter
from src.live_monitor.repository import LiveMonitorRepository
from src.live_monitor.types import BarIntervalValue, MarketEvent, MonitorWireEvent, ProviderHealthEvent


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


def parse_bootstrap(line: str) -> MonitorBootstrap:
    if len(line.encode()) > 64 * 1024:
        raise ValueError("bootstrap exceeds maximum size")
    try:
        return MonitorBootstrap.model_validate_json(line)
    except ValidationError as error:
        raise ValueError("monitor bootstrap is invalid") from error


def _emit(event: MonitorWireEvent) -> str:
    return event.model_dump_json()


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


async def _merged_live_events(bootstrap: MonitorBootstrap) -> AsyncIterator[MarketEvent]:
    queue: asyncio.Queue[MarketEvent] = asyncio.Queue(maxsize=1_024)
    last_seen: dict[tuple[str, str], datetime] = {}
    stale_reported: set[tuple[str, str]] = set()

    async def produce(stream: AsyncIterator[MarketEvent]) -> None:
        async for event in stream:
            last_seen[(event.provider, event.feed)] = datetime.now(UTC)
            stale_reported.discard((event.provider, event.feed))
            await queue.put(event)

    tasks: list[asyncio.Task[None]] = []
    if bootstrap.stocks:
        if bootstrap.alpaca_key_id is None or bootstrap.alpaca_secret is None:
            raise ValueError("Alpaca credentials are required for stock monitoring")
        alpaca = AlpacaMarketDataAdapter(
            feed=bootstrap.stock_feed,
            key_id=bootstrap.alpaca_key_id.get_secret_value(),
            secret=bootstrap.alpaca_secret.get_secret_value(),
        )
        last_seen[("alpaca", bootstrap.stock_feed)] = datetime.now(UTC)
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
        last_seen[("binance", "spot")] = datetime.now(UTC)
        tasks.append(
            asyncio.create_task(
                produce(BinanceSpotAdapter().stream("wss://stream.binance.com:9443/ws", bootstrap.crypto))
            )
        )
    if not tasks:
        raise ValueError("at least one watchlist symbol is required")
    try:
        while True:
            with suppress(TimeoutError):
                yield await asyncio.wait_for(queue.get(), timeout=5)
            now = datetime.now(UTC)
            for (provider, feed), observed_at in tuple(last_seen.items()):
                if now - observed_at <= timedelta(seconds=30) or (provider, feed) in stale_reported:
                    continue
                stale_reported.add((provider, feed))
                yield ProviderHealthEvent(
                    provider=provider,
                    feed=feed,
                    status="stale",
                    reason="no_recent_market_data",
                    occurred_at=now,
                )
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_live(bootstrap: MonitorBootstrap) -> None:
    database = Database.from_url(bootstrap.database_url)
    database.initialize()
    settings = Settings.load(Path.cwd())
    cohorts = load_sealed_cohorts(database, settings.strategies.enabled)
    watchlist = set(bootstrap.stocks) | set(bootstrap.crypto)
    selected = tuple(
        item for item in cohorts if item.symbol in watchlist and item.interval == bootstrap.decision_interval
    )
    repository = LiveMonitorRepository(database)
    repository.start_session(
        bootstrap.session_id,
        config_hash=bootstrap.config_hash,
        cohort_hash=bootstrap.cohort_hash,
    )
    engine = LiveMonitorEngine(
        session_id=bootstrap.session_id,
        decision_interval=bootstrap.decision_interval,
        evidence_resolver=SealedCohortResolver(selected),
        persistence=repository,
    )
    for cohort in selected:
        engine.seed_history(load_decision_history(database, cohort))
    for recovered in repository.recover_active():
        if recovered.plan.symbol in watchlist and recovered.plan.decision_interval == bootstrap.decision_interval:
            engine.restore_setup(recovered.plan, state=recovered.state)
    try:
        print(
            _emit(
                engine.emit(
                    "ready",
                    {"status": "live", "qualified_cohorts": len(selected)},
                    emitted_at=datetime.now(UTC),
                )
            ),
            flush=True,
        )
        async for event in _merged_live_events(bootstrap):
            for wire_event in engine.accept_market_event(event):
                print(_emit(wire_event), flush=True)
    finally:
        repository.finish_session(bootstrap.session_id, reason="monitor_stopped")
        database.dispose()


__all__ = ["MonitorBootstrap", "parse_bootstrap", "replay_events", "run_live"]
