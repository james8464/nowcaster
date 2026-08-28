from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.app_snapshot import models as snapshot_models
from src.app_snapshot.models import AppSnapshot, ResearchSignalSnapshot, SnapshotMetadata
from src.app_snapshot.writer import write_snapshot_atomic


def test_snapshot_contract_is_strictly_versioned_and_uses_safe_confidence_copy():
    snapshot = AppSnapshot(
        metadata=SnapshotMetadata(
            generated_at=datetime(2026, 8, 22, tzinfo=UTC),
            git_commit="abc123",
            data_mode="demo_real_snapshot",
            source_posture="Bundled real public snapshots",
            expectation_mode="expectation_proxy",
        )
    )

    assert snapshot.schema_version == 5
    assert snapshot.broker_status.state == "live_locked"
    assert snapshot.forward_readiness.state == "live_locked"
    assert "probability of profit" not in snapshot.model_dump_json().lower()

    with pytest.raises(ValidationError):
        AppSnapshot(
            schema_version=1,
            metadata=snapshot.metadata,
        )


def test_signal_accuracy_evidence_is_optional_bounded_and_backward_compatible() -> None:
    legacy = ResearchSignalSnapshot.model_validate(
        {
            "signal_id": "legacy",
            "instrument_id": "BTCUSDT",
            "asset_class": "crypto",
            "decision_date": "2026-08-28",
            "horizon": "5m target-before-stop",
            "posture": "abstain",
            "eligibility": "research_only",
            "catalyst": "finalized bar",
            "invalidation": "evidence gate failed",
            "evidence_summary": "legacy snapshot",
        }
    )
    assert legacy.provider is None
    assert legacy.probability_lower_bound is None
    assert legacy.lower_net_edge is None
    assert legacy.drift_status is None

    rich = ResearchSignalSnapshot.model_validate(
        {
            **legacy.model_dump(),
            "provider": "binance",
            "feed": "spot",
            "venue": "Binance",
            "product": "USDT spot",
            "calibrated_probability": 0.67,
            "probability_definition": "target before protective stop within 12 bars",
            "probability_lower_bound": 0.61,
            "probability_upper_bound": 0.73,
            "calibration_observations": 420,
            "calibration_effective_observations": 211.5,
            "brier_score": 0.18,
            "expected_calibration_error": 0.04,
            "gross_edge": 0.0048,
            "estimated_cost": 0.0012,
            "lower_net_edge": 0.0011,
            "model_age_seconds": 45,
            "regime": "high_volatility",
            "drift_status": "stable",
            "drift_score": 0.12,
            "latency_ms": 84,
            "coverage_ratio": 0.31,
            "coverage_status": "selective",
        }
    )
    assert rich.calibration_effective_observations == 211.5
    assert rich.probability_lower_bound <= rich.calibrated_probability <= rich.probability_upper_bound

    with pytest.raises(ValidationError, match="probability"):
        ResearchSignalSnapshot.model_validate(
            {**rich.model_dump(), "probability_lower_bound": 0.70, "probability_upper_bound": 0.73}
        )
    with pytest.raises(ValidationError, match="effective"):
        ResearchSignalSnapshot.model_validate(
            {**rich.model_dump(), "calibration_effective_observations": 421}
        )


def _snapshot_model(name: str):
    model = getattr(snapshot_models, name, None)
    assert model is not None, f"snapshot DTO {name} is missing"
    return model


def test_strategy_and_learning_contracts_are_strict_finite_and_utc() -> None:
    strategy_model = _snapshot_model("StrategySnapshot")
    learning_run_model = _snapshot_model("LearningRunSnapshot")
    trial_model = _snapshot_model("LearningTrialSnapshot")

    strategy = strategy_model(
        strategy_id="rsi_reversal",
        version="1.0.0-abc",
        family="mean_reversion",
        dataset_hash="d" * 64,
        symbol="BTCUSDT",
        interval="5m",
        mode="paper",
        cohort_id="cohort-paper-d",
        state="paper",
        weight=0.25,
        development_metrics={"sharpe": 1.1, "dsr_probability": None},
        final_test_metrics={"sharpe": None},
        warnings=["Historical evidence is not live proof"],
        generation=2,
        progress=1.0,
        complexity=3,
        promotion_state="rejected",
        causal_audit_passed=True,
        no_repaint_badge="passed",
        latest_run_at=datetime(2026, 8, 22, 12, tzinfo=UTC),
    )
    trial = trial_model(
        trial_id="trial-1",
        candidate_hash="c" * 64,
        status="succeeded",
        fitness=0.75,
        evaluated_at=datetime(2026, 8, 22, 10, tzinfo=UTC),
        rule_text="rsi[t-1] > 50",
        complexity=3,
    )
    learning = learning_run_model(
        learning_run_id="learn-1",
        state="completed",
        evaluated_candidates=1,
        evaluation_budget=1,
        best_rule=None,
        final_boundary=datetime(2026, 8, 23, tzinfo=UTC),
        generation=1,
        progress=1.0,
        trials=[trial],
        discovered_rules=[],
        promotion_state="shadow",
        causal_audit_id=None,
        no_repaint_badge="not_audited",
    )

    assert strategy.weight == 0.25
    assert strategy.dataset_hash == "d" * 64
    assert strategy.mode == "paper"
    assert strategy.cohort_id == "cohort-paper-d"
    assert learning.evaluated_candidates == learning.evaluation_budget == 1
    with pytest.raises(ValidationError):
        strategy_model(**{**strategy.model_dump(), "weight": float("inf")})
    with pytest.raises(ValidationError):
        learning_run_model(**{**learning.model_dump(), "final_boundary": datetime(2026, 8, 23)})
    with pytest.raises(ValidationError):
        learning_run_model(
            **{
                **learning.model_dump(),
                "evaluated_candidates": 0,
                "evaluation_budget": 0,
            }
        )
    with pytest.raises(ValidationError):
        trial_model(**{**trial.model_dump(), "fitness": float("nan")})
    with pytest.raises(ValidationError):
        strategy_model(**{**strategy.model_dump(), "unexpected": "forbidden"})


