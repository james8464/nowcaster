"""Durable diagnostic accounting: exact inputs, causal activation and isolation."""

import importlib.util
import json
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.research.live_paper_signal_runtime import FinalizedSpotFeed, LivePaperSignalRunner
from src.research.round_two_contracts import RoundObservation
from src.research.round_two_quality import append_observations
from src.research.round_two_registry import register_round
from tests.integration.test_live_paper_signal_runtime import Feed
from tests.unit.test_trader_workflow import NOW, bars, calendar, protocol
from tests.unit.test_trader_workflow_account import quote

SUBDIR = "diagnostic-workflow-v1"


def api():
    assert importlib.util.find_spec("src.research.trader_workflow_runtime") is not None
    from src.research import trader_workflow_runtime

    return trader_workflow_runtime


@pytest.fixture
def registered(tmp_path):
    register_round(protocol(), tmp_path)
    # Prior finalized bars warm features, but cannot themselves generate profits.
    append_observations(
        tmp_path,
        protocol(),
        [
            RoundObservation.model_validate(r.model_dump(include=set(RoundObservation.model_fields)))
            for r in bars()[:-1]
        ],
    )
    (tmp_path / "day-trader-context-observations.jsonl").write_text(
        "".join(r.model_dump_json() + "\n" for r in bars()[:-1])
    )
    return tmp_path


def enabled(directory):
    return api().enable_workflow(directory, now=NOW - timedelta(seconds=2))


def advance(directory, rows, at=NOW, cal=None):
    return api().advance_workflow(directory, rows, calendar() if cal is None else cal, at)


def test_enable_is_explicit_idempotent_and_preserves_protocol(registered):
    before = (registered / "protocol.json").read_bytes()
    assert api().workflow_status(registered, now=NOW)["state"] == "disabled"
    assert not (registered / SUBDIR).exists()
    first = enabled(registered)
    assert first["paperOnly"] is True and first["schemaVersion"] == 1
    assert first["account"]["cash"] == "10000"
    assert api().enable_workflow(registered, now=NOW) == first
    assert (registered / "protocol.json").read_bytes() == before


@pytest.mark.parametrize("name", ["ProspectiveStudies", "live-paper-study"])
def test_protected_paths_refuse_before_writes(tmp_path, name):
    directory = tmp_path / name / "evidence"
    register_round(protocol(), directory)
    with pytest.raises(ValueError, match="protected"):
        enabled(directory)
    assert sorted(p.name for p in directory.iterdir()) == ["protocol.json"]


def test_unregistered_directory_is_not_created(tmp_path):
    directory = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        enabled(directory)
    assert not directory.exists()


def test_recorded_feed_intent_later_entry_paid_exit_and_restart(registered):
    enabled(registered)
    pending = advance(registered, (bars()[-1],))
    assert pending["state"] == "pending_entry" and pending["positions"] == []
    filled = advance(registered, (quote(NOW + timedelta(seconds=1), size=2),), NOW + timedelta(seconds=1))
    assert filled["state"] == "position_open" and filled["account"]["totalEntries"] == 1
    assert filled["account"]["cash"] == "9792.268766290"
    trigger = advance(
        registered, (quote(NOW + timedelta(seconds=2), bid="101", ask="101.01"),), NOW + timedelta(seconds=2)
    )
    assert trigger["state"] == "pending_exit"
    closed = advance(
        registered, (quote(NOW + timedelta(seconds=3), bid="101", ask="101.01"),), NOW + timedelta(seconds=3)
    )
    assert closed["account"]["completedTrades"] == 1
    assert Decimal(closed["account"]["realizedPnl"]) < 0
    assert closed["review"]["losses"] == 1 and closed["positions"] == []
    assert api().workflow_status(registered, now=NOW + timedelta(seconds=3)) == closed
    journal = registered / SUBDIR / "transitions.jsonl"
    before = journal.read_bytes()
    assert (
        advance(registered, (quote(NOW + timedelta(seconds=3), bid="101", ask="101.01"),), NOW + timedelta(seconds=3))
        == closed
    )
    assert journal.read_bytes() == before


def test_preactivation_rows_cannot_create_intent_or_retrospective_fill(registered):
    api().enable_workflow(registered, now=NOW)
    result = advance(registered, bars(), NOW + timedelta(seconds=1))
    assert result["account"]["totalEntries"] == 0
    assert result["state"] == "watching" and result["decisions"] == []


