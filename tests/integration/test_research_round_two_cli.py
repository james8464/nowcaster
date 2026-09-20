"""End-to-end boundary tests for the credential-free Round 2 runner."""

from __future__ import annotations

import json

import pytest

from scripts.run_research_round_two import main
from src.research.round_two_runtime import UnconfiguredPremiumProviderAdapter


def test_cli_register_ingest_evaluate_and_status_are_paper_only(tmp_path, capsys):
    """Would fail if the runner emitted an actionable or qualified result."""
    fixture = tmp_path / "finalized-observations.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "provider": "binance",
                    "feed": "spot",
                    "symbol": "BTCUSDT",
                    "provider_at": "2026-01-01T00:00:00Z",
                    "received_at": "2026-01-01T00:00:00Z",
                    "available_at": "2026-01-01T00:00:00Z",
                    "source_key": "binance:btc:2026-01-01T00:00:00Z",
                    "close": "100",
                }
            ]
        ),
        encoding="utf-8",
    )

    assert main(["register", "--directory", str(tmp_path), "--starts-at", "2026-01-01T00:00:00Z"]) == 0
    assert main(["ingest", "--directory", str(tmp_path), "--input", str(fixture)]) == 0
    assert main(["evaluate", "--directory", str(tmp_path)]) == 0
    assert main(["status", "--directory", str(tmp_path)]) == 0

    payload = json.loads((tmp_path / "research-round-2-summary.json").read_text(encoding="utf-8"))
    assert payload["paper_only"] is True
    assert payload["qualification_status"] == "unqualified"
    assert "order" not in json.dumps(payload).lower()
    assert "notification" not in json.dumps(payload).lower()
    assert "lifecycle" not in json.dumps(payload).lower()


def test_premium_adapter_requires_explicit_configuration():
    """Would fail if an undeclared premium feed silently became usable."""
    with pytest.raises(RuntimeError, match="not configured"):
        UnconfiguredPremiumProviderAdapter().observations(("BTCUSDT",))


def test_ingest_rejects_action_shaped_input(tmp_path):
    """Would fail if an imported action payload could cross the research boundary."""
    fixture = tmp_path / "unsafe-observations.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "provider": "binance",
                    "feed": "spot",
                    "symbol": "BTCUSDT",
                    "provider_at": "2026-01-01T00:00:00Z",
                    "received_at": "2026-01-01T00:00:00Z",
                    "available_at": "2026-01-01T00:00:00Z",
                    "source_key": "binance:btc:unsafe",
                    "close": "100",
                    "order_id": "must-not-cross",
                }
            ]
        ),
        encoding="utf-8",
    )
    assert main(["register", "--directory", str(tmp_path), "--starts-at", "2026-01-01T00:00:00Z"]) == 0

    with pytest.raises(ValueError, match="action-shaped"):
        main(["ingest", "--directory", str(tmp_path), "--input", str(fixture)])
