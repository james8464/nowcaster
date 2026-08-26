from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from src.cli import app

FIXTURE = Path(__file__).parents[1] / "fixtures" / "live_monitor" / "alpaca_stream.jsonl"


def bootstrap() -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "session_id": "native-session-1",
            "database_url": "duckdb:///:memory:",
            "stock_feed": "iex",
            "stocks": ["AAPL"],
            "crypto": [],
            "decision_interval": "5m",
            "config_hash": "c" * 64,
            "cohort_hash": "d" * 64,
            "alpaca_key_id": "private-key-value",
            "alpaca_secret": "private-secret-value",
        }
    )


def test_replay_command_reads_credentials_from_stdin_and_emits_deterministic_secret_free_jsonl() -> None:
    runner = CliRunner()
    arguments = ["monitor", "run", "--replay", str(FIXTURE), "--replay-provider", "alpaca"]
    first = runner.invoke(app, arguments, input=bootstrap() + "\n")
    second = runner.invoke(app, arguments, input=bootstrap() + "\n")

    assert first.exit_code == second.exit_code == 0
    assert first.stdout == second.stdout
    events = [json.loads(line) for line in first.stdout.splitlines()]
    assert events[0]["event_type"] == "ready"
    assert any(event["event_type"] == "bar_finalized" for event in events)
    assert events[-1]["event_type"] == "provider_health"
    assert "private-key-value" not in first.output
    assert "private-secret-value" not in first.output
    assert "private-key-value" not in " ".join(arguments)


def test_monitor_command_rejects_extra_bootstrap_fields_and_never_echoes_them() -> None:
    payload = json.loads(bootstrap())
    payload["unexpected"] = "do-not-echo"
    result = CliRunner().invoke(
        app,
        ["monitor", "run", "--replay", str(FIXTURE), "--replay-provider", "alpaca"],
        input=json.dumps(payload) + "\n",
    )

    assert result.exit_code == 2
    assert "configuration_rejected" in result.stderr
    assert "do-not-echo" not in result.output
