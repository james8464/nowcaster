from __future__ import annotations

import asyncio
import io
import json
import selectors
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.database.engine import Database
from src.live_monitor import command
from src.live_monitor.command import MonitorBootstrap
from src.live_monitor.providers import ProviderSymbolMetadata
from src.live_monitor.types import MarketQuote


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


def test_shutdown_discards_an_event_already_waiting_in_the_transport_queue(monkeypatch, capsys) -> None:
    bootstrap = MonitorBootstrap(
        schema_version=1,
        session_id="stop-race",
        database_url="duckdb:///:memory:",
        crypto=("BTCUSDT",),
        config_hash="c" * 64,
        cohort_hash="0" * 64,
    )
    monkeypatch.setattr(
        command,
        "load_binance_symbol_metadata",
        lambda _symbols: {"BTCUSDT": ProviderSymbolMetadata("BTCUSDT", Decimal("0.01"), True, False, False)},
    )
    closed = []

    async def stopping_stream(_bootstrap, _metadata, stop_event, **_kwargs):
        try:
            stop_event.set()
            now = datetime.now(UTC)
            yield MarketQuote(
                provider="binance",
                feed="spot",
                symbol="BTCUSDT",
                bid=Decimal("100"),
                ask=Decimal("100.01"),
                last=Decimal("100"),
                tick_size=Decimal("0.01"),
                provider_time=now,
                received_at=now,
            )
        finally:
            closed.append(True)

    monkeypatch.setattr(command, "_merged_live_events", stopping_stream)
    asyncio.run(command.run_live(bootstrap))

    assert closed == [True]
    assert [json.loads(line)["event_type"] for line in capsys.readouterr().out.splitlines()] == ["ready"]


@pytest.mark.parametrize("failure_scope", ["market", "control"])
def test_monitor_internal_failure_reports_terminal_error_and_exits_with_stdin_open(failure_scope) -> None:
    probe = """
import asyncio
import sys
from datetime import UTC, datetime
from decimal import Decimal
from src.live_monitor import command
from src.live_monitor.providers import ProviderSymbolMetadata
from src.live_monitor.types import MarketQuote, ProviderHealthEvent
from scripts.live_engine_entry import main

scope = sys.argv[1]
command.load_binance_symbol_metadata = lambda symbols: {
    symbol: ProviderSymbolMetadata(symbol, Decimal('0.01'), True, False, False) for symbol in symbols
}
original_record = command.LiveMonitorRepository.record_market_event
def persist(self, session_id, event):
    if scope == 'market' and isinstance(event, MarketQuote):
        raise RuntimeError('private-failure-context-must-not-escape')
    return original_record(self, session_id, event)
command.LiveMonitorRepository.record_market_event = persist
if scope == 'control':
    def failed_receipt(self, **kwargs):
        raise RuntimeError('private-failure-context-must-not-escape')
    command.LiveMonitorRepository.record_notification_receipt = failed_receipt

async def market_stream(bootstrap, metadata, stop_event, **kwargs):
    now = datetime.now(UTC)
    yield ProviderHealthEvent(provider='binance', feed='spot', status='healthy', reason='subscribed', occurred_at=now)
    await asyncio.sleep(0.02)
    yield MarketQuote(provider='binance', feed='spot', symbol='BTCUSDT', bid=Decimal('100'),
        ask=Decimal('100.01'), last=Decimal('100'), tick_size=Decimal('0.01'), provider_time=now, received_at=now)
    while not stop_event.is_set():
        await asyncio.sleep(0.01)
command._merged_live_events = market_stream
sys.argv = ['nowcaster-engine', 'monitor', 'run']
raise SystemExit(main())
"""
    root = Path(__file__).resolve().parents[2]
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", probe, failure_scope],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output: list[bytes] = []
    try:
        bootstrap = MonitorBootstrap(
            schema_version=1,
            session_id="failure-probe",
            database_url="duckdb:///:memory:",
            crypto=("BTCUSDT",),
            config_hash="c" * 64,
            cohort_hash="0" * 64,
        )
        process.stdin.write((bootstrap.model_dump_json() + "\n").encode())
        process.stdin.flush()
        ready = False
        deadline = time.monotonic() + 90
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(timeout=0.1):
                    continue
                line = process.stdout.readline()
                if not line:
                    break
                output.append(line)
                if json.loads(line).get("event_type") == "ready":
                    ready = True
                    break
        assert ready, "monitor did not reach its isolated ready state"
        if failure_scope == "control":
            process.stdin.write(
                (
                    json.dumps({"schema_version": 1, "command": "notification_delivered", "event_id": "a" * 64}) + "\n"
                ).encode()
            )
            process.stdin.flush()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pytest.fail("failed monitor remained alive while its private input pipe was open")
        output.append(process.stdout.read())
        stderr = process.stderr.read()
        events = [json.loads(line) for line in b"".join(output).splitlines()]
        assert process.returncode != 0
        assert events[-1]["event_type"] == "fatal_error"
        assert events[-1]["payload"]["reason"] == "internal_monitor_failure"
        assert b"private-failure-context-must-not-escape" not in b"".join(output) + stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
