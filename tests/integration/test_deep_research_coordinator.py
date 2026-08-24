from __future__ import annotations

from datetime import UTC, datetime
from threading import Timer

from src.database.engine import Database
from src.deep_research.contracts import ResearchProtocol, RunState
from src.deep_research.control import ControlState, ResearchControl
from src.deep_research.coordinator import CandidateWork, DeepResearchCoordinator
from src.deep_research.repository import DeepResearchRepository

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _protocol(*, workers: int, trial_budget: int = 4, continuous: bool = False) -> ResearchProtocol:
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
        workers=workers,
        trial_budget=None if continuous else trial_budget,
        continuous=continuous,
        cycle_budget=trial_budget,
        final_test_start=NOW,
        created_at=NOW,
    )


def _work(ordinal: int, *, failures_before_success: int = 0, duplicate_of: int | None = None) -> CandidateWork:
    base = 0.002 + ordinal * 0.0001
    fold = tuple([base, -0.0002, base * 0.8, 0.0001] * 20)
    return CandidateWork(
        ordinal=ordinal,
        candidate_hash=f"{ordinal:064x}",
        definition={"ordinal": ordinal},
        fold_returns=(fold, fold, fold, fold),
        gross_returns=fold * 4,
        costs=tuple([0.0001] * (len(fold) * 4)),
        failures_before_success=failures_before_success,
        duplicate_of=duplicate_of,
        delay_seconds=0.01 * (5 - ordinal),
    )


def _run(tmp_path, *, workers: int, works: tuple[CandidateWork, ...], run_id: str):
    database = Database.from_url(f"duckdb:///{tmp_path / f'{run_id}.duckdb'}")
    database.initialize()
    repository = DeepResearchRepository(database, clock=lambda: NOW)
    control = ResearchControl(tmp_path / run_id, run_id=run_id, nonce="n" * 32)
    control.initialize()
    outcome = DeepResearchCoordinator(
        run_id=run_id,
        protocol=_protocol(workers=workers, trial_budget=len(works)),
        repository=repository,
        control=control,
        sealed_evaluator=lambda work: tuple([0.002 + work.ordinal * 0.0001, -0.0001] * 160),
    ).run(works)
    return database, outcome


def test_parallel_completion_is_committed_in_ordinal_order_and_worker_count_independent(tmp_path) -> None:
    works = tuple(_work(ordinal) for ordinal in range(1, 5))
    single_database, single = _run(tmp_path, workers=1, works=works, run_id="single")
    parallel_database, parallel = _run(tmp_path, workers=4, works=works, run_id="parallel")

    assert single.fitness_by_ordinal == parallel.fitness_by_ordinal
    for database, run_id in ((single_database, "single"), (parallel_database, "parallel")):
        rows = database.frame(
            "select ordinal from deep_research_trials where run_id = :run_id order by persisted_sequence",
            {"run_id": run_id},
        )
        assert rows["ordinal"].tolist() == [1, 2, 3, 4]
    assert set(parallel.worker_thread_limits) == {"1"}


def test_worker_retries_once_then_records_repeat_failure_without_hiding_the_trial(tmp_path) -> None:
    database, outcome = _run(
        tmp_path,
        workers=2,
        works=(_work(1, failures_before_success=1), _work(2, failures_before_success=2)),
        run_id="retry",
    )

    rows = database.frame("select ordinal, status from deep_research_trials order by ordinal")
    assert rows.to_dict("records") == [
        {"ordinal": 1, "status": "succeeded"},
        {"ordinal": 2, "status": "failed"},
    ]
    assert outcome.evaluated_attempts == 2


def test_duplicate_attempt_is_counted_without_worker_evaluation(tmp_path) -> None:
    database, _ = _run(
        tmp_path,
        workers=2,
        works=(_work(1), _work(2, duplicate_of=1)),
        run_id="duplicates",
    )
    rows = database.frame("select ordinal, status from deep_research_trials order by ordinal")
    assert rows.to_dict("records") == [
        {"ordinal": 1, "status": "succeeded"},
        {"ordinal": 2, "status": "duplicate"},
    ]


