import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.research.day_trader_context import DayTraderContextProtocol, MarketContextSnapshot, TimeframeTrend
from src.research.day_trader_decision import gate_suggestion, retain_context_reports
from src.research.day_trader_lifecycle import PaperLifecycle
from src.research.live_paper_notification_bridge import (
    read_notification_evidence,
    record_notification_outcome,
    reserve_notification,
)
from src.research.live_paper_signal_runtime import _reconcile_publications
from src.research.live_paper_signals import LiveSignalState, SignalEventLedger
from src.research.round_two_contracts import ResearchRoundProtocol
from src.research.round_two_registry import append_jsonl_fsync, register_round
from src.research.trend_advisor import TrendAdvisorSuggestion
from src.strategies.types import canonical_hash

NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)


def test_historical_notification_lookup_is_exact_and_read_only(published):
    directory, identity = published
    notice = reserve_notification(directory, enabled=True, protocol_hash=identity, now=NOW)
    record_notification_outcome(
        directory,
        protocol_hash=identity,
        material_key=notice.material_key,
        outcome="delivered",
        now=NOW + timedelta(seconds=1),
    )
    before = {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}
    result = read_notification_evidence(directory, protocol_hash=identity, material_key=notice.material_key)
    assert result["notification"]["material_key"] == notice.material_key
    assert result["suggestion"]["candidate_hash"] == notice.candidate_hash
    assert result["outcome"] == "delivered"
    for key, protocol in [("f" * 64, identity), (notice.material_key, "f" * 64)]:
        with pytest.raises(ValueError):
            read_notification_evidence(directory, protocol_hash=protocol, material_key=key)
    assert before == {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}
    helper = os.environ.get("NOWCASTER_PAPER_HELPER")
    executable = [str(Path(helper).resolve())] if helper else [sys.executable, "scripts/run_live_paper_signals.py"]
    response = subprocess.run(
        [
            *executable,
            "notification-evidence",
            "--directory",
            str(directory),
            "--protocol-hash",
            identity,
            "--material-key",
            notice.material_key,
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=directory if helper else None,
        env={"PATH": "/usr/bin:/bin", "HOME": str(directory), "TMPDIR": str(directory)} if helper else None,
    )
    assert json.loads(response.stdout) == result
    assert before == {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}


@pytest.fixture
def published(tmp_path):
    return _published(tmp_path, NOW)


def _published(tmp_path, now):
    protocol = ResearchRoundProtocol.default(round_id="notice-test", starts_at=now - timedelta(days=200))
    register_round(protocol, tmp_path)
    candidate = protocol.candidates[0]
    suggestion = TrendAdvisorSuggestion(
        symbol=candidate.symbol,
        strategy_id=candidate.strategy_id,
        round_id=protocol.round_id,
        protocol_hash=protocol.identity_hash,
        source_hash=canonical_hash(protocol.source.model_dump(mode="json")),
        candidate_hash=canonical_hash(candidate.model_dump(mode="json")),
        evidence_hash="d" * 64,
        policy_hash="e" * 64,
        posture="long_research",
        decision_at=now,
        available_at=now,
        expires_at=now + timedelta(seconds=10),
        entry_low="100",
        entry_high="101",
        invalidation="99",
        target="103",
        reasons=("trend_aligned", "candidate_confirmed"),
    )
    settings = DayTraderContextProtocol(round_protocol=protocol)
    unsigned = MarketContextSnapshot.model_construct(
        symbol=candidate.symbol,
        protocol_hash=protocol.identity_hash,
        context_protocol_hash=settings.identity_hash,
        source_identity_hash=suggestion.source_hash,
        source_hashes=("1" * 64,),
        calendar_hash="2" * 64,
        decision_at=now,
        available_at=now,
        expires_at=suggestion.expires_at,
        trends=tuple(
            TimeframeTrend(
                timeframe_minutes=minutes,
                direction="up",
                strength="1",
                change_bps="10",
                last_bar_at=now.replace(second=0, microsecond=0),
            )
            for minutes in (1, 5, 15)
        ),
        realized_volatility_bps=Decimal("2"),
        atr_normalized_range=Decimal("1"),
        spread_bps=Decimal("2"),
        quote_imbalance=Decimal("0.2"),
        session="europe_americas_overlap",
        calendar_blackout=False,
        exclusions=(),
        feature_hash="0" * 64,
    )
    payload = unsigned.model_dump(mode="json", exclude={"feature_hash"})
    context = MarketContextSnapshot.model_validate({**payload, "feature_hash": canonical_hash(payload)})
    decision = gate_suggestion(suggestion, context, now)
    assert decision.suggestion.posture == "long_research"
    retain_context_reports(
        tmp_path, (decision,), protocol_hash=protocol.identity_hash, context_protocol_hash=settings.identity_hash
    )
    (tmp_path / "day-trader-context-summary.json").write_text(
        json.dumps(
            {
                "protocol_hash": protocol.identity_hash,
                "context_protocol_hash": settings.identity_hash,
                "reports": [decision.model_dump(mode="json")],
            }
        )
    )
    initial = PaperLifecycle.from_report(decision, created_at=now)
    append_jsonl_fsync(tmp_path / "paper-publications.jsonl", [initial.model_dump(mode="json")])
    _reconcile_publications(tmp_path, protocol, SignalEventLedger(tmp_path, protocol_hash=protocol.identity_hash), now)
    state = LiveSignalState(
        kind="published",
        protocol_hash=protocol.identity_hash,
        updated_at=now,
        evaluated_at=now,
        suggestion=decision.suggestion,
    )
    (tmp_path / "live-paper-signal-state.json").write_text(state.model_dump_json())
    return tmp_path, protocol.identity_hash


def test_reservation_requires_opt_in_and_survives_restart(published):
    directory, identity = published
    assert reserve_notification(directory, enabled=False, protocol_hash=identity, now=NOW) is None
    payload = reserve_notification(directory, enabled=True, protocol_hash=identity, now=NOW)
    assert payload.symbol == "BTCUSDT"
    assert reserve_notification(directory, enabled=True, protocol_hash=identity, now=NOW) is None
    record_notification_outcome(
        directory,
        protocol_hash=identity,
        material_key=payload.material_key,
        outcome="delivered",
        now=NOW + timedelta(seconds=1),
    )
    kinds = [item.kind for item in SignalEventLedger(directory, protocol_hash=identity).events()]
    assert kinds == ["published", "notification_attempt", "notification_outcome"]
    with pytest.raises(ValueError):
        record_notification_outcome(
            directory,
            protocol_hash=identity,
            material_key=payload.material_key,
            outcome="delivered",
            now=NOW + timedelta(seconds=2),
        )


def test_expiry_identity_and_unknown_attempt_fail_closed(published):
    directory, identity = published
    assert (
        reserve_notification(directory, enabled=True, protocol_hash=identity, now=NOW + timedelta(seconds=10)) is None
    )
    with pytest.raises(ValueError):
        reserve_notification(directory, enabled=True, protocol_hash="f" * 64, now=NOW)
    with pytest.raises(ValueError):
        record_notification_outcome(
            directory, protocol_hash=identity, material_key="d" * 64, outcome="delivered", now=NOW
        )


def test_torn_reservation_retained_and_expired_delivery_is_failed(published):
    directory, identity = published
    payload = reserve_notification(directory, enabled=True, protocol_hash=identity, now=NOW)
    result = record_notification_outcome(
        directory,
        protocol_hash=identity,
        material_key=payload.material_key,
        outcome="delivered",
        now=NOW + timedelta(seconds=10),
    )
    assert result == "failed"
    path = directory / "paper-notification-delivery.jsonl"
    path.write_bytes(path.read_bytes()[:-1])
    original = path.read_bytes()
    with pytest.raises(ValueError):
        reserve_notification(directory, enabled=True, protocol_hash=identity, now=NOW)
    assert path.read_bytes() == original


def test_notification_rejects_candidate_or_source_outside_protocol(published):
    directory, identity = published
    path = directory / "live-paper-signal-state.json"
    state = LiveSignalState.model_validate_json(path.read_text())
    for field in ("candidate_hash", "source_hash"):
        suggestion = state.suggestion.model_copy(update={field: "f" * 64})
        path.write_text(state.model_copy(update={"suggestion": suggestion}).model_dump_json())
        assert reserve_notification(directory, enabled=True, protocol_hash=identity, now=NOW) is None


def test_cli_notification_is_local_opt_in_and_records_outcome(tmp_path):
    directory, identity = _published(tmp_path, datetime.now(UTC))
    script = Path(__file__).resolve().parents[2] / "scripts/run_live_paper_signals.py"

    def invoke(command, *args):
        return subprocess.run(
            [sys.executable, str(script), command, "--directory", str(directory), "--protocol-hash", identity, *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )

    assert json.loads(invoke("notification").stdout) is None
    payload = json.loads(invoke("notification", "--enabled").stdout)
    assert payload["symbol"] == "BTCUSDT"
    assert json.loads(invoke("notification", "--enabled").stdout) is None
    outcome = invoke("notification-outcome", "--material-key", payload["material_key"], "--outcome", "failed")
    assert json.loads(outcome.stdout) == {"outcome": "failed"}
