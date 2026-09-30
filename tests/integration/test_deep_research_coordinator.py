from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from threading import Timer

import pytest

from src.database.engine import Database
from src.deep_research.contracts import ResearchProtocol, RunState
from src.deep_research.control import ControlState, ResearchControl
from src.deep_research.coordinator import (
    CandidateWork,
    DeepResearchCoordinator,
    recommended_worker_count,
    resource_preemption_reason,
)
from src.deep_research.repository import DeepResearchRepository

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


class CountingRepository(DeepResearchRepository):
    def promotion_count(self):
        return self.database.scalar("select count(*) from deep_research_promotions")


def test_training_only_never_promotes_or_reads_final(tmp_path):
    database = Database.from_url(f"duckdb:///{tmp_path / 'training.duckdb'}")
    database.initialize()
    repository = CountingRepository(database)
    control = ResearchControl(tmp_path / "training", run_id="training", nonce="t" * 32)
    control.initialize()

    def forbidden(*args):
        raise AssertionError("training must not inspect final or evaluate promotion")

    coordinator = DeepResearchCoordinator(
        run_id="training",
        protocol=_protocol(workers=1, trial_budget=2, continuous=True),
        repository=repository,
        control=control,
        sealed_evaluator=forbidden,
    )
    coordinator._promotion = forbidden
    # Equal fitness: canonical hash, not dispatch ordinal, chooses the winner.
    works = (_work(1), replace(_work(1), ordinal=2, candidate_hash="0" * 64))
    result = coordinator.run(works, evaluate_final=False)
    assert result.promotion_outcome == "training_only"
    assert result.best_candidate_hash == "0" * 64
    assert repository.promotion_count() == 0
    assert database.scalar("select count(*) from deep_research_trials") == 2


def test_continuous_protocol_rejects_final_evaluation_before_run_creation(tmp_path):
    database = Database.from_url(f"duckdb:///{tmp_path / 'forbidden.duckdb'}")
    database.initialize()
    control = ResearchControl(tmp_path / "forbidden", run_id="forbidden", nonce="f" * 32)
    control.initialize()
    coordinator = DeepResearchCoordinator(
        run_id="forbidden",
        protocol=_protocol(workers=1, continuous=True),
        repository=CountingRepository(database),
        control=control,
        sealed_evaluator=lambda _: (1.0,),
    )
    with pytest.raises(ValueError, match="continuous.*training"):
        coordinator.run((_work(1),), evaluate_final=True)
    assert database.scalar("select count(*) from deep_research_runs") == 0


def test_worker_recommendation_always_reserves_two_processors_and_honors_ceiling() -> None:
    assert recommended_worker_count(12) == 10
    assert recommended_worker_count(12, configured_max=4) == 4
    assert recommended_worker_count(2) == 1
    assert recommended_worker_count(1) == 1


def test_resource_preemption_reasons_prioritize_live_safety() -> None:
    assert resource_preemption_reason(live_heartbeat_healthy=False) == "live_heartbeat_unhealthy"
    assert resource_preemption_reason(thermal_state="critical") == "thermal_critical"
    assert resource_preemption_reason(memory_pressure=True) == "memory_pressure"
    assert resource_preemption_reason(low_disk_space=True) == "low_disk_space"
    assert resource_preemption_reason(sleeping=True) == "system_sleep"
    assert resource_preemption_reason() is None


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


@pytest.mark.parametrize("boundary", ["initial", "retry"])
def test_ownership_loss_prevents_every_submission_and_leaves_replacement_owner_intact(tmp_path, monkeypatch, boundary):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import ExitStack

    from src.background_research.runtime import OwnershipLostError, _exclusive, _OwnedControl
    from src.deep_research import coordinator as module

    database = Database.from_url(f"duckdb:///{tmp_path / 'ownership.duckdb'}")
    database.initialize()
    control = ResearchControl(tmp_path / "control", run_id="owner", nonce="n" * 32)
    control.initialize()
    lock = tmp_path / "campaign.lock"
    calls = []
    original_evaluate = module.evaluate_candidate_work
    with ExitStack() as owners:
        verify = owners.enter_context(_exclusive(lock))
        replacement = []

        def lose():
            lock.unlink()
            replacement.append(owners.enter_context(_exclusive(lock)))

        def evaluate(work, attempt):
            calls.append((work.ordinal, attempt))
            try:
                return original_evaluate(work, attempt)
            finally:
                if boundary == "retry" and (work.ordinal, attempt) == (1, 1):
                    lose()  # The real first evaluation fails before the retry boundary.

        class Executor(ThreadPoolExecutor):
            def submit(self, fn, work, attempt):
                future = super().submit(fn, work, attempt)
                if boundary == "initial" and work.ordinal == 1:
                    future.result(timeout=5)
                    lose()  # Authority disappears between two initial submissions.
                return future

        monkeypatch.setattr(module, "ProcessPoolExecutor", Executor)
        monkeypatch.setattr(module, "evaluate_candidate_work", evaluate)
        works = (_work(1), _work(2)) if boundary == "initial" else (_work(1, failures_before_success=1),)
        coordinator = DeepResearchCoordinator(
            run_id="owner",
            protocol=_protocol(workers=2, trial_budget=len(works)),
            repository=DeepResearchRepository(database),
            control=_OwnedControl(control, verify),
            sealed_evaluator=lambda _: (),
        )
        with pytest.raises(OwnershipLostError):
            coordinator.run(works, evaluate_final=False)
        assert calls == [(1, 1)]
        replacement[0]()
        assert database.scalar("select count(*) from deep_research_trials") == 0


