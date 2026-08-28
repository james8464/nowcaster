from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.app_snapshot.builder import build_app_snapshot
from src.app_snapshot.models import DeepResearchResourceSnapshot, DeepResearchRunSnapshot
from src.config.settings import Settings
from src.database.engine import Database
from src.deep_research.contracts import (
    AttemptStatus,
    CandidateAttempt,
    ChampionChallengerTransition,
    DeploymentState,
    PromotionEvidence,
    ResearchProtocol,
    ResourceSample,
    RunState,
)
from src.deep_research.repository import DeepResearchRepository
from src.reporting.strategy_report import render_strategy_research_report

NOW = datetime(2026, 8, 25, 10, tzinfo=UTC)


def _protocol() -> ResearchProtocol:
    return ResearchProtocol(
        dataset_hash="a" * 64,
        code_hash="b" * 64,
        search_space_hash="c" * 64,
        cost_policy_hash="d" * 64,
        symbol="BTCUSDT",
        provider="binance",
        feed="spot",
        interval="5m",
        seed=42,
        workers=4,
        trial_budget=10,
        continuous=False,
        final_test_start=NOW,
        created_at=NOW,
    )


def test_snapshot_projects_bounded_run_counts_resources_and_honest_failed_gates(project_root, tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'snapshot.duckdb'}")
    database.initialize()
    repository = DeepResearchRepository(database, clock=lambda: NOW)
    repository.create_run("deep-1", _protocol())
    repository.append_attempts_ordered(
        "deep-1",
        [
            CandidateAttempt(
                ordinal=index,
                candidate_hash=f"{index:064x}",
                definition={"ordinal": index},
                status=AttemptStatus.SUCCEEDED if index < 3 else AttemptStatus.FAILED,
                attempted_at=NOW,
                completed_at=NOW,
                fitness=0.5 if index < 3 else None,
                error_summary="invalid parameters" if index == 3 else None,
            )
            for index in range(1, 4)
        ],
    )
    repository.append_resource_sample(
        "deep-1",
        ResourceSample(NOW, active_workers=4, queued_trials=6, memory_bytes=1_000_000, thermal_state="nominal"),
    )
    repository.append_promotion(
        "deep-1",
        PromotionEvidence(
            candidate_hash="1".zfill(64),
            incumbent_hash=None,
            promoted=False,
            outcome="no_reliable_strategy_found",
            score=0.5,
            evidence={"stress_evidence_grade": "conservative_default_liquidity"},
            failed_gates=("minimum 300 closed trades not met",),
            transition=ChampionChallengerTransition(
                challenger_hash="1".zfill(64),
                incumbent_hash=None,
                protocol_hash=_protocol().identity,
                deployment_state=DeploymentState.REJECTED,
                shadow_cohort_hash=None,
                rollback_target_hash=None,
                forward_evidence_reset=False,
                transitioned_at=NOW,
            ),
        ),
    )
    repository.set_state("deep-1", RunState.COMPLETED, reason="cycle_complete")
    settings = Settings.load(project_root, mode="test").model_copy(
        update={"database_url": str(database.engine.url), "project_root": Path.cwd()}
    )

    snapshot = build_app_snapshot(database, settings)
    run = snapshot.deep_research_runs[0]

    assert snapshot.schema_version == 5
    assert run.run_id == "deep-1"
    assert run.evaluated_attempts == 3
    assert run.succeeded_attempts == 2
    assert run.failed_attempts == 1
    assert run.outcome == "no_reliable_strategy_found"
    assert run.failed_gates == ["minimum 300 closed trades not met"]
    assert run.resources.active_workers == 4
    assert run.progress == pytest.approx(0.3)

    report = render_strategy_research_report(snapshot)
    assert "Hypothetical research result" in report
    assert "minimum 300 closed trades not met" in report


def test_snapshot_models_reject_invalid_probability_progress_and_resources() -> None:
    with pytest.raises(ValidationError):
        DeepResearchResourceSnapshot(active_workers=-1, queued_trials=0, memory_bytes=None, thermal_state="nominal")

    with pytest.raises(ValidationError):
        DeepResearchRunSnapshot(
            run_id="run",
            state="running",
            symbol="BTCUSDT",
            interval="5m",
            provider="binance",
            feed="spot",
            dataset_hash="a" * 64,
            protocol_id="b" * 64,
            started_at=NOW,
            updated_at=NOW,
            final_test_start=NOW,
            continuous=False,
            trial_budget=10,
            cycle_budget=10,
            evaluated_attempts=0,
            succeeded_attempts=0,
            failed_attempts=0,
            generation=1,
            progress=1.1,
            best_candidate_hash=None,
            champion_score=None,
            outcome="research_running",
            failed_gates=[],
            resources=DeepResearchResourceSnapshot(),
        )
