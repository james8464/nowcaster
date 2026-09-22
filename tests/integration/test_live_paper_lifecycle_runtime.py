"""Live publication must produce durable, causally advanced hypothetical history."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.research import live_paper_signal_runtime as runtime
from src.research.day_trader_context import CalendarSnapshot, ContextObservation
from src.research.day_trader_lifecycle import LifecycleLedger, PaperLifecycle
from src.research.live_paper_signals import SignalEventLedger
from src.research.round_two_contracts import ResearchRoundProtocol, RoundObservation
from src.research.round_two_quality import append_observations
from src.research.round_two_registry import register_round
from src.research.trend_advisor import TrendAdvisorSuggestion

NOW = datetime(2026, 9, 22, 12, 0, 2, tzinfo=UTC)


@pytest.fixture
def live(tmp_path, monkeypatch):
    protocol = ResearchRoundProtocol.default(round_id="lifecycle-runtime", starts_at=NOW - timedelta(days=200))
    protocol = protocol.model_copy(update={"warmup_minutes": 1}).validated()
    register_round(protocol, tmp_path)

    class Feed:
        at = NOW - timedelta(minutes=1)
        empty = False
        missing_book = False
        close_override = None

        def row(self, symbol, at):
            price = self.close_override or Decimal("101") + Decimal(str((at - NOW).total_seconds() / 60)) / 100
            return ContextObservation(
                provider="binance",
                feed="spot",
                symbol=symbol,
                provider_at=at.replace(second=0),
                received_at=at,
                available_at=at,
                source_key=f"{symbol}:{at.replace(second=0).isoformat()}",
                open=price,
                close=price,
                high=price + Decimal("0.02"),
                low=price - Decimal("0.02"),
                volume="1000",
                bid=price - Decimal("0.01"),
                ask=price + Decimal("0.01"),
                bid_size=3,
                ask_size=1,
                quote_provider_at=None if self.missing_book else at.replace(second=0),
                quote_received_at=at,
                quote_available_at=at,
                quote_source_key=f"quote:{symbol}:{at}",
            )

        def observations(self, symbols):
            return () if self.empty else tuple(self.row(symbol, self.at) for symbol in symbols)

    feed = Feed()
    history = [feed.row(symbol, NOW + timedelta(minutes=m)) for m in range(-59, -1) for symbol in protocol.symbols]
    append_observations(
        tmp_path,
        protocol,
        [
            RoundObservation.model_validate(row.model_dump(include=set(RoundObservation.model_fields)))
            for row in history
        ],
    )
    calendar = CalendarSnapshot(
        source="retained-test-calendar",
        revision="v1",
        published_at=NOW - timedelta(hours=2),
        available_at=NOW - timedelta(hours=2),
        valid_until=NOW + timedelta(hours=2),
        coverage_starts_at=NOW - timedelta(hours=2),
        coverage_ends_at=NOW + timedelta(hours=2),
        events=(),
    )
    (tmp_path / "day-trader-calendar.jsonl").write_text(calendar.model_dump_json() + "\n")
    assert runtime.LivePaperSignalRunner(feed, clock=lambda: feed.at).run_once(tmp_path).kind == "warming"
    template = json.loads((tmp_path / "research-round-2-summary.json").read_text())["trendAdvisor"][0]

    def advice(protocol, result, quality, observations, *, decision_at, registry):
        fields = TrendAdvisorSuggestion.model_validate(template).model_dump()
        fields.update(
            symbol=result.candidate.symbol,
            posture="long_research",
            decision_at=decision_at,
            available_at=decision_at,
            expires_at=decision_at + timedelta(seconds=15),
            entry_low="100",
            entry_high="101",
            invalidation="99",
            target="103",
            reasons=("trend_aligned", "candidate_confirmed"),
        )
        return TrendAdvisorSuggestion.model_validate(fields)

    monkeypatch.setattr(runtime, "advise", advice)
    feed.at = NOW
    return tmp_path, protocol, feed


def poll(live):
    path, _, feed = live
    return runtime.LivePaperSignalRunner(feed, clock=lambda: feed.at).run_once(path)


def records(live):
    path, protocol, _ = live
    return LifecycleLedger(path, protocol_hash=protocol.identity_hash).events()


def test_publication_creates_one_origin_and_restart_never_duplicates_it(live):
    state = poll(live)
    assert state.kind == "published"
    retained = records(live)
    assert len(retained) == 1
    assert retained[0].origin_report.suggestion == state.suggestion
    assert retained[0].created_at == NOW
    assert retained[0].revision == 0
    poll(live)
    assert records(live) == retained


def test_nonfinal_retained_bar_cannot_close_a_lifecycle(live):
    path, protocol, feed = live
    assert poll(live).kind == "published"
    before = records(live)
    feed.at = NOW + timedelta(minutes=1)
    feed.close_override = Decimal("104")
    enriched = feed.row("BTCUSDT", feed.at).model_copy(update={"finalized": False})
    append_observations(
        path,
        protocol,
        [RoundObservation.model_validate(enriched.model_dump(include=set(RoundObservation.model_fields)))],
    )
    with (path / runtime.CONTEXT_OBSERVATIONS_FILE).open("a") as handle:
        handle.write(enriched.model_dump_json() + "\n")
    with pytest.raises(ValueError, match="final"):
        runtime._advance_retained_lifecycles(path, protocol, feed.at)
    assert records(live) == before


@pytest.mark.parametrize("invalid", ["book", "calendar", "stale"])
def test_ineligible_or_expired_publication_cannot_create_a_lifecycle(live, invalid):
    path, _, feed = live
    if invalid == "book":
        feed.missing_book = True
    elif invalid == "calendar":
        (path / "day-trader-calendar.jsonl").unlink()
    else:
        times = iter((NOW, NOW, NOW + timedelta(seconds=16)))
        state = runtime.LivePaperSignalRunner(feed, clock=lambda: next(times)).run_once(path)
        assert state.kind == "stale"
        assert records(live) == ()
        return
    assert poll(live).suggestion is None
    assert records(live) == ()


def test_later_retained_bars_advance_chain_and_cli_exposes_completed_history(live):
    path, _, feed = live
    assert poll(live).kind == "published"
    origin = records(live)[0]
    feed.at = NOW + timedelta(minutes=1)
    poll(live)
    chain = [x for x in records(live) if x.lifecycle_hash == origin.lifecycle_hash]
    assert [x.revision for x in chain] == [0, 1]
    assert chain[-1].completed_at is None
    assert chain[-1].last_observation.bar.provider_at == NOW.replace(second=0) + timedelta(minutes=1)
    feed.at = NOW + timedelta(minutes=2)
    feed.close_override = Decimal("104")
    poll(live)
    chain = [x for x in records(live) if x.lifecycle_hash == origin.lifecycle_hash]
    assert [x.revision for x in chain] == [0, 1, 2]
    assert chain[-1].exit_reason == "target"
    assert chain[-1].completed_at == feed.at
    assert chain[-1].previous_record_hash == chain[-2].record_hash
    before = (path / "paper-lifecycles.jsonl").read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[2] / "scripts/run_live_paper_signals.py"),
            "decision-context",
            "--directory",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    outcomes = json.loads(result.stdout)["outcomes"]
    assert any(x["lifecycle_hash"] == origin.lifecycle_hash and x["exit_reason"] == "target" for x in outcomes)
    assert (path / "paper-lifecycles.jsonl").read_bytes() == before
    poll(live)
    assert (path / "paper-lifecycles.jsonl").read_bytes() == before


@pytest.mark.parametrize("empty", [False, True])
def test_missing_minute_expires_open_lifecycle_even_during_collection_warmup(live, empty):
    _, _, feed = live
    poll(live)
    origin = records(live)[0]
    feed.at = NOW + timedelta(minutes=2)
    feed.empty = empty
    feed.close_override = Decimal("104")
    poll(live)
    chain = [x for x in records(live) if x.lifecycle_hash == origin.lifecycle_hash]
    assert [x.revision for x in chain] == [0, 1]
    assert chain[-1].exit_reason == "expired"
    assert chain[-1].completed_at == feed.at


def test_restart_recovers_retained_observation_after_interrupted_revision_write(live, monkeypatch):
    _, _, feed = live
    poll(live)
    origin = records(live)[0]
    original_append = LifecycleLedger.append

    def fail_revision_once(self, item):
        if item.lifecycle_hash == origin.lifecycle_hash and item.revision == 1:
            raise OSError("simulated unavailable lifecycle writer")
        original_append(self, item)

    monkeypatch.setattr(LifecycleLedger, "append", fail_revision_once)
    feed.at = NOW + timedelta(minutes=1)
    assert poll(live).kind == "failed"
    assert [x.revision for x in records(live) if x.lifecycle_hash == origin.lifecycle_hash] == [0]
    monkeypatch.setattr(LifecycleLedger, "append", original_append)
    poll(live)
    chain = [x for x in records(live) if x.lifecycle_hash == origin.lifecycle_hash]
    assert [x.revision for x in chain] == [0, 1]
    assert chain[-1].completed_at is None


def test_corrupt_lifecycle_history_blocks_new_publication_without_rewriting(live):
    path, _, feed = live
    poll(live)
    history = path / "paper-lifecycles.jsonl"
    corrupt = history.read_bytes() + b'{"torn":'
    history.write_bytes(corrupt)
    feed.at = NOW + timedelta(minutes=1)
    state = poll(live)
    assert state.kind == "failed"
    assert state.suggestion is None
    assert history.read_bytes() == corrupt


@pytest.mark.parametrize("write", ["journal", "event", "lifecycle", "state"])
@pytest.mark.parametrize("after_write", [False, True])
@pytest.mark.parametrize("restart_delay", [0, 30])
def test_publication_transaction_recovers_every_write_boundary(live, monkeypatch, write, after_write, restart_delay):
    """A crash must neither orphan publication nor invent a fresh decision on replay."""
    path, protocol, feed = live

    class Crash(BaseException):
        pass

    def interrupt(original, predicate):
        def wrapped(*args, **kwargs):
            if predicate(*args, **kwargs):
                if after_write:
                    original(*args, **kwargs)
                raise Crash()
            return original(*args, **kwargs)

        return wrapped

    with monkeypatch.context() as patch:
        if write == "journal":
            patch.setattr(
                runtime,
                "append_jsonl_fsync",
                interrupt(
                    runtime.append_jsonl_fsync, lambda path, *args, **kwargs: path.name == "paper-publications.jsonl"
                ),
            )
        elif write == "event":
            patch.setattr(
                SignalEventLedger,
                "append",
                interrupt(SignalEventLedger.append, lambda self, event: event.kind == "published"),
            )
        elif write == "lifecycle":
            patch.setattr(
                LifecycleLedger, "append", interrupt(LifecycleLedger.append, lambda self, item: item.revision == 0)
            )
        else:
            patch.setattr(
                runtime,
                "_write_atomic_json",
                interrupt(
                    runtime._write_atomic_json,
                    lambda path, payload: path.name == runtime.STATE_FILE and payload["kind"] == "published",
                ),
            )
        with pytest.raises(Crash):
            poll(live)

    before_decisions = (path / "trend-advisor-decisions.jsonl").read_bytes()
    before_context = (path / "day-trader-context-reports.jsonl").read_bytes()
    feed.empty = True
    feed.at = NOW + timedelta(seconds=restart_delay)
    state = poll(live)
    published = [
        event
        for event in SignalEventLedger(path, protocol_hash=protocol.identity_hash).events()
        if event.kind == "published"
    ]
    origins = [item for item in records(live) if item.revision == 0]
    committed = write != "journal" or after_write
    assert len(published) == len(origins) == int(committed)
    if committed:
        assert origins[0].created_at == published[0].at == NOW
        assert published[0].detail == "lifecycle:" + origins[0].lifecycle_hash
        if restart_delay == 0:
            assert state.kind == "published"
            assert state.suggestion == origins[0].origin_report.suggestion
        else:
            assert state.suggestion is None
    assert (path / "trend-advisor-decisions.jsonl").read_bytes() == before_decisions
    assert (path / "day-trader-context-reports.jsonl").read_bytes() == before_context
    events_before = (path / "signal-events.jsonl").read_bytes()
    lifecycles_before = (path / "paper-lifecycles.jsonl").read_bytes() if committed else None
    poll(live)
    assert (path / "signal-events.jsonl").read_bytes() == events_before
    if committed:
        assert (path / "paper-lifecycles.jsonl").read_bytes() == lifecycles_before


@pytest.mark.parametrize("damage", ["missing", "torn", "different_origin"])
def test_recovery_rejects_missing_or_substituted_publication_identity(live, damage):
    path, _, feed = live
    poll(live)
    publication = path / "paper-publications.jsonl"
    original = records(live)[0]
    if damage == "missing":
        publication.unlink()
    elif damage == "torn":
        publication.write_bytes(publication.read_bytes()[:-1])
    else:
        alternate = PaperLifecycle.from_report(original.origin_report, created_at=NOW + timedelta(seconds=1))
        publication.write_text(alternate.model_dump_json() + "\n")
    before = (path / "paper-lifecycles.jsonl").read_bytes()
    events_before = (path / "signal-events.jsonl").read_bytes()
    feed.at = NOW + timedelta(seconds=1)
    feed.empty = True
    state = poll(live)
    assert state.kind == "failed"
    assert state.suggestion is None
    assert (path / "paper-lifecycles.jsonl").read_bytes() == before
    assert (path / "signal-events.jsonl").read_bytes() == events_before


@pytest.mark.parametrize(
    "damage",
    [
        "journal_missing",
        "journal_torn",
        "journal_corrupt",
        "journal_mismatch",
        "event_missing",
        "event_mismatch",
        "event_duplicate",
        "event_manifest_missing",
        "lifecycle_missing",
        "lifecycle_torn",
        "lifecycle_mismatch",
        "lifecycle_manifest_missing",
    ],
)
def test_status_and_cli_reject_damaged_publication_read_only(live, monkeypatch, capsys, damage):
    """A current-looking state cannot bypass absent or inconsistent durable evidence."""
    from scripts.run_live_paper_signals import main

    path, _, _ = live
    assert poll(live).kind == "published"
    journal = path / "paper-publications.jsonl"
    events = path / "signal-events.jsonl"
    lifecycles = path / "paper-lifecycles.jsonl"
    if damage == "journal_missing":
        journal.unlink()
    elif damage == "journal_torn":
        journal.write_bytes(journal.read_bytes()[:-1])
    elif damage == "journal_corrupt":
        journal.write_bytes(journal.read_bytes() + b"{}\n")
    elif damage == "journal_mismatch":
        journal.write_bytes(b"")
    elif damage in {"event_missing", "event_mismatch", "event_duplicate"}:
        rows = [json.loads(line) for line in events.read_text().splitlines()]
        if damage == "event_missing":
            rows = [row for row in rows if row["kind"] != "published"]
        elif damage == "event_mismatch":
            next(row for row in rows if row["kind"] == "published")["candidate_hash"] = "0" * 64
        else:
            rows.append(next(row for row in rows if row["kind"] == "published"))
        events.write_text("".join(json.dumps(row) + "\n" for row in rows))
    elif damage == "event_manifest_missing":
        (path / "signal-events-manifest.json").unlink()
    elif damage == "lifecycle_missing":
        lifecycles.unlink()
    elif damage == "lifecycle_torn":
        lifecycles.write_bytes(lifecycles.read_bytes()[:-1])
    elif damage == "lifecycle_mismatch":
        initial = records(live)[0]
        alternate = PaperLifecycle.from_report(initial.origin_report, created_at=NOW + timedelta(seconds=1))
        lifecycles.write_text(alternate.model_dump_json() + "\n")
    else:
        (path / "paper-lifecycles-manifest.json").unlink()
    # Removing lock files also detects accidental use of mutating ledger constructors.
    for lock in path.glob("*.lock"):
        lock.unlink()
    before = {item.name: (item.read_bytes(), item.stat().st_mtime_ns) for item in path.iterdir() if item.is_file()}
    state = runtime.read_live_signal_status(path, now=NOW)
    assert state.kind == "abstaining"
    assert state.suggestion is None
    assert "publication_evidence_unavailable" in state.reasons
    monkeypatch.setattr(runtime, "_now", lambda: NOW)
    assert main(["status", "--directory", str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "abstaining"
    assert payload["suggestion"] is None
    assert before == {
        item.name: (item.read_bytes(), item.stat().st_mtime_ns) for item in path.iterdir() if item.is_file()
    }


def test_status_and_cli_read_healthy_publication_without_creating_files(live, monkeypatch, capsys):
    from scripts.run_live_paper_signals import main

    path, _, _ = live
    original = poll(live)
    for lock in path.glob("*.lock"):
        lock.unlink()
    before = {item.name: (item.read_bytes(), item.stat().st_mtime_ns) for item in path.iterdir() if item.is_file()}
    assert runtime.read_live_signal_status(path, now=NOW) == original
    monkeypatch.setattr(runtime, "_now", lambda: NOW)
    assert main(["status", "--directory", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "published"
    assert before == {
        item.name: (item.read_bytes(), item.stat().st_mtime_ns) for item in path.iterdir() if item.is_file()
    }
