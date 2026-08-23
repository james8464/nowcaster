from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.app_snapshot import models as snapshot_models
from src.app_snapshot.models import AppSnapshot, SnapshotMetadata
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

    assert snapshot.schema_version == 2
    assert "probability of profit" not in snapshot.model_dump_json().lower()

    with pytest.raises(ValidationError):
        AppSnapshot(
            schema_version=1,
            metadata=snapshot.metadata,
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
        symbol="BTCUSDT",
        interval="5m",
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
    assert AppSnapshot.model_validate_json(text).schema_version == 2
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