@pytest.mark.parametrize("boundary", ["initial", "retry"])
@pytest.mark.parametrize("requested", [ControlState.PAUSED, ControlState.STOPPED])
def test_each_submission_honors_pause_and_stop_without_losing_completed_work(
    tmp_path, monkeypatch, boundary, requested
):
    from concurrent.futures import ThreadPoolExecutor

    from src.deep_research import coordinator as module

    database = Database.from_url(f"duckdb:///{tmp_path / 'submission-control.duckdb'}")
    database.initialize()
    control = ResearchControl(tmp_path / "control", run_id="dispatch", nonce="n" * 32)
    control.initialize()
    calls, paused = [], []
    original_evaluate, original_wait = module.evaluate_candidate_work, control.wait_until_runnable

    def evaluate(work, attempt):
        calls.append((work.ordinal, attempt))
        try:
            return original_evaluate(work, attempt)
        finally:
            if boundary == "retry" and attempt == 1:
                control.request(requested)

    class Executor(ThreadPoolExecutor):
        def submit(self, fn, work, attempt):
            future = super().submit(fn, work, attempt)
            if boundary == "initial" and work.ordinal == 1:
                future.result(timeout=5)
                control.request(requested)
            return future

    def resume_when_waiting(**kwargs):
        if control.read() is ControlState.PAUSED:
            assert calls == [(1, 1)]
            paused.append(True)
            control.request(ControlState.RUNNING)
        return original_wait(**kwargs)

    monkeypatch.setattr(module, "ProcessPoolExecutor", Executor)
    monkeypatch.setattr(module, "evaluate_candidate_work", evaluate)
    monkeypatch.setattr(control, "wait_until_runnable", resume_when_waiting)
    works = (_work(1), _work(2)) if boundary == "initial" else (_work(1, failures_before_success=1),)
    outcome = DeepResearchCoordinator(
        run_id="dispatch",
        protocol=_protocol(workers=2, trial_budget=len(works)),
        repository=DeepResearchRepository(database),
        control=control,
        sealed_evaluator=lambda _: (),
    ).run(works, evaluate_final=False)
    if requested is ControlState.PAUSED:
        assert paused == [True]
        assert calls == ([(1, 1), (2, 1)] if boundary == "initial" else [(1, 1), (1, 2)])
    else:
        assert calls == [(1, 1)]
        assert outcome.state is RunState.STOPPED
        assert database.scalar("select count(*) from deep_research_trials") == 1
        assert database.scalar("select max(next_ordinal) from deep_research_checkpoints") == 2


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
    ).run((_work(1), _work(2)), generation=1, finish_run=False, evaluate_final=False)
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
    ).run((_work(3), _work(4)), generation=resume.generation, create_run=False, finish_run=True, evaluate_final=False)

    assert second.state is RunState.COMPLETED
    rows = database.frame("select ordinal, generation from deep_research_trials order by ordinal")
    assert rows.to_dict("records") == [
        {"ordinal": 1, "generation": 1},
        {"ordinal": 2, "generation": 1},
        {"ordinal": 3, "generation": 2},
        {"ordinal": 4, "generation": 2},
    ]


def test_resource_guard_pauses_with_checkpoint_before_worker_dispatch_and_preserves_broker_isolation(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'resource-guard.duckdb'}")
    database.initialize()
    repository = DeepResearchRepository(database, clock=lambda: NOW)
    control = ResearchControl(tmp_path / "guard", run_id="guard", nonce="g" * 32)
    control.initialize()
    outcome = DeepResearchCoordinator(
        run_id="guard",
        protocol=_protocol(workers=2, trial_budget=2),
        repository=repository,
        control=control,
        sealed_evaluator=lambda _: (0.01,),
        resource_guard=lambda _works, _workers, _directory: (False, "memory reserve exhausted", 1234),
    ).run((_work(1), _work(2)))

    assert outcome.state is RunState.PAUSED
    assert outcome.promotion_outcome == "resource_preempted"
    assert database.scalar("select count(*) from deep_research_trials") == 0
    assert database.scalar("select state from deep_research_runs") == "paused"
    assert database.scalar("select terminal_reason from deep_research_runs") == "memory reserve exhausted"
    assert database.scalar("select count(*) from deep_research_checkpoints") == 1
    assert database.scalar("select count(*) from broker_order_intents") == 0


def test_resource_deterioration_between_batches_preempts_new_work_and_keeps_completed_trials(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'mid-resource.duckdb'}")
    database.initialize()
    repository = DeepResearchRepository(database, clock=lambda: NOW)
    control = ResearchControl(tmp_path / "mid-resource", run_id="mid-resource", nonce="r" * 32)
    control.initialize()
    calls = 0

    def guard(_works, _workers, _directory):
        nonlocal calls
        calls += 1
        return (calls < 3, "live heartbeat unavailable" if calls >= 3 else "available", 100)

    outcome = DeepResearchCoordinator(
        run_id="mid-resource",
        protocol=_protocol(workers=1, trial_budget=3),
        repository=repository,
        control=control,
        sealed_evaluator=lambda _: (0.01,),
        resource_guard=guard,
    ).run((_work(1), _work(2), _work(3)))

    assert outcome.state is RunState.PAUSED
    assert outcome.evaluated_attempts == 1
    assert database.scalar("select count(*) from deep_research_trials") == 1
    assert database.scalar("select max(next_ordinal) from deep_research_checkpoints") == 2
