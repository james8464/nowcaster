"""End-to-end boundary tests for the credential-free Round 2 runner."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from scripts.run_research_round_two import main
from src.research.round_two_contracts import RoundStatus
from src.research.round_two_registry import append_jsonl_fsync, load_round_protocol
from src.research.round_two_runtime import (
    UnconfiguredPremiumProviderAdapter,
    build_round_report,
    ingest_file,
    register_default_round,
)
from src.research.round_two_walkforward import CandidateResult, FoldResult, SealedTestReceipt, WalkForwardFold
from src.strategies.types import canonical_hash


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


def test_ingest_accepts_multiple_jsonl_observations(tmp_path):
    """Would fail if a valid one-object-per-line feed file was parsed as one JSON value."""
    fixture = tmp_path / "finalized-observations.jsonl"
    first = {
        "provider": "binance",
        "feed": "spot",
        "symbol": "BTCUSDT",
        "provider_at": "2026-01-01T00:00:00Z",
        "received_at": "2026-01-01T00:00:00Z",
        "available_at": "2026-01-01T00:00:00Z",
        "source_key": "binance:btc:one",
        "close": "100",
    }
    second = {
        **first,
        "provider_at": "2026-01-01T00:01:00Z",
        "received_at": "2026-01-01T00:01:00Z",
        "available_at": "2026-01-01T00:01:00Z",
        "source_key": "binance:btc:two",
    }
    fixture.write_text("\n".join((json.dumps(first), json.dumps(second))) + "\n", encoding="utf-8")
    assert main(["register", "--directory", str(tmp_path), "--starts-at", "2026-01-01T00:00:00Z"]) == 0

    assert ingest_file(tmp_path, fixture) == 2


def test_report_retains_a_complete_large_result_record_before_bounding_output(tmp_path):
    """Would fail if a byte-tail dropped a valid candidate record before parsing it."""
    register_default_round(tmp_path, main_starts_at())
    protocol = load_round_protocol(tmp_path)
    result = CandidateResult(
        candidate=protocol.candidates[0],
        status=RoundStatus.INSUFFICIENT_DATA,
        reasons=("e" * (65 * 1024),),
    )
    append_jsonl_fsync(tmp_path / "candidate-results.jsonl", [result.model_dump(mode="json")])

    report = build_round_report(tmp_path)

    assert report.candidates[0]["symbol"] == "BTCUSDT"
    assert len(report.candidates[0]["reasons"][0]) < 1024


def test_report_rejects_unsealed_experimental_result(tmp_path):
    """Would fail if stored experimental text bypassed retained sealed evidence checks."""
    register_default_round(tmp_path, main_starts_at())
    protocol = load_round_protocol(tmp_path)
    forged = CandidateResult(candidate=protocol.candidates[0], status=RoundStatus.EXPERIMENTAL_PAPER_ONLY)
    append_jsonl_fsync(tmp_path / "candidate-results.jsonl", [forged.model_dump(mode="json")])

    report = build_round_report(tmp_path)

    assert report.status != RoundStatus.EXPERIMENTAL_PAPER_ONLY
    assert "candidate_evidence_missing" in report.reasons


def test_report_labels_corrupt_retained_result_evidence(tmp_path):
    """Would fail if invalid retained bytes were silently treated as no evidence."""
    register_default_round(tmp_path, main_starts_at())
    (tmp_path / "candidate-results.jsonl").write_text("not-json\n", encoding="utf-8")

    report = build_round_report(tmp_path)

    assert report.status == RoundStatus.REJECTED
    assert report.reasons == ("candidate_results_corrupt",)


def test_report_rejects_experimental_result_without_matching_execution_ledger(tmp_path):
    """Would fail if a self-consistent receipt/result could replace simulated evidence."""
    starts_at = main_starts_at()
    register_default_round(tmp_path, starts_at)
    protocol = load_round_protocol(tmp_path)
    candidate = protocol.candidates[0]
    validation_end = starts_at + timedelta(days=protocol.schedule.train_days + protocol.schedule.validation_days)
    fold = WalkForwardFold(
        fold_id=validation_end.isoformat(),
        train_start=starts_at,
        train_end=starts_at + timedelta(days=protocol.schedule.train_days),
        validation_end=validation_end,
        test_end=validation_end + timedelta(days=protocol.schedule.sealed_test_days),
    )
    selection = {"candidate": candidate.model_dump(mode="json"), "parameters": {}, "reasons": []}
    initial_receipt = SealedTestReceipt(
        round_hash=protocol.identity_hash,
        fold_id=fold.fold_id,
        sealed_at=starts_at,
        selections=(selection,),
    )
    receipt = initial_receipt.model_copy(update={"selection_hash": canonical_hash(initial_receipt.selections)})
    forged_fold = FoldResult(
        fold=fold,
        selected_parameters={},
        status=RoundStatus.EXPERIMENTAL_PAPER_ONLY,
        receipt=receipt,
    )
    forged = CandidateResult(
        candidate=candidate,
        status=RoundStatus.EXPERIMENTAL_PAPER_ONLY,
        folds=(forged_fold,),
    )
    append_jsonl_fsync(tmp_path / "sealed-test-receipts.jsonl", [receipt.model_dump(mode="json")])
    append_jsonl_fsync(tmp_path / "candidate-results.jsonl", [forged.model_dump(mode="json")])

    report = build_round_report(tmp_path)

    assert report.status == RoundStatus.REJECTED
    assert report.reasons == ("candidate_evidence_missing",)


def main_starts_at():
    from datetime import UTC, datetime

    return datetime(2026, 1, 1, tzinfo=UTC)