def test_sealed_evidence_is_requested_once_and_only_for_the_frozen_winner(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'sealed.duckdb'}")
    database.initialize()
    repository = DeepResearchRepository(database, clock=lambda: NOW)
    control = ResearchControl(tmp_path / "sealed", run_id="sealed", nonce="n" * 32)
    control.initialize()
    inspected: list[int] = []

    def sealed_evaluator(work: CandidateWork) -> tuple[float, ...]:
        inspected.append(work.ordinal)
        return tuple([0.003, -0.0001] * 160)

    outcome = DeepResearchCoordinator(
        run_id="sealed",
        protocol=_protocol(workers=2, trial_budget=3),
        repository=repository,
        control=control,
        sealed_evaluator=sealed_evaluator,
    ).run((_work(1), _work(2), _work(3)))

    assert inspected == [3]
    assert outcome.best_candidate_hash == f"{3:064x}"


def test_pre_requested_stop_preserves_a_resumable_checkpoint_and_never_touches_broker_ledgers(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'stopped.duckdb'}")
    database.initialize()
    repository = DeepResearchRepository(database, clock=lambda: NOW)
    protocol = _protocol(workers=2, trial_budget=2)
    control = ResearchControl(tmp_path / "control", run_id="stopped", nonce="n" * 32)
    control.initialize()
    control.request(ControlState.STOPPED)

    outcome = DeepResearchCoordinator(
        run_id="stopped",
        protocol=protocol,
        repository=repository,
        control=control,
        sealed_evaluator=lambda _: (0.01,),
    ).run((_work(1), _work(2)))

    assert outcome.state is RunState.STOPPED
    assert database.scalar("select count(*) from deep_research_checkpoints") == 1
    assert database.scalar("select count(*) from broker_order_intents") == 0
    assert database.scalar("select count(*) from broker_orders") == 0


def test_stop_during_search_drains_only_the_active_bounded_batch_and_checkpoints_completed_work(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'mid-stop.duckdb'}")
    database.initialize()
    repository = DeepResearchRepository(database, clock=lambda: NOW)
    protocol = _protocol(workers=1, trial_budget=4)
    control = ResearchControl(tmp_path / "mid-stop", run_id="mid-stop", nonce="n" * 32)
    control.initialize()
    timer = Timer(0.02, lambda: control.request(ControlState.STOPPED))
    timer.start()
    try:
        outcome = DeepResearchCoordinator(
            run_id="mid-stop",
            protocol=protocol,
            repository=repository,
            control=control,
            sealed_evaluator=lambda _: (0.01,),
        ).run(tuple(_work(ordinal) for ordinal in range(1, 5)))
    finally:
        timer.cancel()

    assert outcome.state is RunState.STOPPED
    assert outcome.evaluated_attempts == 1
    assert database.scalar("select count(*) from deep_research_trials") == 1
    assert database.scalar("select max(next_ordinal) from deep_research_checkpoints") == 2


def test_continuous_run_resumes_at_the_checkpointed_ordinal_and_generation(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'resume-cycle.duckdb'}")
    database.initialize()
    repository = DeepResearchRepository(database)
    protocol = _protocol(workers=2, trial_budget=2, continuous=True)
    first_control = ResearchControl(tmp_path / "resume-first", run_id="resume-cycle", nonce="a" * 32)
    first_control.initialize()
    first = DeepResearchCoordinator(
        run_id="resume-cycle",
        protocol=protocol,
        repository=repository,
        control=first_control,
        sealed_evaluator=lambda work: tuple([0.002 + work.ordinal * 0.0001, -0.0001] * 160),
    ).run((_work(1), _work(2)), generation=1, finish_run=False)
    assert first.state is RunState.RUNNING

    resume = repository.resume_run("resume-cycle", protocol)
    assert (resume.next_ordinal, resume.generation) == (3, 2)
    second_control = ResearchControl(tmp_path / "resume-second", run_id="resume-cycle", nonce="b" * 32)
    second_control.initialize()
    second = DeepResearchCoordinator(
        run_id="resume-cycle",
        protocol=protocol,
        repository=repository,
        control=second_control,
        sealed_evaluator=lambda work: tuple([0.002 + work.ordinal * 0.0001, -0.0001] * 160),
    ).run((_work(3), _work(4)), generation=resume.generation, create_run=False, finish_run=True)

    assert second.state is RunState.COMPLETED
    rows = database.frame("select ordinal, generation from deep_research_trials order by ordinal")
    assert rows.to_dict("records") == [
        {"ordinal": 1, "generation": 1},
        {"ordinal": 2, "generation": 1},
        {"ordinal": 3, "generation": 2},
        {"ordinal": 4, "generation": 2},
    ]
