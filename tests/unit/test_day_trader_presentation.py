"""The native bridge validates retained evidence before projecting display fields."""

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from src.research.day_trader_decision import gate_suggestion, retain_context_reports
from src.research.day_trader_lifecycle import LifecycleLedger, advance_lifecycle
from src.research.day_trader_presentation import decision_presentation
from src.research.round_two_contracts import ResearchRoundProtocol
from src.research.round_two_registry import register_round
from src.strategies.types import canonical_hash
from tests.integration.test_day_trader_decision import NOW, context, suggestion
from tests.unit.test_day_trader_lifecycle import lifecycle, observation


def test_projection_expires_context_and_keeps_completed_outcomes_historical(tmp_path):
    report = gate_suggestion(suggestion(), context(), NOW)
    context_hash = report.context.context_protocol_hash
    retain_context_reports(tmp_path, (report,), protocol_hash="a" * 64, context_protocol_hash=context_hash)
    current = decision_presentation(tmp_path, protocol_hash="a" * 64, context_protocol_hash=context_hash, now=NOW)
    assert current["contexts"][0]["regime"] == "trend"
    ledger = LifecycleLedger(tmp_path, protocol_hash="a" * 64)
    initial = lifecycle()
    ledger.append(initial)
    ledger.append(advance_lifecycle(initial, observation(58, high="105", close="104")))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    historical = decision_presentation(
        tmp_path, protocol_hash="a" * 64, context_protocol_hash=context_hash, now=NOW + timedelta(seconds=60)
    )
    assert historical["contexts"] == []
    assert historical["outcomes"][0]["exit_reason"] == "target"
    assert "entry_low" not in historical["outcomes"][0]
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}


def test_rejects_corrupted_or_unbound_retained_history(tmp_path):
    report = gate_suggestion(suggestion(), context(), NOW)
    identity = report.context.context_protocol_hash
    retain_context_reports(tmp_path, (report,), protocol_hash="a" * 64, context_protocol_hash=identity)
    with pytest.raises(ValueError):
        decision_presentation(tmp_path, protocol_hash="b" * 64, context_protocol_hash=identity, now=NOW)
    path = tmp_path / "day-trader-context-reports.jsonl"
    value = json.loads(path.read_text())
    value["quantity"] = 1
    path.write_text(json.dumps(value) + "\n")
    with pytest.raises(ValueError):
        decision_presentation(tmp_path, protocol_hash="a" * 64, context_protocol_hash=identity, now=NOW)


def test_empty_history_does_not_create_files(tmp_path):
    result = decision_presentation(tmp_path, protocol_hash="a" * 64, context_protocol_hash="b" * 64, now=NOW)
    assert result["contexts"] == result["outcomes"] == []
    assert list(tmp_path.iterdir()) == []


def test_cli_projects_registered_identity_without_mutation(tmp_path):
    protocol = ResearchRoundProtocol.default(round_id="native-context-test", starts_at=NOW)
    register_round(protocol, tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    result = subprocess.run(
        [sys.executable, "scripts/run_live_paper_signals.py", "decision-context", "--directory", str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    digest = payload.pop("content_hash")
    assert digest == canonical_hash(payload)
    assert payload["protocol_hash"] == protocol.identity_hash
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
