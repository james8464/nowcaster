"""End-to-end boundary tests for the credential-free Round 2 runner."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.run_research_round_two import main
from src.research.round_two_contracts import RoundReport, RoundStatus
from src.research.round_two_registry import append_jsonl_fsync, load_round_protocol
from src.research.round_two_runtime import (
    UnconfiguredPremiumProviderAdapter,
    _native_round_payload,
    build_round_report,
    evaluate_registered_round,
    ingest_file,
    register_default_round,
    write_round_report,
)
from src.research.round_two_walkforward import (
    CandidateResult,
    EvaluationMetrics,
    FoldResult,
    SealedTestReceipt,
    SimulatedTrade,
    TrainingTrial,
    WalkForwardFold,
    _aggregate,
)
from src.strategies.types import canonical_hash

HEALTH = {
    "provider": "binance", "feed": "spot", "revision": "binance-spot-public-v1",
    "reported_at": "2026-01-01T00:00:00Z", "last_successful_observation_at": None,
    "maximum_age_seconds": 15, "state": "unavailable", "exclusions": ["no_available_observations"],
}


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
    assert set(payload) == {
        "roundId",
        "protocolHash",
        "status",
        "paperOnly",
        "qualificationStatus",
        "reasons",
        "candidates",
        "trendAdvisor",
        "providerHealth",
    }
    assert payload["paperOnly"] is True
    assert payload["qualificationStatus"] == "unqualified"
    assert all("strategyId" in candidate and "sealedMetrics" in candidate for candidate in payload["candidates"])
    assert "order" not in json.dumps(payload).lower()
    assert "notification" not in json.dumps(payload).lower()
    assert "lifecycle" not in json.dumps(payload).lower()


def test_premium_adapter_requires_explicit_configuration():
    """Would fail if an undeclared premium feed silently became usable."""
    with pytest.raises(RuntimeError, match="not configured"):
        UnconfiguredPremiumProviderAdapter().observations(("BTCUSDT",))


def test_report_requires_source_backed_health_and_retains_error_exclusions(tmp_path):
    register_default_round(tmp_path, main_starts_at())
    empty = _native_round_payload(build_round_report(tmp_path))
    assert empty["providerHealth"]["provider"] == "binance"
    assert empty["providerHealth"]["feed"] == "spot"
    assert empty["providerHealth"]["state"] == "unavailable"
    assert empty["providerHealth"]["lastSuccessfulObservationAt"] is None
    fixture = tmp_path / "health.json"
    fixture.write_text(json.dumps([
        {"provider": "binance", "feed": "spot", "symbol": "BTCUSDT",
         "provider_at": "2026-01-01T00:00:00Z", "received_at": "2026-01-01T00:00:00Z",
         "available_at": "2026-01-01T00:00:00Z", "source_key": "health:ok", "close": "100"},
        {"provider": "binance", "feed": "spot", "symbol": "BTCUSDT",
         "provider_at": "2026-01-01T00:01:00Z", "received_at": "2026-01-01T00:01:00Z",
         "available_at": "2026-01-01T00:01:00Z", "source_key": "health:error", "provider_error": "disconnected"},
    ]))
    ingest_file(tmp_path, fixture)
    health = _native_round_payload(build_round_report(tmp_path))["providerHealth"]
    assert health["state"] == "error"
    assert health["lastSuccessfulObservationAt"] == "2026-01-01T00:00:00Z"
    assert "provider_error" in health["exclusions"]
    assert "observation_stale" in health["exclusions"]
    assert "no_available_observations" in health["exclusions"]


def test_recent_receipt_of_old_market_data_cannot_claim_healthy_provider(tmp_path):
    from src.research.round_two_contracts import RoundObservation
    from src.research.round_two_quality import append_observations

    register_default_round(tmp_path, main_starts_at())
    protocol = load_round_protocol(tmp_path)
    now = datetime.now(UTC)
    rows = [RoundObservation(
        provider="binance", feed="spot", symbol=symbol, source_key=f"old:{symbol}:{index}",
        provider_at=main_starts_at() + timedelta(minutes=index), received_at=now, available_at=now, close="100",
    ) for symbol in protocol.symbols for index in range(61)]
    append_observations(tmp_path, protocol, rows)
    health = _native_round_payload(build_round_report(tmp_path))["providerHealth"]
    assert health["state"] == "stale"
    assert health["lastSuccessfulObservationAt"] == "2026-01-01T01:00:00Z"
    assert "observation_stale" in health["exclusions"]


def test_provider_health_becomes_healthy_only_with_current_clean_data(tmp_path):
    from src.research.round_two_contracts import RoundObservation
    from src.research.round_two_quality import append_observations

    register_default_round(tmp_path, main_starts_at())
    protocol = load_round_protocol(tmp_path)
    now = datetime.now(UTC)
    rows = [RoundObservation(
        provider="binance", feed="spot", symbol=symbol, source_key=f"fresh:{symbol}:{index}",
        provider_at=now - timedelta(minutes=60-index), received_at=now - timedelta(minutes=60-index),
        available_at=now - timedelta(minutes=60-index), close="100",
    ) for symbol in protocol.symbols for index in range(61)]
    append_observations(tmp_path, protocol, rows)
    health = _native_round_payload(build_round_report(tmp_path))["providerHealth"]
    assert health["state"] == "healthy"
    assert health["exclusions"] == []


def test_health_cannot_attribute_another_provider_to_the_registered_source(tmp_path):
    register_default_round(tmp_path, main_starts_at())
    append_jsonl_fsync(tmp_path / "observations.jsonl", [{
        "provider": "other", "feed": "spot", "symbol": "BTCUSDT", "source_key": "wrong-source",
        "provider_at": "2026-01-01T00:00:00Z", "received_at": "2026-01-01T00:00:00Z",
        "available_at": "2026-01-01T00:00:00Z", "close": "100",
    }])
    with pytest.raises(ValueError, match="provider/feed"):
        build_round_report(tmp_path)


@pytest.mark.parametrize("update", [
    {"maximum_age_seconds": 0}, {"maximum_age_seconds": 86401}, {"exclusions": ["x"] * 17},
    {"revision": "é" * 129}, {"state": "qualified"}, {"feed": "margin"},
    {"reported_at": "2026-01-01T00:00:00"}, {"state": "healthy"},
])
def test_provider_health_rejects_unbounded_or_unsubstantiated_claims(update):
    from src.research.round_two_contracts import RoundProviderHealth

    with pytest.raises(ValueError):
        RoundProviderHealth.model_validate({**HEALTH, **update})


def test_runtime_summary_decodes_with_the_strict_native_parser(tmp_path):
    """Would fail if Python's published report drifted from the native wire contract."""
    register_default_round(tmp_path, main_starts_at(), round_id="round-two-native-wire")
    observation_path = tmp_path / "one-finalized-observation.json"
    observation_path.write_text(
        json.dumps(
            [{
                "provider": "binance", "feed": "spot", "symbol": "BTCUSDT",
                "provider_at": "2026-01-01T00:00:00Z", "received_at": "2026-01-01T00:00:00Z",
                "available_at": "2026-01-01T00:00:00Z", "source_key": "native-wire:btc:one", "close": "100",
            }]
        ),
        encoding="utf-8",
    )
    ingest_file(tmp_path, observation_path)
    evaluate_registered_round(tmp_path)
    report_path = write_round_report(tmp_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["candidates"]
    assert {"symbol", "strategyId", "sealedMetrics", "paperOnly", "qualificationStatus"} <= set(
        payload["candidates"][0]
    )
    environment = {**os.environ, "NOWCASTER_RESEARCH_ROUND_REPORT_PATH": str(report_path)}
    package_directory = Path(__file__).resolve().parents[2] / "macos" / "Nowcaster"

    completed = subprocess.run(
        ["swift", "test", "--filter", "decodesRuntimeOutputWireFormat"],
        cwd=package_directory,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def _native_candidate(**updates):
    candidate = {
        "symbol": "BTCUSDT",
        "strategy_id": "ema",
        "direction": "long",
        "status": "insufficient_data",
        "paper_only": True,
        "qualification_status": "unqualified",
        "reasons": ["insufficient_data"],
        "sealed_metrics": {
            "net_return": "0", "stressed_net_return": "0", "lower_edge": None,
            "trade_count": 0, "maximum_drawdown": "0", "coverage": "0",
        },
    }
    candidate.update(updates)
    return candidate


@pytest.mark.parametrize(
    ("report", "message"),
    [
        (RoundReport(provider_health=HEALTH, round_id="r" * 257,
                     protocol_hash="a" * 64, status=RoundStatus.INSUFFICIENT_DATA), "roundId"),
        (
            RoundReport(
                provider_health=HEALTH,
                round_id="round", protocol_hash="a" * 64, status=RoundStatus.INSUFFICIENT_DATA,
                reasons=tuple("reason" for _ in range(17)),
            ),
            "reasons",
        ),
        (
            RoundReport(
                provider_health=HEALTH,
                round_id="round", protocol_hash="a" * 64, status=RoundStatus.INSUFFICIENT_DATA,
                candidates=tuple(_native_candidate() for _ in range(101)),
            ),
            "candidates",
        ),
        (
            RoundReport(
                provider_health=HEALTH,
                round_id="round", protocol_hash="a" * 64, status=RoundStatus.INSUFFICIENT_DATA,
                candidates=(_native_candidate(strategy_id="s" * 257),),
            ),
            "strategyId",
        ),
        (
            RoundReport(
                provider_health=HEALTH,
                round_id="round", protocol_hash="a" * 64, status=RoundStatus.INSUFFICIENT_DATA,
                candidates=(
                    _native_candidate(
                        sealed_metrics={**_native_candidate()["sealed_metrics"], "net_return": "nan"}
                    ),
                ),
            ),
            "netReturn",
        ),
    ],
)
def test_native_wire_publisher_rejects_values_the_native_parser_would_refuse(report, message):
    """Would fail if retained evidence were silently truncated or made import-incompatible."""
    with pytest.raises(ValueError, match=message):
        _native_round_payload(report)


@pytest.mark.parametrize("value", ("1_0", " 1 ", "1\n", "٠١"))
def test_native_wire_publisher_rejects_non_swift_numeric_spellings(value):
    """Would fail if Python float parsing accepted a spelling unavailable to native decoding."""
    report = RoundReport(
        provider_health=HEALTH,
        round_id="round",
        protocol_hash="a" * 64,
        status=RoundStatus.INSUFFICIENT_DATA,
        candidates=(
            _native_candidate(sealed_metrics={**_native_candidate()["sealed_metrics"], "net_return": value}),
        ),
    )

    with pytest.raises(ValueError, match="netReturn"):
        _native_round_payload(report)


@pytest.mark.parametrize("value", ("0", "-0", "1.25", ".5", "1.", "1e-3", "-2E+4"))
def test_native_wire_publisher_accepts_ascii_swift_numeric_spellings(value):
    report = RoundReport(
        provider_health=HEALTH,
        round_id="round",
        protocol_hash="a" * 64,
        status=RoundStatus.INSUFFICIENT_DATA,
        candidates=(
            _native_candidate(sealed_metrics={**_native_candidate()["sealed_metrics"], "net_return": value}),
        ),
    )

    payload = _native_round_payload(report)
    assert payload["candidates"][0]["sealedMetrics"]["netReturn"] == value


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


def test_report_retains_a_complete_large_result_record_and_refuses_an_unimportable_publication(tmp_path):
    """Would fail if a byte-tail dropped or silently truncated retained candidate evidence."""
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
    assert len(report.candidates[0]["reasons"][0]) == 65 * 1024
    with pytest.raises(ValueError, match="candidate reasons reason"):
        _native_round_payload(report)


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


def test_report_rejects_duplicate_fold_even_when_aggregate_matches(tmp_path):
    """Would fail if a duplicate consumed fold could inflate an experimental aggregate."""
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
    metrics = sufficient_metrics(starts_at)
    selection = {"candidate": candidate.model_dump(mode="json"), "parameters": {}, "reasons": []}
    initial_receipt = SealedTestReceipt(
        round_hash=protocol.identity_hash,
        fold_id=fold.fold_id,
        sealed_at=starts_at,
        selections=(selection,),
    )
    receipt = initial_receipt.model_copy(update={"selection_hash": canonical_hash(initial_receipt.selections)})
    retained_fold = FoldResult(
        fold=fold,
        selected_parameters={},
        training_trials=(TrainingTrial(parameters={}, metrics=metrics),),
        train=metrics,
        validation=metrics,
        sealed_test=metrics,
        status=RoundStatus.EXPERIMENTAL_PAPER_ONLY,
        receipt=receipt,
    )
    aggregate = _aggregate([metrics, metrics])
    duplicate = CandidateResult(
        candidate=candidate,
        selected_parameters={},
        train=aggregate,
        validation=aggregate,
        sealed_test=aggregate,
        folds=(retained_fold, retained_fold),
        status=RoundStatus.EXPERIMENTAL_PAPER_ONLY,
    )
    append_jsonl_fsync(tmp_path / "sealed-test-receipts.jsonl", [receipt.model_dump(mode="json")])
    append_jsonl_fsync(
        tmp_path / "sealed-test-results.jsonl",
        [{"candidate": candidate.model_dump(mode="json"), "result": retained_fold.model_dump(mode="json")}],
    )
    append_jsonl_fsync(tmp_path / "candidate-results.jsonl", [duplicate.model_dump(mode="json")])

    report = build_round_report(tmp_path)

    assert report.status == RoundStatus.REJECTED
    assert report.reasons == ("candidate_evidence_missing",)


def sufficient_metrics(at):
    trade = SimulatedTrade(
        decision_at=at,
        entry_at=at,
        exit_at=at,
        entry_price=Decimal("100"),
        exit_price=Decimal("101"),
        quantity=Decimal("1"),
        net_pnl=Decimal("1"),
        gross_pnl=Decimal("1"),
        fees=Decimal("0"),
        spread_cost=Decimal("0"),
        slippage_cost=Decimal("0"),
    )
    trades = (trade,) * 100
    return EvaluationMetrics(
        net_return=Decimal("0.01"),
        stressed_net_return=Decimal("0.01"),
        lower_edge=Decimal("0.01"),
        trade_count=len(trades),
        coverage=Decimal("1"),
        trades=trades,
    )


def main_starts_at():
    from datetime import UTC, datetime

    return datetime(2026, 1, 1, tzinfo=UTC)
