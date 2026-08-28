from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from src.database.engine import Database
from src.deep_research.contracts import AttemptStatus, CandidateAttempt, ResearchProtocol, RunState
from src.deep_research.repository import DeepResearchRepository

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _protocol(*, dataset_hash: str = "a" * 64) -> ResearchProtocol:
    return ResearchProtocol(
        dataset_hash=dataset_hash,
        code_hash="b" * 64,
        search_space_hash="c" * 64,
        cost_policy_hash="d" * 64,
        symbol="BTCUSDT",
        provider="binance",
        feed="spot",
        interval="5m",
        seed=42,
        workers=2,
        trial_budget=3,
        continuous=False,
        final_test_start=NOW,
        created_at=NOW,
    )


def _attempt(ordinal: int, status: AttemptStatus = AttemptStatus.SUCCEEDED) -> CandidateAttempt:
    completed = NOW + timedelta(seconds=ordinal)
    return CandidateAttempt(
        ordinal=ordinal,
        candidate_hash=f"{ordinal:064x}",
        definition={"ordinal": ordinal},
        status=status,
        attempted_at=NOW,
        completed_at=completed,
        fitness=float(ordinal) if status is AttemptStatus.SUCCEEDED else None,
        error_summary="bad candidate" if status in {AttemptStatus.FAILED, AttemptStatus.INVALID} else None,
    )


def _repository(tmp_path) -> tuple[Database, DeepResearchRepository]:
    database = Database.from_url(f"duckdb:///{tmp_path / 'research.duckdb'}")
    database.initialize()
    return database, DeepResearchRepository(database, clock=lambda: NOW)


def test_schema_v5_initializes_idempotently_with_all_research_tables(tmp_path) -> None:
    database, _ = _repository(tmp_path)
    database.initialize()

    assert database.schema_version() == 10
    assert {
        "deep_research_runs",
        "deep_research_trials",
        "deep_research_fold_metrics",
        "deep_research_stress_metrics",
        "deep_research_promotions",
        "deep_research_checkpoints",
        "deep_research_resource_samples",
    } <= set(database.table_names())


def test_repository_commits_out_of_order_completions_in_ordinal_order_and_counts_failures(tmp_path) -> None:
    database, repository = _repository(tmp_path)
    protocol = _protocol()
    repository.create_run("run-1", protocol)

    repository.append_attempts_ordered(
        "run-1",
        [_attempt(3, AttemptStatus.FAILED), _attempt(1), _attempt(2, AttemptStatus.DUPLICATE)],
    )

    rows = database.frame(
        "select ordinal, status from deep_research_trials where run_id = 'run-1' order by persisted_sequence"
    )
    assert rows.to_dict("records") == [
        {"ordinal": 1, "status": "succeeded"},
        {"ordinal": 2, "status": "duplicate"},
        {"ordinal": 3, "status": "failed"},
    ]


def test_trial_ordinals_are_append_only(tmp_path) -> None:
    _, repository = _repository(tmp_path)
    repository.create_run("run-1", _protocol())
    repository.append_attempt("run-1", _attempt(1))

    with pytest.raises(ValueError, match="already exists"):
        repository.append_attempt("run-1", _attempt(1, AttemptStatus.FAILED))


def test_checkpoint_resume_requires_exact_protocol_identity(tmp_path) -> None:
    _, repository = _repository(tmp_path)
    protocol = _protocol()
    repository.create_run("run-1", protocol)
    repository.append_attempt("run-1", _attempt(1))
    checkpoint_id = repository.checkpoint("run-1", next_ordinal=2, generation=1, payload={"leader": "1"})
    repository.set_state("run-1", RunState.PAUSED, reason="operator_pause")

    state = repository.load_resume_state("run-1", protocol)
    assert state.checkpoint_id == checkpoint_id
    assert state.next_ordinal == 2
    assert state.generation == 1
    assert state.payload == {"leader": "1"}

    repository.resume_run("run-1", protocol)
    assert repository.database.scalar("select state from deep_research_runs where run_id = 'run-1'") == "running"

    with pytest.raises(ValueError, match="protocol identity"):
        repository.load_resume_state("run-1", _protocol(dataset_hash="9" * 64))


def test_global_trial_identity_survives_run_restarts_and_counts_protocol_changes(tmp_path) -> None:
    database, repository = _repository(tmp_path)
    protocol = _protocol()
    repository.create_run("run-1", protocol)
    repository.create_run("run-2", protocol)
    repository.append_attempt("run-1", _attempt(1))
    repository.append_attempt("run-2", _attempt(1))

    restarted = database.frame("select global_trial_id from deep_research_trials order by run_id")
    assert restarted["global_trial_id"].nunique() == 1
    assert repository.global_trial_count(protocol) == 1

    changed = replace(protocol, code_hash="e" * 64)
    repository.create_run("run-3", changed)
    repository.append_attempt("run-3", _attempt(1))

    assert repository.global_trial_count(protocol) == 2
    assert repository.global_successful_fitness(protocol) == (1.0, 1.0)
