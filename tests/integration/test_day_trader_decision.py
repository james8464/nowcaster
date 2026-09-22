"""Context gates must prevent otherwise valid advice from bypassing causal evidence."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.research.day_trader_context import MarketContextSnapshot
from src.research.day_trader_decision import DecisionContextReport, gate_suggestion
from src.research.live_paper_signal_runtime import LivePaperSignalRunner
from src.research.round_two_contracts import ResearchRoundProtocol, RoundObservation
from src.research.round_two_registry import register_round
from src.research.trend_advisor import TrendAdvisorSuggestion
from src.strategies.types import canonical_hash

NOW = datetime(2026, 9, 22, 13, 0, 2, tzinfo=UTC)


def suggestion(**changes):
    values = dict(
        symbol="BTCUSDT",
        strategy_id="trend",
        round_id="decision-test",
        protocol_hash="a" * 64,
        source_hash="b" * 64,
        candidate_hash="c" * 64,
        evidence_hash="d" * 64,
        policy_hash="e" * 64,
        posture="long_research",
        decision_at=NOW,
        available_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=12),
        entry_low="100",
        entry_high="101",
        invalidation="99",
        target="104",
        reasons=("trend_aligned", "candidate_confirmed"),
    )
    values.update(changes)
    return TrendAdvisorSuggestion.model_validate(values)


def context(**changes):
    values = dict(
        schema_version=1,
        symbol="BTCUSDT",
        protocol_hash="a" * 64,
        context_protocol_hash="f" * 64,
        source_identity_hash="b" * 64,
        source_hashes=("1" * 64,),
        conflict_evidence_hash=None,
        calendar_hash="2" * 64,
        decision_at=NOW,
        available_at=NOW,
        expires_at=NOW + timedelta(seconds=5),
        trends=tuple(
            dict(timeframe_minutes=m, direction="up", strength="1", change_bps="10", last_bar_at=NOW.replace(second=0))
            for m in (1, 5, 15)
        ),
        realized_volatility_bps=Decimal("2"),
        atr_normalized_range=Decimal("1"),
        spread_bps=Decimal("2"),
        quote_imbalance=Decimal("0.2"),
        session="europe_americas_overlap",
        calendar_blackout=False,
        exclusions=(),
    )
    values.update(changes)
    # Construct nested models before JSON hashing.
    from src.research.day_trader_context import TimeframeTrend

    values["trends"] = tuple(TimeframeTrend.model_validate(v) for v in values["trends"])
    unsigned = MarketContextSnapshot.model_construct(**values, feature_hash="0" * 64)
    values["feature_hash"] = canonical_hash(unsigned.model_dump(mode="json", exclude={"feature_hash"}))
    return MarketContextSnapshot.model_validate(values)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"exclusions": ("volatility_exceeds_maximum",)}, "context_volatile"),
        ({"exclusions": ("spread_exceeds_maximum",)}, "context_illiquid"),
        (
            {
                "trends": tuple(
                    dict(timeframe_minutes=m, direction="flat", strength="0", change_bps="0") for m in (1, 5, 15)
                )
            },
            "context_range",
        ),
        ({"calendar_blackout": True}, "context_event_blackout"),
        ({"protocol_hash": "3" * 64}, "context_protocol_mismatch"),
        ({"source_identity_hash": "3" * 64}, "context_source_mismatch"),
        ({"symbol": "ETHUSDT"}, "context_symbol_mismatch"),
        ({"decision_at": NOW + timedelta(seconds=1)}, "context_decision_mismatch"),
        ({"expires_at": NOW}, "context_expired"),
        ({"calendar_hash": None}, "context_calendar_unavailable"),
        ({"quote_imbalance": None}, "context_features_unavailable"),
    ],
)
def test_unacceptable_context_strips_posture_and_levels(changes, reason):
    report = gate_suggestion(suggestion(), context(**changes), NOW)
    assert report.suggestion.posture == "stand_aside"
    assert reason in report.reasons
    assert report.suggestion.entry_low is None
    assert report.suggestion.target is None


def test_missing_and_tampered_context_fail_closed():
    assert "context_missing" in gate_suggestion(suggestion(), None, NOW).reasons
    tampered = context().model_copy(update={"calendar_blackout": True})
    assert "context_invalid" in gate_suggestion(suggestion(), tampered, NOW).reasons


def test_approved_context_preserves_identity_and_tightens_expiry():
    original, snapshot = suggestion(), context()
    report = gate_suggestion(original, snapshot, NOW)
    assert report.suggestion.posture == "long_research"
    assert report.suggestion.expires_at == NOW + timedelta(seconds=5)
    assert report.suggestion.available_at == NOW
    for name in ("protocol_hash", "policy_hash", "source_hash", "candidate_hash", "evidence_hash"):
        assert getattr(report.suggestion, name) == getattr(original, name)
    assert report.advisor == original
    assert report.context == snapshot
    assert DecisionContextReport.model_validate_json(report.model_dump_json()) == report
    with pytest.raises(ValueError):
        DecisionContextReport.model_validate({**report.model_dump(), "report_hash": "0" * 64})


def test_context_never_upgrades_rejected_candidate_or_extends_original_expiry():
    rejected = suggestion(
        posture="stand_aside",
        entry_low=None,
        entry_high=None,
        invalidation=None,
        target=None,
        reasons=("candidate_gates_failed",),
    )
    report = gate_suggestion(rejected, context(), NOW)
    assert report.suggestion.posture == "stand_aside"
    assert "candidate_gates_failed" in report.suggestion.reasons
    short = suggestion(expires_at=NOW + timedelta(seconds=1))
    assert gate_suggestion(short, context(), NOW).suggestion.expires_at == short.expires_at
    assert gate_suggestion(short, context(), NOW + timedelta(seconds=1)).suggestion.posture == "stand_aside"


def test_live_runner_retains_context_abstention_before_publication(tmp_path):
    protocol = ResearchRoundProtocol.default(round_id="context-runtime", starts_at=NOW - timedelta(days=200))
    register_round(protocol, tmp_path)

    class Feed:
        def observations(self, symbols):
            return tuple(
                RoundObservation(
                    provider="binance",
                    feed="spot",
                    symbol=symbol,
                    provider_at=NOW.replace(second=0),
                    received_at=NOW,
                    available_at=NOW,
                    source_key=f"{symbol}:bar",
                    open="100",
                    high="102",
                    low="99",
                    close="101",
                    volume="1000",
                    bid="100.99",
                    ask="101.01",
                )
                for symbol in symbols
            )

    state = LivePaperSignalRunner(Feed(), clock=lambda: NOW).run_once(tmp_path)
    path = tmp_path / "day-trader-context-reports.jsonl"
    reports = [DecisionContextReport.model_validate_json(line) for line in path.read_text().splitlines()]
    assert reports and all(report.suggestion.posture == "stand_aside" for report in reports)
    assert all("calendar_missing" in report.reasons for report in reports)
    assert state.suggestion is None
    original = path.read_bytes()
    LivePaperSignalRunner(Feed(), clock=lambda: NOW).run_once(tmp_path)
    assert path.read_bytes() == original
    latest = json.loads((tmp_path / "day-trader-context-summary.json").read_text())
    assert latest["protocol_hash"] == protocol.identity_hash
    assert latest["reports"][0]["report_hash"] == reports[0].report_hash


def test_resealed_report_cannot_change_the_advisor_or_output_binding():
    report = gate_suggestion(suggestion(), context(), NOW)
    fields = report.model_dump()
    fields["suggestion"]["candidate_hash"] = "8" * 64
    with pytest.raises(ValueError, match="binding"):
        DecisionContextReport.model_validate(fields)


def test_old_published_state_without_context_is_never_current(tmp_path):
    from src.research.live_paper_signal_runtime import read_live_signal_status
    from src.research.live_paper_signals import LiveSignalState

    protocol = ResearchRoundProtocol.default(round_id="legacy-state", starts_at=NOW - timedelta(days=200))
    register_round(protocol, tmp_path)
    advice = suggestion(protocol_hash=protocol.identity_hash)
    state = LiveSignalState(
        kind="published", protocol_hash=protocol.identity_hash, updated_at=NOW, evaluated_at=NOW, suggestion=advice
    )
    (tmp_path / "live-paper-signal-state.json").write_text(state.model_dump_json())
    projected = read_live_signal_status(tmp_path, now=NOW)
    assert projected.kind == "abstaining"
    assert projected.suggestion is None
    assert "context_evidence_unavailable" in projected.reasons


def test_retention_rejects_policy_drift_and_corruption_without_rewriting(tmp_path):
    from src.research.day_trader_decision import retain_context_reports

    report = gate_suggestion(suggestion(), context(), NOW)
    arguments = dict(protocol_hash="a" * 64, context_protocol_hash="f" * 64)
    retain_context_reports(tmp_path, (report,), **arguments)
    path = tmp_path / "day-trader-context-reports.jsonl"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="policy changed"):
        retain_context_reports(tmp_path, (report,), **{**arguments, "context_protocol_hash": "0" * 64})
    assert path.read_bytes() == before
    path.write_bytes(before[:-1])
    with pytest.raises(ValueError, match="unterminated"):
        retain_context_reports(tmp_path, (report,), **arguments)
    assert path.read_bytes() == before[:-1]


def test_deleted_context_manifest_is_never_recreated(tmp_path):
    from src.research.day_trader_decision import retain_context_reports

    report = gate_suggestion(suggestion(), context(), NOW)
    arguments = dict(protocol_hash="a" * 64, context_protocol_hash="f" * 64)
    retain_context_reports(tmp_path, (report,), **arguments)
    manifest = tmp_path / "day-trader-context-manifest.json"
    manifest.unlink()
    with pytest.raises(ValueError, match="manifest"):
        retain_context_reports(tmp_path, (report,), **arguments)
    assert not manifest.exists()


def test_calendar_outage_recovery_requires_continuous_warmup_across_restarts(tmp_path):
    from src.research.day_trader_context import CalendarSnapshot
    from src.research.live_paper_signals import SignalEventLedger

    protocol = ResearchRoundProtocol.default(round_id="calendar-recovery", starts_at=NOW - timedelta(days=200))
    protocol = protocol.model_copy(update={"warmup_minutes": 2}).validated()
    register_round(protocol, tmp_path)

    class Feed:
        at = NOW

        def observations(self, symbols):
            return tuple(
                RoundObservation(
                    provider="binance",
                    feed="spot",
                    symbol=symbol,
                    provider_at=self.at.replace(second=0),
                    received_at=self.at,
                    available_at=self.at,
                    source_key=f"{symbol}:{self.at}",
                    open="100",
                    high="102",
                    low="99",
                    close="101",
                    volume="1000",
                    bid="100.99",
                    ask="101.01",
                )
                for symbol in symbols
            )

    feed = Feed()

    def poll(minute):
        feed.at = NOW + timedelta(minutes=minute)
        return LivePaperSignalRunner(feed, clock=lambda: feed.at).run_once(tmp_path)

    poll(0)
    calendar = CalendarSnapshot(
        source="local",
        revision="v1",
        published_at=NOW,
        available_at=NOW,
        valid_until=NOW + timedelta(hours=2),
        coverage_starts_at=NOW - timedelta(hours=1),
        coverage_ends_at=NOW + timedelta(hours=2),
        events=(),
    )
    path = tmp_path / "day-trader-calendar.jsonl"
    path.write_text(calendar.model_dump_json() + "\n")
    assert "calendar_reconnect_warmup" in poll(1).reasons
    assert "calendar_reconnect_warmup" in poll(2).reasons
    path.unlink()
    poll(3)
    path.write_text(calendar.model_dump_json() + "\n")
    assert "calendar_reconnect_warmup" in poll(4).reasons
    assert "calendar_reconnect_warmup" in poll(5).reasons
    assert "calendar_reconnect_warmup" not in poll(6).reasons
    events = SignalEventLedger(tmp_path, protocol_hash=protocol.identity_hash).events()
    assert [event.at for event in events if event.detail == "calendar_unavailable"] == [NOW, NOW + timedelta(minutes=3)]
    assert [event.at for event in events if event.detail == "calendar_reconnect_warmup"] == [
        NOW + timedelta(minutes=1),
        NOW + timedelta(minutes=4),
    ]


def test_calendar_import_stamps_receipt_and_retains_revisions(tmp_path):
    from src.research.day_trader_context import CalendarSnapshot
    from src.research.live_paper_signal_runtime import import_calendar_snapshot

    protocol = ResearchRoundProtocol.default(round_id="calendar-import", starts_at=NOW - timedelta(days=200))
    register_round(protocol, tmp_path)
    imported = CalendarSnapshot(
        source="local",
        revision="v1",
        published_at=NOW - timedelta(hours=1),
        available_at=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(hours=2),
        coverage_starts_at=NOW - timedelta(hours=1),
        coverage_ends_at=NOW + timedelta(hours=2),
        events=(),
    )
    source = tmp_path / "calendar-input.json"
    source.write_text(imported.model_dump_json())
    retained = import_calendar_snapshot(tmp_path, source, now=NOW)
    assert retained.available_at == NOW
    assert retained.published_at == imported.published_at
    path = tmp_path / "day-trader-calendar.jsonl"
    before = path.read_bytes()
    assert import_calendar_snapshot(tmp_path, source, now=NOW + timedelta(seconds=1)) == retained
    assert path.read_bytes() == before
    source.write_text(imported.model_copy(update={"valid_until": NOW + timedelta(hours=1)}).model_dump_json())
    with pytest.raises(ValueError, match="revision"):
        import_calendar_snapshot(tmp_path, source, now=NOW + timedelta(seconds=2))
    assert path.read_bytes() == before


def test_explicit_calendar_import_cli(tmp_path):
    from src.research.day_trader_context import CalendarSnapshot

    now = datetime.now(UTC)
    protocol = ResearchRoundProtocol.default(round_id="calendar-cli", starts_at=now - timedelta(days=200))
    register_round(protocol, tmp_path)
    supplied = CalendarSnapshot(
        source="local",
        revision="v1",
        published_at=now - timedelta(hours=1),
        available_at=now - timedelta(hours=1),
        valid_until=now + timedelta(hours=2),
        coverage_starts_at=now - timedelta(hours=1),
        coverage_ends_at=now + timedelta(hours=2),
        events=(),
    )
    source = tmp_path / "input.json"
    source.write_text(supplied.model_dump_json())
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[2] / "scripts/run_live_paper_signals.py"),
            "import-calendar",
            "--directory",
            str(tmp_path),
            "--file",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    imported = CalendarSnapshot.model_validate_json(result.stdout)
    assert imported.available_at >= now
    assert imported.revision == "v1"
    assert (tmp_path / "day-trader-calendar.jsonl").read_text().count("\n") == 1
