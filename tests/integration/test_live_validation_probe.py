from __future__ import annotations

import shutil
import sys
from pathlib import Path

from scripts.validate_live_monitor import run_probe


def test_probe_uses_isolated_public_crypto_bootstrap_and_clean_shutdown(tmp_path: Path, monkeypatch) -> None:
    project = Path(__file__).parents[2]
    root = tmp_path / "project"
    shutil.copytree(project / "config", root / "config")
    monkeypatch.setenv("APCA_API_KEY_ID", "parent-secret")
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    secret_name = "APCA_" + "API_SECRET_KEY"
    (root / ".env").write_text(f"{secret_name}=file-secret\n", encoding="utf-8")
    engine = tmp_path / "fake-engine"
    engine.write_text(
        f"#!{sys.executable}\n"
        + """
import json
import os
import sys
from datetime import UTC, datetime

assert "APCA_API_KEY_ID" not in os.environ
assert "APCA_API_SECRET_KEY" not in os.environ
assert os.environ["NOWCASTER_DISABLE_DOTENV"] == "1"
bootstrap = json.loads(sys.stdin.readline())
assert bootstrap["stocks"] == []
assert bootstrap["crypto"] == ["BTCUSDT", "ETHUSDT"]
assert "alpaca_key_id" not in bootstrap and "alpaca_secret" not in bootstrap
assert bootstrap["database_url"].endswith("/monitor.duckdb")
now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
def emit(sequence, kind, payload):
    print(json.dumps({"schema_version": 1, "event_id": str(sequence).zfill(64), "sequence": sequence,
                      "event_type": kind, "emitted_at": now, "payload": payload}), flush=True)
emit(0, "ready", {"status": "live", "qualified_cohorts": 0, "cohort_hash": "0" * 64,
                   "readiness_receipt_id": None})
emit(1, "provider_health", {"provider": "binance", "feed": "spot", "status": "healthy",
                              "reason": "subscribed", "occurred_at": now})
for sequence, symbol in enumerate(("BTCUSDT", "ETHUSDT"), start=2):
    emit(sequence, "quote", {"provider": "binance", "feed": "spot", "symbol": symbol,
                              "bid": "99.9", "ask": "100", "last": "99.95", "tick_size": "0.01",
                              "sequence": None, "provider_time": now, "received_at": now, "processed_at": now})
assert json.loads(sys.stdin.readline())["command"] == "shutdown"
""",
        encoding="utf-8",
    )
    engine.chmod(0o700)
    output = tmp_path / "probe"

    report = run_probe(root=root, output=output, seconds=1, engine=engine)

    assert report["issues"] == []
    assert report["exit_code"] == 0
    assert report["engine"] == "external"
    assert report["safety"] == {
        "broker_credentials_supplied": False,
        "environment_allowlisted": True,
        "environment_file_loading": False,
        "isolated_database": True,
        "order_submission": False,
    }
    assert (output / "summary.json").is_file()
    assert len((output / "observations.jsonl").read_text(encoding="utf-8").splitlines()) == 4


def test_probe_rejects_a_clean_engine_exit_before_the_requested_window(tmp_path: Path) -> None:
    project = Path(__file__).parents[2]
    root = tmp_path / "project"
    shutil.copytree(project / "config", root / "config")
    engine = tmp_path / "early-exit-engine"
    engine.write_text(
        f"#!{sys.executable}\n"
        + """
import json
import sys
from datetime import UTC, datetime

json.loads(sys.stdin.readline())
now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
def emit(sequence, kind, payload):
    print(json.dumps({"schema_version": 1, "event_id": str(sequence).zfill(64), "sequence": sequence,
                      "event_type": kind, "emitted_at": now, "payload": payload}), flush=True)
emit(0, "ready", {"status": "live", "qualified_cohorts": 0, "cohort_hash": "0" * 64,
                   "readiness_receipt_id": None})
emit(1, "provider_health", {"provider": "binance", "feed": "spot", "status": "healthy",
                              "reason": "subscribed", "occurred_at": now})
for sequence, symbol in enumerate(("BTCUSDT", "ETHUSDT"), start=2):
    emit(sequence, "quote", {"provider": "binance", "feed": "spot", "symbol": symbol,
                              "bid": "99.9", "ask": "100", "last": "99.95", "tick_size": "0.01",
                              "sequence": None, "provider_time": now, "received_at": now, "processed_at": now})
""",
        encoding="utf-8",
    )
    engine.chmod(0o700)

    report = run_probe(root=root, output=tmp_path / "early-exit", seconds=10, engine=engine)

    assert report["exit_code"] == 0
    assert report["live_seconds"] < report["requested_live_seconds"]
    assert "incomplete_observation_window" in report["issues"]