def test_changed_duplicate_is_rejected_without_appending(registered):
    enabled(registered)
    advance(registered, (bars()[-1],))
    path = registered / SUBDIR / "transitions.jsonl"
    before = path.read_bytes()
    changed = bars()[-1].model_copy(update={"bid": Decimal("103.68")})
    with pytest.raises(ValueError, match="duplicate"):
        advance(registered, (changed,), NOW + timedelta(seconds=1))
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["torn", "hash", "replay", "deleted"])
def test_journal_corruption_reports_error_without_empty_success(registered, damage):
    enabled(registered)
    advance(registered, (bars()[-1],))
    path = registered / SUBDIR / "transitions.jsonl"
    if damage == "deleted":
        path.unlink()
    elif damage == "torn":
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
    else:
        from src.strategies.types import canonical_hash, canonical_json

        row = json.loads(path.read_text())
        row["transition"]["account"]["cash"] = "9999"
        if damage == "replay":
            row["hash"] = canonical_hash({k: v for k, v in row.items() if k != "hash"})
        path.write_text(canonical_json(row) + "\n")
    result = api().workflow_status(registered, now=NOW)
    assert result["state"] == "error" and result["account"] is None and result["reasons"]
    with pytest.raises(ValueError):
        advance(registered, (), NOW + timedelta(seconds=1))


def test_regressing_clock_rejected_and_status_expires(registered):
    enabled(registered)
    advance(registered, (bars()[-1],))
    with pytest.raises(ValueError, match="clock"):
        advance(registered, (), NOW - timedelta(seconds=1))
    assert api().workflow_status(registered, now=NOW + timedelta(seconds=16))["state"] == "stale"


@pytest.mark.parametrize("field", ["policyHash", "implementationHash", "protocolHash"])
def test_manifest_mismatch_cannot_resume(registered, field):
    enabled(registered)
    path = registered / SUBDIR / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest[field] = "0" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="identity"):
        enabled(registered)
    assert api().workflow_status(registered, now=NOW)["state"] == "error"


def test_collector_advance_precedes_legacy_gates_and_fresh_quotes_manage(registered):
    enabled(registered)
    (registered / "day-trader-calendar.jsonl").write_text(calendar().model_dump_json() + "\n")
    runner = LivePaperSignalRunner(Feed([bars()[-1]]), clock=lambda: NOW)
    legacy = runner.run_once(registered)
    assert legacy.suggestion is None
    assert api().workflow_status(registered, now=NOW)["state"] == "pending_entry"
    later = quote(NOW + timedelta(seconds=1), size=2).model_copy(update={"source_key": bars()[-1].source_key})
    LivePaperSignalRunner(Feed([later]), clock=lambda: NOW + timedelta(seconds=1)).run_once(registered)
    assert api().workflow_status(registered, now=NOW + timedelta(seconds=1))["account"]["totalEntries"] == 1
    assert len((registered / "day-trader-context-observations.jsonl").read_text().splitlines()) == 60


def test_workflow_error_does_not_break_legacy_collector(registered):
    enabled(registered)
    (registered / SUBDIR / "manifest.json").write_text("{}")
    state = LivePaperSignalRunner(Feed([bars()[-1]]), clock=lambda: NOW).run_once(registered)
    assert state.kind != "failed"
    assert api().workflow_status(registered, now=NOW)["state"] == "error"


