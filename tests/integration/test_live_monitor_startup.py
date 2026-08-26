from __future__ import annotations

import asyncio
import io
from decimal import Decimal

from src.database.engine import Database
from src.live_monitor import command
from src.live_monitor.command import MonitorBootstrap
from src.live_monitor.providers import ProviderSymbolMetadata


def test_empty_evidence_starts_in_abstention_mode_and_shutdown_finishes_the_session(tmp_path, monkeypatch) -> None:
    path = tmp_path / "monitor.duckdb"
    bootstrap = MonitorBootstrap(
        schema_version=1,
        session_id="empty-evidence-session",
        database_url=f"duckdb:///{path}",
        stocks=(),
        crypto=("BTCUSDT",),
        decision_interval="5m",
        config_hash="c" * 64,
        cohort_hash="0" * 64,
    )

    monkeypatch.setattr(
        command,
        "load_binance_symbol_metadata",
        lambda _symbols: {"BTCUSDT": ProviderSymbolMetadata("BTCUSDT", Decimal("0.01"), True, True, True)},
    )

    async def stopped_stream(_bootstrap, _metadata, stop_event, **_kwargs):
        while not stop_event.is_set():
            await asyncio.sleep(0.001)
        if False:
            yield None

    monkeypatch.setattr(command, "_merged_live_events", stopped_stream)

    asyncio.run(
        command.run_live(
            bootstrap,
            control_stream=io.StringIO('{"schema_version":1,"command":"shutdown"}\n'),
        )
    )

    database = Database.from_url(f"duckdb:///{path}")
    assert (
        database.scalar("select status from monitor_sessions where session_id = 'empty-evidence-session'") == "stopped"
    )
    assert (
        database.scalar("select cohort_hash from monitor_sessions where session_id = 'empty-evidence-session'")
        == "0" * 64
    )
