import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from src.intraday.session_journal import SessionJournal


def test_parity_cli_reads_retained_account_events_without_rewriting_them(tmp_path):
    session = tmp_path / "session"
    journal = SessionJournal(session, {"round_id": "read-only-fixture"})
    journal.append(
        "decision",
        datetime(2026, 10, 14, 7, 20, 2, tzinfo=UTC),
        {
            "bar_key": "fixed-fixture",
            "broker_symbol": "DE30_EUR",
            "strategy_id": "opening_range_15",
            "status": "ready",
            "direction": "long",
            "reason": "confirmed_breakout",
        },
    )
    original = (session / "quotes.jsonl").read_bytes()
    pine = tmp_path / "pine.jsonl"
    pine.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "tradingview_chart",
                "broker_symbol": "DE30_EUR",
                "strategy_id": "opening_range_15",
                "bar_close_utc_ms": 1791962400000,
                "direction": "long",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "parity.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/compare_pine_nowcaster.py",
            "--pine-alerts",
            str(pine),
            "--account-session",
            str(session),
            "--output",
            str(output),
        ],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["direction_matches"] == 1
    assert report["rows"][0]["status"] == "direction_match"
    assert report["price_fill_parity_supported"] is False
    assert (session / "quotes.jsonl").read_bytes() == original
