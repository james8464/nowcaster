import json
from datetime import UTC, datetime

import pytest

from src.intraday.journal import PaperJournal

T = datetime(2026, 10, 6, 8, 20, tzinfo=UTC)


def test_journal_retains_open_and_close_with_hash_chain_across_restart(tmp_path):
    path = tmp_path / "round-1"
    with PaperJournal(path, protocol_hash="a" * 64) as journal:
        first = journal.append("opened", T, {"plan_hash": "b" * 64, "entry": "24002"})
        second = journal.append("closed", T, {"open_hash": first.record_hash, "net_pnl": "-2.5"})
    with PaperJournal(path, protocol_hash="a" * 64) as journal:
        assert [event.kind for event in journal.events()] == ["opened", "closed"]
        assert second.previous_hash == first.record_hash
        with pytest.raises(ValueError, match="duplicate"):
            journal.append("closed", T, {"open_hash": first.record_hash, "net_pnl": "-2.5"})


def test_journal_rejects_changed_protocol_and_tampered_loss(tmp_path):
    path = tmp_path / "round-1"
    with PaperJournal(path, protocol_hash="a" * 64) as journal:
        journal.append("closed", T, {"net_pnl": "-10"})
    with pytest.raises(ValueError, match="protocol"):
        PaperJournal(path, protocol_hash="b" * 64)
    events = path / "events.jsonl"
    row = json.loads(events.read_text())
    row["payload"]["net_pnl"] = "10"
    events.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="hash"):
        PaperJournal(path, protocol_hash="a" * 64)