def test_cli_enable_status_wire_is_bounded_paper_only(registered):
    script = Path(__file__).resolve().parents[2] / "scripts/run_live_paper_signals.py"
    for command in ("workflow-enable", "workflow-status"):
        result = subprocess.run(
            [sys.executable, str(script), command, "--directory", str(registered)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        state = json.loads(result.stdout)
        assert state["paperOnly"] is True and state["schemaVersion"] == 1
        assert set(state) == {
            "schemaVersion",
            "paperOnly",
            "protocolHash",
            "policyHash",
            "updatedAt",
            "state",
            "reasons",
            "decisions",
            "account",
            "positions",
            "recentTrades",
            "review",
        }
        assert len(result.stdout) < 100000


def test_complete_tail_deletion_is_detected_by_checkpoint(registered):
    enabled(registered)
    advance(registered, (bars()[-1],))
    path = registered / SUBDIR / "transitions.jsonl"
    path.write_text("")
    assert api().workflow_status(registered, now=NOW)["state"] == "error"


def test_actual_implementation_change_blocks_resume(registered, monkeypatch):
    enabled(registered)
    monkeypatch.setattr(api(), "_implementation_hash", lambda: "0" * 64)
    with pytest.raises(ValueError, match="identity"):
        enabled(registered)


def test_refreshed_quote_reusing_key_with_changed_input_surfaces_separate_error(registered):
    enabled(registered)
    (registered / "day-trader-calendar.jsonl").write_text(calendar().model_dump_json() + "\n")
    LivePaperSignalRunner(Feed([bars()[-1]]), clock=lambda: NOW).run_once(registered)
    later = quote(NOW + timedelta(seconds=1), size=2).model_copy(update={"source_key": bars()[-1].source_key})
    LivePaperSignalRunner(Feed([later]), clock=lambda: NOW + timedelta(seconds=1)).run_once(registered)
    changed = later.model_copy(update={"bid": Decimal("103.68")})
    legacy = LivePaperSignalRunner(Feed([changed]), clock=lambda: NOW + timedelta(seconds=2)).run_once(registered)
    assert legacy.kind != "failed"
    state = api().workflow_status(registered, now=NOW + timedelta(seconds=2))
    assert state["state"] == "error" and state["account"] is None


def test_symlinked_workflow_directory_cannot_write_into_old_evidence(registered, tmp_path):
    target = tmp_path / "old-evidence"
    target.mkdir()
    (registered / SUBDIR).symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        enabled(registered)
    assert list(target.iterdir()) == []


def test_missing_calendar_does_not_block_pending_risk_exit(registered):
    enabled(registered)
    advance(registered, (bars()[-1],))
    advance(registered, (quote(NOW + timedelta(seconds=1), size=2),), NOW + timedelta(seconds=1))
    api().advance_workflow(
        registered, (quote(NOW + timedelta(seconds=2), bid="101", ask="101.01"),), None, NOW + timedelta(seconds=2)
    )
    state = api().advance_workflow(
        registered, (quote(NOW + timedelta(seconds=3), bid="101", ask="101.01"),), None, NOW + timedelta(seconds=3)
    )
    assert state["account"]["completedTrades"] == 1 and state["review"]["losses"] == 1


def test_status_does_not_write_or_resurrect_account_from_projection(registered):
    enabled(registered)
    advance(registered, (bars()[-1],))
    files = {p.name: p.read_bytes() for p in (registered / SUBDIR).iterdir()}
    api().workflow_status(registered, now=NOW)
    assert {p.name: p.read_bytes() for p in (registered / SUBDIR).iterdir()} == files


def test_empty_poll_heartbeat_cannot_refresh_stale_source(registered):
    enabled(registered)
    advance(registered, (bars()[-1],))
    result = advance(registered, (), NOW + timedelta(seconds=20))
    assert result["state"] == "stale"
    assert result["updatedAt"] == "2026-09-22T13:00:01Z"


def test_management_uses_fresh_quotes_after_legacy_candle_freshness_expires(registered):
    enabled(registered)
    (registered / "day-trader-calendar.jsonl").write_text(calendar().model_dump_json() + "\n")
    LivePaperSignalRunner(Feed([bars()[-1]]), clock=lambda: NOW).run_once(registered)
    for second, bid in [(1, "103.69"), (30, "101"), (31, "101")]:
        at = NOW + timedelta(seconds=second)
        row = quote(at, size=2, bid=bid, ask=str(Decimal(bid) + Decimal(".02"))).model_copy(
            update={"source_key": bars()[-1].source_key}
        )
        legacy = LivePaperSignalRunner(Feed([row]), clock=lambda at=at: at).run_once(registered)
        assert legacy.suggestion is None
    state = api().workflow_status(registered, now=NOW + timedelta(seconds=31))
    assert state["account"]["completedTrades"] == 1
    assert state["state"] != "stale"


def test_public_feed_refreshes_old_closed_candle_quote_only_when_enabled():
    at = NOW + timedelta(seconds=30)
    opened = int((NOW.replace(second=0) - timedelta(minutes=1)).timestamp() * 1000)
    candle = [opened, "100", "102", "99", "101", "1000", opened + 59999, "0", 1, "0", "0", "0"]

    def fetch(path, params):
        return {"serverTime": int(at.timestamp() * 1000)} if path == "/api/v3/time" else [candle]

    current = dict(e="24hrTicker", E=int(at.timestamp() * 1000), s="BTCUSDT", b="100", a="101", B="10", A="20")
    feed = FinalizedSpotFeed(fetch_json=fetch, fetch_quote=lambda symbol: current, clock=lambda: at)
    assert feed.observations(("BTCUSDT",)) == ()
    feed.collect_workflow_quotes = True
    rows = feed.observations(("BTCUSDT",))
    assert len(rows) == 1 and rows[0].quote_provider_at == at
    assert rows[0].provider_at == NOW.replace(second=0)
