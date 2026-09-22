"""Causal collection, retention, restart and process-control contracts."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.research.live_paper_signal_runtime import (
    FinalizedSpotFeed,
    LivePaperSignalRunner,
    read_live_signal_status,
    request_stop,
)
from src.research.live_paper_signals import SignalEventLedger
from src.research.round_two_contracts import ResearchRoundProtocol, RoundObservation
from src.research.round_two_quality import load_observations
from src.research.round_two_registry import register_round

NOW = datetime(2026, 9, 21, 12, 0, 2, tzinfo=UTC)


def bar(symbol="BTCUSDT", at=NOW, **changes):
    values = dict(
        provider="binance",
        feed="spot",
        symbol=symbol,
        provider_at=at.replace(second=0),
        received_at=at,
        available_at=at,
        source_key=f"{symbol}:{at.replace(second=0).isoformat()}",
        open="100",
        high="102",
        low="99",
        close="101",
        volume="1000",
        bid="100",
        ask="101",
    )
    values.update(changes)
    return RoundObservation(**values)


class Feed:
    def __init__(self, rows):
        self.rows = rows

    def observations(self, symbols):
        if isinstance(self.rows, Exception):
            raise self.rows
        return tuple(self.rows)


@pytest.fixture
def registered(tmp_path):
    protocol = ResearchRoundProtocol.default(round_id="live-test", starts_at=NOW - timedelta(days=200))
    register_round(protocol, tmp_path)
    return tmp_path


def events(directory):
    protocol = ResearchRoundProtocol.model_validate_json((directory / "protocol.json").read_text())
    return SignalEventLedger(directory, protocol_hash=protocol.identity_hash).events()


def test_ingests_before_evaluation_and_never_repeats_bar_after_restart(registered):
    feed = Feed([bar(), bar("ETHUSDT")])
    state = LivePaperSignalRunner(feed, clock=lambda: NOW).run_once(registered)
    assert state.kind == "warming"
    assert all(row.available_at <= state.evaluated_at for row in load_observations(registered))
    assert (registered / "research-round-2-summary.json").exists()
    before = sum(e.kind == "evaluated" for e in events(registered))
    LivePaperSignalRunner(feed, clock=lambda: NOW).run_once(registered)
    assert len(load_observations(registered)) == 2
    assert sum(e.kind == "evaluated" for e in events(registered)) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"close": None},
        {"available_at": NOW + timedelta(seconds=1)},
        {"provider_at": NOW - timedelta(minutes=2)},
        {"provider": "other"},
    ],
)
def test_invalid_batch_is_abstention_without_observations_or_publication(registered, changes):
    state = LivePaperSignalRunner(Feed([bar(**changes)]), clock=lambda: NOW).run_once(registered)
    assert state.kind == "abstaining"
    assert not load_observations(registered)
    assert not any(e.kind in {"published", "notification_attempt"} for e in events(registered))


def test_conflicting_finalized_bar_preserves_first_observation(registered):
    feed = Feed([bar(), bar("ETHUSDT")])
    runner = LivePaperSignalRunner(feed, clock=lambda: NOW)
    runner.run_once(registered)
    feed.rows = [bar(close="102")]
    assert runner.run_once(registered).kind == "abstaining"
    assert str(load_observations(registered)[0].close) == "101"


def test_nonfinal_enriched_bar_is_rejected_before_any_retention(registered):
    from src.research.day_trader_context import ContextObservation

    row = ContextObservation(**bar().model_dump(), finalized=False)
    state = LivePaperSignalRunner(Feed([row]), clock=lambda: NOW).run_once(registered)
    assert state.kind == "abstaining"
    assert not load_observations(registered)
    assert not (registered / "day-trader-context-observations.jsonl").exists()


def test_retained_nonfinal_context_cannot_be_projected_as_final(registered):
    from src.research.day_trader_context import ContextObservation
    from src.research.live_paper_signal_runtime import _context_observations

    row = ContextObservation(**bar().model_dump(), finalized=False)
    (registered / "day-trader-context-observations.jsonl").write_text(row.model_dump_json() + "\n")
    with pytest.raises(ValueError, match="final"):
        _context_observations(registered, (bar(),))


def test_transport_failure_then_recovery_records_reconnect_and_warms(registered):
    feed = Feed(OSError("offline"))
    runner = LivePaperSignalRunner(feed, clock=lambda: NOW)
    assert runner.run_once(registered).kind == "failed"
    feed.rows = [bar(), bar("ETHUSDT")]
    state = runner.run_once(registered)
    assert state.kind == "warming"
    assert "reconnect_warmup" in state.reasons
    assert any(e.kind == "reconnect" for e in events(registered))


@pytest.mark.parametrize("gap_during_recovery", [False, True])
def test_empty_poll_and_runner_restart_cannot_erase_interruption_warmup(tmp_path, gap_during_recovery):
    """Recovery must survive state projections and collect a new continuous window."""
    protocol = ResearchRoundProtocol.default(round_id="recovery-test", starts_at=NOW - timedelta(days=200))
    protocol = protocol.model_copy(update={"warmup_minutes": 2}).validated()
    register_round(protocol, tmp_path)
    clock = NOW - timedelta(minutes=3)
    feed = Feed([])

    def poll(rows, at):
        nonlocal clock
        clock = at
        feed.rows = rows
        # Each invocation recreates the runner, as a resumed run-once process does.
        return LivePaperSignalRunner(feed, clock=lambda: clock).run_once(tmp_path)

    def rows(at):
        return [bar(symbol, at, bid="100.99", ask="101.01") for symbol in protocol.symbols]

    for minute in range(-3, 1):
        at = NOW + timedelta(minutes=minute)
        poll(rows(at), at)
    before = sum(event.kind == "evaluated" for event in events(tmp_path))
    assert poll(OSError("offline"), NOW + timedelta(seconds=5)).kind == "failed"
    poll([], NOW + timedelta(seconds=20))
    recovery_at = NOW + timedelta(minutes=1)
    state = poll(rows(recovery_at), recovery_at)
    assert "reconnect_warmup" in state.reasons
    assert state.suggestion is None
    assert state.evaluated_at is None
    assert [event.at for event in events(tmp_path) if event.kind == "reconnect"] == [recovery_at]
    assert sum(event.kind == "evaluated" for event in events(tmp_path)) == before
    at = NOW + timedelta(minutes=2)
    assert "reconnect_warmup" in poll([] if gap_during_recovery else rows(at), at).reasons
    assert sum(event.kind == "evaluated" for event in events(tmp_path)) == before
    at = NOW + timedelta(minutes=3)
    if gap_during_recovery:
        assert "continuity_warmup" in poll(rows(at), at).reasons
        at = NOW + timedelta(minutes=4)
        assert "continuity_warmup" in poll(rows(at), at).reasons
        assert sum(event.kind == "evaluated" for event in events(tmp_path)) == before
        at = NOW + timedelta(minutes=5)
    assert poll(rows(at), at).evaluated_at == at
    assert sum(event.kind == "evaluated" for event in events(tmp_path)) == before + 1
    assert sum(event.kind == "reconnect" for event in events(tmp_path)) == 1


def test_status_expires_stale_data_and_clock_regression_fails_closed(registered):
    runner = LivePaperSignalRunner(Feed([bar(), bar("ETHUSDT")]), clock=lambda: NOW)
    runner.run_once(registered)
    assert read_live_signal_status(registered, now=NOW + timedelta(minutes=1)).kind == "stale"
    earlier = LivePaperSignalRunner(Feed([]), clock=lambda: NOW - timedelta(seconds=1))
    assert earlier.run_once(registered).kind == "failed"


def test_stop_control_retains_history_and_cli_status(registered):
    runner = LivePaperSignalRunner(Feed([bar(), bar("ETHUSDT")]), clock=lambda: NOW)
    runner.run_once(registered)
    before = len(events(registered))
    request_stop(registered)
    assert runner.run_once(registered).kind == "stopped"
    assert len(events(registered)) > before
    command = [
        sys.executable,
        str(Path(__file__).parents[2] / "scripts/run_live_paper_signals.py"),
        "status",
        "--directory",
        str(registered),
    ]
    output = subprocess.run(command, text=True, capture_output=True, check=True)
    assert json.loads(output.stdout)["kind"] == "stopped"


def test_public_adapter_omits_open_candle_and_stamps_actual_receipt():
    opened = int((NOW.replace(second=0) - timedelta(minutes=1)).timestamp() * 1000)
    closed = [opened, "100", "102", "99", "101", "1000", opened + 59999, "0", 1, "0", "0", "0"]
    unclosed = [opened + 60000, "100", "102", "99", "101", "1000", opened + 119999, "0", 1, "0", "0", "0"]

    def fetch(path, params):
        if path == "/api/v3/time":
            return {"serverTime": int(NOW.timestamp() * 1000)}
        if path == "/api/v3/klines":
            return [closed, unclosed]
        return {"symbol": "BTCUSDT", "bidPrice": "100", "askPrice": "101"}

    quote = dict(e="24hrTicker", E=int(NOW.timestamp() * 1000) - 100, s="BTCUSDT", b="100", a="101", B="10", A="20")
    rows = FinalizedSpotFeed(fetch_json=fetch, fetch_quote=lambda symbol: quote, clock=lambda: NOW).observations(
        ("BTCUSDT",)
    )
    assert len(rows) == 1
    assert rows[0].provider_at == NOW.replace(second=0)
    assert rows[0].available_at == NOW
    assert rows[0].close == 101
    assert rows[0].quote_provider_at == NOW - timedelta(milliseconds=100)
    assert rows[0].quote_received_at == NOW
    assert rows[0].bid_size == 10
    assert rows[0].ask_size == 20
    assert "ticker" in rows[0].quote_source_key


@pytest.mark.parametrize(
    "changes",
    [
        {"E": None},
        {"E": True},
        {"E": int(NOW.timestamp() * 1000) + 1},
        {"E": int(NOW.timestamp() * 1000) - 15000},
        {"s": "ETHUSDT"},
        {"e": "bookTicker"},
        {"B": "0"},
        {"B": None},
        {"A": "NaN"},
        {"E": 10**40},
    ],
)
def test_public_quote_rejects_missing_stale_future_or_wrong_provenance(changes):
    opened = int((NOW.replace(second=0) - timedelta(minutes=1)).timestamp() * 1000)
    candle = [opened, "100", "102", "99", "101", "1000", opened + 59999, "0", 1, "0", "0", "0"]

    def fetch(path, params):
        return {"serverTime": int(NOW.timestamp() * 1000)} if path == "/api/v3/time" else [candle]

    quote = dict(e="24hrTicker", E=int(NOW.timestamp() * 1000) - 100, s="BTCUSDT", b="100", a="101", B="10", A="20")
    quote.update(changes)
    with pytest.raises(ValueError):
        FinalizedSpotFeed(fetch_json=fetch, fetch_quote=lambda symbol: quote, clock=lambda: NOW).observations(
            ("BTCUSDT",)
        )


def test_server_clock_prevents_locally_premature_finalization():
    opened = int((NOW.replace(second=0) - timedelta(minutes=1)).timestamp() * 1000)

    def fetch(path, params):
        if path == "/api/v3/time":
            return {"serverTime": opened + 59000}
        if path == "/api/v3/klines":
            return [[opened, "100", "102", "99", "101", "1000", opened + 59999, "0", 1, "0", "0", "0"]]
        return {"symbol": "BTCUSDT", "bidPrice": "100", "askPrice": "101"}

    assert not FinalizedSpotFeed(fetch_json=fetch, clock=lambda: NOW).observations(("BTCUSDT",))


def test_public_quote_transport_verifies_host_with_bundled_roots_and_bounded_frame(monkeypatch):
    import ssl
    from contextlib import contextmanager

    from src.research.live_paper_signal_runtime import _public_quote

    @contextmanager
    def connect(url, **options):
        assert url == "wss://stream.binance.com:9443/ws/btcusdt@ticker"
        assert options["ssl"].verify_mode == ssl.CERT_REQUIRED
        assert options["ssl"].check_hostname
        assert options["ssl"].cert_store_stats()["x509_ca"] > 0
        assert options["max_size"] <= 16384

        class Socket:
            def recv(self, timeout):
                assert 0 < timeout <= 3
                return '{"e":"24hrTicker","E":123,"s":"BTCUSDT","b":"1","a":"2","B":"3","A":"4"}'

        yield Socket()

    monkeypatch.setattr("websockets.sync.client.connect", connect)
    assert _public_quote("BTCUSDT")["E"] == 123


def test_public_rest_transport_verifies_host_with_bundled_roots(monkeypatch):
    import ssl
    from io import BytesIO

    from src.research import live_paper_signal_runtime as runtime

    def open_response(url, *, timeout, context=None):
        assert context is not None
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
        assert context.cert_store_stats()["x509_ca"] > 0
        return BytesIO(b'{"serverTime":123}')

    monkeypatch.setattr(runtime, "urlopen", open_response)
    assert runtime._public_json("/api/v3/time", {}) == {"serverTime": 123}


def test_unregistered_directory_is_not_created(tmp_path):
    directory = tmp_path / "absent"
    with pytest.raises((ValueError, FileNotFoundError)):
        LivePaperSignalRunner(Feed([]), clock=lambda: NOW).run_once(directory)
    assert not directory.exists()


def test_corrupt_advisor_evidence_fails_closed_without_rewriting_it(registered):
    path = registered / "trend-advisor-decisions.jsonl"
    path.write_bytes(b'{"torn":')
    state = LivePaperSignalRunner(Feed([bar(), bar("ETHUSDT")]), clock=lambda: NOW).run_once(registered)
    assert state.kind == "failed"
    assert path.read_bytes() == b'{"torn":'
    assert not (registered / "research-round-2-summary.json").exists()


def test_service_session_excludes_concurrent_runner(registered):
    import fcntl

    with (registered / "live-paper-signal.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="already running"):
            LivePaperSignalRunner(Feed([]), clock=lambda: NOW).run_once(registered)
    assert not load_observations(registered)


def test_start_loop_honors_stop_and_restarts_same_evidence(registered):
    class StoppingFeed(Feed):
        def observations(self, symbols):
            request_stop(registered)
            return super().observations(symbols)

    state = LivePaperSignalRunner(StoppingFeed([bar(), bar("ETHUSDT")]), clock=lambda: NOW).start(
        registered, poll_seconds=1
    )
    assert state.kind == "stopped"
    assert len(load_observations(registered)) == 2
    assert any(event.kind == "reconnect" for event in events(registered))


def test_slow_evaluation_does_not_refresh_expired_source(registered):
    times = iter([NOW, NOW, NOW + timedelta(seconds=16)])
    state = LivePaperSignalRunner(Feed([bar(), bar("ETHUSDT")]), clock=lambda: next(times)).run_once(registered)
    assert state.kind == "stale"
    assert state.suggestion is None


@pytest.mark.parametrize("maximum_age", [5, 15])
@pytest.mark.parametrize("quote_lag,publish_delay", [(0, 0), (10, 0), (10, 4)])
def test_delayed_receipt_publication_expires_at_provider_deadline(
    tmp_path, monkeypatch, maximum_age, quote_lag, publish_delay
):
    """A synthetic passing advisor decision exercises real publication and status.

    Scoring is isolated here: the regression is the service extending the scorer's
    receipt-based expiry past the supporting feed's provider-time deadline.
    """
    from decimal import Decimal

    from src.research import live_paper_signal_runtime as runtime
    from src.research.day_trader_context import CalendarSnapshot, ContextObservation
    from src.research.round_two_quality import append_observations
    from src.research.trend_advisor import TrendAdvisorSuggestion

    protocol = ResearchRoundProtocol.default(round_id="expiry-test", starts_at=NOW - timedelta(days=200))
    protocol = protocol.model_copy(
        update={"warmup_minutes": 1, "maximum_observation_age_seconds": maximum_age}
    ).validated()
    register_round(protocol, tmp_path)

    def rich_bar(symbol, at):
        price = Decimal("101") + Decimal(str((at - NOW).total_seconds() / 60)) / 100
        return ContextObservation(
            **bar(
                symbol,
                at,
                open=price,
                close=price,
                high=price + Decimal("0.02"),
                low=price - Decimal("0.02"),
                bid=price - Decimal("0.01"),
                ask=price + Decimal("0.01"),
            ).model_dump(),
            bid_size=3,
            ask_size=1,
            quote_provider_at=at.replace(second=0) - timedelta(seconds=quote_lag),
            quote_received_at=at,
            quote_available_at=at,
            quote_source_key=f"quote:{symbol}:{at}",
        )

    history = [
        rich_bar(symbol, NOW + timedelta(minutes=minute)) for minute in range(-59, -1) for symbol in protocol.symbols
    ]
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
    clock = NOW - timedelta(minutes=1)
    feed = Feed([rich_bar(symbol, clock) for symbol in protocol.symbols])
    runner = LivePaperSignalRunner(feed, clock=lambda: clock)
    assert runner.run_once(tmp_path).kind == "warming"
    template = json.loads((tmp_path / "research-round-2-summary.json").read_text())["trendAdvisor"][0]

    def passing_advice(protocol, result, quality, observations, *, decision_at, registry):
        fields = TrendAdvisorSuggestion.model_validate(template).model_dump()
        fields.update(
            symbol=result.candidate.symbol,
            posture="long_research",
            decision_at=decision_at,
            available_at=decision_at,
            expires_at=decision_at + timedelta(seconds=maximum_age),
            entry_low="100",
            entry_high="101",
            invalidation="99",
            target="103",
            reasons=("trend_aligned", "candidate_confirmed"),
        )
        return TrendAdvisorSuggestion.model_validate(fields)

    monkeypatch.setattr(runtime, "advise", passing_advice)
    clock = NOW
    feed.rows = [rich_bar(symbol, clock) for symbol in protocol.symbols]
    publication_clock = iter((NOW, NOW, NOW + timedelta(seconds=publish_delay)))
    runner.clock = lambda: next(publication_clock)
    state = runner.run_once(tmp_path)
    deadline = NOW.replace(second=min(maximum_age, 15 - quote_lag))
    if NOW + timedelta(seconds=publish_delay) >= deadline:
        assert state.kind == "stale"
        assert state.suggestion is None
        assert not any(event.kind == "published" for event in events(tmp_path))
        return
    assert state.kind == "published"
    assert any(event.kind == "published" for event in events(tmp_path))
    assert state.suggestion.expires_at == deadline
    report = json.loads((tmp_path / "research-round-2-summary.json").read_text())
    assert all(TrendAdvisorSuggestion.model_validate(item).expires_at == deadline for item in report["trendAdvisor"])
    assert read_live_signal_status(tmp_path, now=deadline - timedelta(microseconds=1)).kind == "published"
    assert read_live_signal_status(tmp_path, now=deadline).kind == "stale"
    assert read_live_signal_status(tmp_path, now=deadline).suggestion is None
    # A current-looking state cannot survive loss or substitution of its context.
    context_path = tmp_path / "day-trader-context-summary.json"
    retained = context_path.read_text()
    context_path.unlink()
    assert read_live_signal_status(tmp_path, now=NOW).kind == "abstaining"
    context_path.write_text(retained)
    assert read_live_signal_status(tmp_path, now=NOW).kind == "published"
    reports_path = tmp_path / "day-trader-context-reports.jsonl"
    reports_before = reports_path.read_bytes()
    with reports_path.open("ab") as stream:
        stream.write(b'{"corrupted":"tail"}\n')
    assert read_live_signal_status(tmp_path, now=NOW).kind == "abstaining"
    reports_path.write_bytes(reports_before)
    manifest_path = tmp_path / "day-trader-context-manifest.json"
    manifest_path.unlink()
    assert read_live_signal_status(tmp_path, now=NOW).kind == "abstaining"
    runner.clock = lambda: NOW
    assert runner.run_once(tmp_path).kind == "failed"
    assert not manifest_path.exists()