def test_atomic_writer_replaces_a_complete_valid_document(tmp_path):
    snapshot = AppSnapshot(
        metadata=SnapshotMetadata(
            generated_at=datetime(2026, 8, 22, tzinfo=UTC),
            git_commit="abc123",
            data_mode="demo_real_snapshot",
            source_posture="Bundled real public snapshots",
            expectation_mode="expectation_proxy",
        )
    )

    path = write_snapshot_atomic(snapshot, tmp_path / "nested" / "nowcaster-snapshot.json")

    text = path.read_text()
    assert AppSnapshot.model_validate_json(text).schema_version == 5
    assert text.count("\n") == 1
    assert not list(path.parent.glob("*.tmp"))


def test_learning_run_wire_contract_uses_string_summary_and_required_utc_boundary() -> None:
    learning_run_model = _snapshot_model("LearningRunSnapshot")
    discovered_rule_model = _snapshot_model("DiscoveredRuleSnapshot")
    detail = discovered_rule_model(
        rule_id="rule-1",
        strategy_id="learned-rsi",
        version="1.0.0",
        state="shadow",
        rule_text="rsi[t-1] > 50",
        fitness=0.25,
        complexity=3,
        discovered_at=datetime(2026, 8, 22, 11, tzinfo=UTC),
    )

    decoded = learning_run_model.model_validate(
        {
            "learning_run_id": "learn-1",
            "state": "completed",
            "evaluated_candidates": 1,
            "evaluation_budget": 1,
            "best_rule": "rsi[t-1] > 50",
            "best_rule_detail": detail.model_dump(),
            "final_boundary": "2026-08-23T00:00:00Z",
        }
    )

    assert decoded.best_rule == "rsi[t-1] > 50"
    assert decoded.best_rule_detail.rule_id == "rule-1"
    with pytest.raises(ValidationError, match="final_boundary"):
        learning_run_model.model_validate(
            {
                "learning_run_id": "learn-without-boundary",
                "state": "completed",
                "evaluated_candidates": 0,
                "evaluation_budget": 1,
                "best_rule": None,
            }
        )


def test_snapshot_wire_instants_require_literal_z_utc() -> None:
    learning_run_model = _snapshot_model("LearningRunSnapshot")
    base = {
        "learning_run_id": "learn-utc",
        "state": "completed",
        "evaluated_candidates": 0,
        "evaluation_budget": 1,
        "best_rule": None,
    }
    assert learning_run_model.model_validate({**base, "final_boundary": "2026-08-23T00:00:00Z"})
    with pytest.raises(ValidationError, match="literal Z"):
        learning_run_model.model_validate({**base, "final_boundary": "2026-08-23T00:00:00+00:00"})
    with pytest.raises(ValidationError, match="literal Z"):
        learning_run_model.model_validate({**base, "final_boundary": "2026-08-23"})


def test_snapshot_evidence_and_details_are_structurally_bounded() -> None:
    component_model = _snapshot_model("EnsembleComponentSnapshot")
    audit_model = _snapshot_model("CausalAuditSnapshot")
    nested: object = True
    for _ in range(snapshot_models.MAX_EVIDENCE_DEPTH + 1):
        nested = {"nested": nested}
    component = {
        "strategy_id": "rsi_reversal",
        "version": "1",
        "family": "mean_reversion",
        "dataset_hash": "d" * 64,
        "symbol": "BTCUSDT",
        "interval": "5m",
        "mode": "paper",
        "cohort_id": "cohort-d",
        "effective_at": "2026-08-22T12:00:00Z",
        "weight": 0.5,
        "evidence": nested,
    }
    with pytest.raises(ValidationError, match="depth"):
        component_model.model_validate(component)

    audit = {
        "audit_id": "audit-d",
        "dataset_hash": "d" * 64,
        "strategy_id": "rsi_reversal",
        "version": "1",
        "symbol": "BTCUSDT",
        "interval": "5m",
        "mode": "paper",
        "audited_at": "2026-08-22T12:00:00Z",
        "passed": True,
        "details": {"items": list(range(snapshot_models.MAX_EVIDENCE_COLLECTION_LENGTH + 1))},
        "no_repaint_badge": "passed",
    }
    with pytest.raises(ValidationError, match="collection"):
        audit_model.model_validate(audit)

    component["evidence"] = {"text": "x" * (snapshot_models.MAX_EVIDENCE_STRING_BYTES + 1)}
    with pytest.raises(ValidationError, match="string"):
        component_model.model_validate(component)
