from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier, local

import pytest

from src.background_research import training
from src.background_research.data import read_learning_source
from src.background_research.scheduler import LearningScheduler
from src.background_research.training import LearningTrainer
from src.deep_research.candidates import generate_candidates
from src.deep_research.control import ControlState, ResearchControl
from src.deep_research.evaluation import CandidatePathEvidence
from src.research.round_two_quality import append_observations
from src.research.round_two_walkforward import EvaluationMetrics
from src.strategies.types import canonical_hash
from tests.unit.test_background_research_data import reserved


def phase_evaluator(batch, registry, *, final_return=0.1, crash=False):
    """Replace expensive arithmetic only; exercise real selection and durable seals."""
    calls = []

    def evaluate(payload):
        phase = (
            "training"
            if payload.evaluation_start == batch.training_start
            else ("validation" if payload.evaluation_start == batch.training_end else "holdout")
        )
        calls.append((phase, payload.candidate.payload()))
        if phase != "training":
            with registry._locked():
                state, _, _ = registry._read()
            selection = next(event.payload for event in state.events[batch.batch_id] if event.phase == "selection")
            assert selection["candidate_hash"] == payload.candidate.identity
            assert payload.candidate.kind == "baseline"
            if phase == "holdout":
                assert len(state.exposures) == 1
                if crash:
                    raise KeyboardInterrupt("crash after durable exposure")
        net = final_return if phase == "holdout" else 0.10
        metrics = EvaluationMetrics(
            gross_return=Decimal(str(net + 0.01)),
            net_return=Decimal(str(net)),
            stressed_net_return=Decimal(str(net - 0.01)),
            lower_edge=Decimal("0.001"),
            trade_count=200,
            maximum_drawdown=Decimal("0.01"),
            fees=Decimal("5"),
            spread_cost=Decimal("3"),
            slippage_cost=Decimal("2"),
            coverage=Decimal("1"),
        )
        rate = 0.02 if payload.candidate.kind == "baseline" else 0.0002
        fold = (rate, -0.0001, rate * 0.7, -0.0002) * 4
        return CandidatePathEvidence((fold,) * 4, fold * 4, (0.0001,) * 64, 200, metrics)

    return evaluate, calls


def seed_training_results(campaign, batch, registry):
    """Prior durable arithmetic fixture; later selection and sealing stay real."""
    for attempt in generate_candidates(
        campaign.search_spaces[0].to_search_space(), count=batch.max_attempts, seed=campaign.seed
    ):
        candidate = attempt.candidate
        payload = {"ordinal": attempt.ordinal, "generation": 1, "candidate": candidate.payload()}
        identity = f"{batch.batch_id}:{attempt.ordinal}"
        registry.append_event(
            batch.batch_id,
            {"kind": "attempt", "attempt_id": identity, "candidate_hash": candidate.identity, "payload": payload},
        )
        metrics = EvaluationMetrics(
            gross_return=Decimal("0.11"),
            net_return=Decimal("0.10"),
            stressed_net_return=Decimal("0.09"),
            lower_edge=Decimal("0.001"),
            trade_count=200,
            maximum_drawdown=Decimal("0.01"),
            fees=Decimal("5"),
            spread_cost=Decimal("3"),
            slippage_cost=Decimal("2"),
            coverage=Decimal("1"),
        )
        fold = (0.02, -0.0001, 0.01, -0.0002) * 4
        payload.update(
            fitness=10.0 if candidate.kind == "baseline" else 1.0,
            fold_returns=(fold,) * 4,
            gross_returns=fold * 4,
            costs=(0.0001,) * 64,
            metrics=metrics.model_dump(mode="json"),
        )
        registry.append_event(
            batch.batch_id,
            {
                "kind": "attempt_result",
                "attempt_id": identity,
                "candidate_hash": candidate.identity,
                "outcome": "completed",
                "payload": payload,
            },
        )


def test_all_attempts_and_failures_retained_without_source_writes_or_qualification(tmp_path):
    campaign, _, _, registry, batch = reserved(tmp_path, training_count=1440)
    before = {path.name: path.read_bytes() for path in campaign.source_directory.iterdir() if path.is_file()}
    control = ResearchControl(tmp_path / "control", run_id=batch.batch_id, nonce="t" * 32)
    control.initialize()
    events = []
    status = LearningTrainer(registry).run_batch(campaign, batch, control=control, emit=events.append)
    assert status.batch_attempt_count == 4
    assert status.state == "waiting"
    with registry._locked():
        state, _, _ = registry._read()
    retained = state.events[batch.batch_id]
    assert len([event for event in retained if event.kind == "attempt_result"]) == 4
    selection = next(event.payload for event in retained if event.phase == "selection")
    assert selection["candidate_hash"]
    validation = next(event.payload for event in retained if event.phase == "validation")
    assert "coverage_below_minimum" in validation["failed_gates"]
    assert any("insufficient_trades" in gate for gate in validation["failed_gates"])
    assert not state.exposures
    assert not any(event.phase == "proposal" for event in retained)
    assert before == {path.name: path.read_bytes() for path in campaign.source_directory.iterdir() if path.is_file()}
    again = LearningTrainer(registry).run_batch(campaign, batch, control=control, emit=events.append)
    assert again == status


def test_insufficient_training_waits_before_any_attempt_and_preserves_gaps(tmp_path):
    campaign, _, _, registry, batch = reserved(tmp_path)
    control = ResearchControl(tmp_path / "control", run_id=batch.batch_id, nonce="t" * 32)
    control.initialize()
    status = LearningTrainer(registry).run_batch(campaign, batch, control=control, emit=lambda _: None)
    assert status.state == "waiting"
    assert status.batch_attempt_count == 0
    with registry._locked():
        state, _, _ = registry._read()
    assert state.batch_finished(batch.batch_id)
    evidence = next(event.payload for event in state.events[batch.batch_id] if event.phase == "interruption")
    assert evidence["gaps"]
    assert "coverage_below_minimum" in evidence["reasons"]


@pytest.mark.parametrize("final_return", (0.9, -0.9))
def test_locked_literal_training_winner_survives_changed_final_returns_and_proposal_keeps_costs(
    tmp_path, monkeypatch, final_return
):
    campaign, _, _, registry, batch = reserved(tmp_path, training_count=1440)
    seed_training_results(campaign, batch, registry)
    evaluator, calls = phase_evaluator(batch, registry, final_return=final_return)
    monkeypatch.setattr(training, "evaluate_candidate_payload", evaluator)
    control = ResearchControl(tmp_path / "control", run_id=batch.batch_id, nonce="t" * 32)
    control.initialize()
    trainer = LearningTrainer(registry)
    assert trainer.run_batch(campaign, batch, control=control, emit=lambda _: None).state == "completed"
    assert [phase for phase, _ in calls][-2:] == ["validation", "holdout"]
    with registry._locked():
        state, _, _ = registry._read()
    proposal = next(event.payload for event in state.events[batch.batch_id] if event.phase == "proposal")
    assert proposal["candidate"] == {
        "kind": "baseline",
        "strategy_id": "desk_donchian_breakout_1m",
        "parameters": {"lookback": 2},
        "rule": None,
    }
    assert proposal["holdout"]["net_return"] == str(final_return)
    assert proposal["holdout"]["fees"] == "5"
    assert proposal["holdout"]["spread_cost"] == "3"
    assert proposal["holdout"]["slippage_cost"] == "2"
    assert proposal["holdout"]["trade_count"] == 200
    assert proposal["qualification_status"] == "unqualified"
    assert proposal["failed_gates"]  # Existing reliability gates cannot be discarded.
    before = len(calls)
    trainer.run_batch(campaign, batch, control=control, emit=lambda _: None)
    assert len(calls) == before


def test_crash_after_holdout_reservation_burns_interval_and_never_repeats_evaluation(tmp_path, monkeypatch):
    campaign, _, _, registry, batch = reserved(tmp_path, training_count=1440)
    seed_training_results(campaign, batch, registry)
    evaluator, calls = phase_evaluator(batch, registry, crash=True)
    monkeypatch.setattr(training, "evaluate_candidate_payload", evaluator)
    control = ResearchControl(tmp_path / "control", run_id=batch.batch_id, nonce="t" * 32)
    control.initialize()
    with pytest.raises(KeyboardInterrupt, match="durable exposure"):
        LearningTrainer(registry).run_batch(campaign, batch, control=control, emit=lambda _: None)
    assert sum(phase == "holdout" for phase, _ in calls) == 1
    status = LearningTrainer(registry).run_batch(campaign, batch, control=control, emit=lambda _: None)
    assert status.state == "waiting"
    assert sum(phase == "holdout" for phase, _ in calls) == 1
    assert status.batch_attempt_count == 4
    with registry._locked():
        state, _, _ = registry._read()
    assert len(state.exposures) == 1
    assert state.batch_finished(batch.batch_id)
    assert not any(event.phase == "proposal" for event in state.events[batch.batch_id])


def test_two_generations_consume_100_attempts_including_duplicates_and_resume_without_new_data(tmp_path, monkeypatch):
    campaign, _, _, registry, batch = reserved(tmp_path, training_count=1440, attempts=100)
    control = ResearchControl(tmp_path / "control", run_id=batch.batch_id, nonce="t" * 32)
    control.initialize()
    status = LearningTrainer(registry).run_batch(campaign, batch, control=control, emit=lambda _: None)
    assert status.batch_attempt_count == 100
    with registry._locked():
        state, _, _ = registry._read()
    attempts = [event for event in state.events[batch.batch_id] if event.kind == "attempt"]
    assert [event.payload["generation"] for event in attempts] == [1] * 50 + [2] * 50
    assert any(event.outcome == "rejected" for event in state.events[batch.batch_id])
    results = [event for event in state.events[batch.batch_id] if event.kind == "attempt_result"]
    assert len(results) == 100
    assert 1 < sum(event.outcome == "completed" for event in results) < 100
    assert (
        LearningScheduler(registry).next_batch(
            campaign,
            symbol="BTCUSDT",
            data_fingerprint=batch.data_fingerprint,
            through=batch.created_at + timedelta(days=1),
            now=batch.created_at + timedelta(days=1),
        )
        is None
    )


def test_parent_result_persistence_crash_resumes_same_attempts_without_dispatching_again(tmp_path, monkeypatch):
    campaign, _, _, registry, batch = reserved(tmp_path, training_count=1440)
    control = ResearchControl(tmp_path / "control", run_id=batch.batch_id, nonce="t" * 32)
    control.initialize()
    append = registry.append_event

    def crash_after_write(batch_id, event):
        append(batch_id, event)
        if event["kind"] == "attempt_result" and event["outcome"] == "completed":
            raise KeyboardInterrupt("parent crashed after durable result")

    monkeypatch.setattr(registry, "append_event", crash_after_write)
    with pytest.raises(KeyboardInterrupt, match="durable result"):
        LearningTrainer(registry).run_batch(campaign, batch, control=control, emit=lambda _: None)
    monkeypatch.setattr(registry, "append_event", append)
    with registry._locked():
        before, _, _ = registry._read()
    original_ids = [event.attempt_id for event in before.events[batch.batch_id] if event.kind == "attempt"]
    status = LearningTrainer(registry).run_batch(campaign, batch, control=control, emit=lambda _: None)
    assert status.batch_attempt_count == 4
    with registry._locked():
        after, _, _ = registry._read()
    assert [event.attempt_id for event in after.events[batch.batch_id] if event.kind == "attempt"] == original_ids
    assert sum(event.outcome == "completed" for event in after.events[batch.batch_id]) == 1
    assert any(event.outcome == "interrupted" for event in after.events[batch.batch_id])


def test_stopped_execution_resumes_original_batch_only_with_fresh_control(tmp_path, monkeypatch):
    campaign, _, _, registry, batch = reserved(tmp_path, training_count=1440)
    monkeypatch.setattr(training.os, "cpu_count", lambda: 3)
    control = ResearchControl(tmp_path / "control", run_id="execution-one", nonce="t" * 32)
    control.initialize()

    def stop_after_result(event):
        if event["stage"] == "search":
            control.request(ControlState.STOPPED)

    trainer = LearningTrainer(registry)
    assert trainer.run_batch(campaign, batch, control=control, emit=stop_after_result).state == "paused"
    directory = registry.root / "batches" / canonical_hash(batch.batch_id)
    original_db = directory / f"training-{canonical_hash(control.run_id)}.duckdb"
    original_bytes = original_db.read_bytes()
    original_control = control.path.read_bytes()
    with registry._locked():
        before, _, _ = registry._read()
    original_ids = [event.attempt_id for event in before.events[batch.batch_id] if event.kind == "attempt"]
    assert len(original_ids) == 4
    assert sum(event.outcome == "completed" for event in before.events[batch.batch_id]) == 1
    with pytest.raises(ValueError, match="terminal.*fresh execution"):
        trainer.run_batch(campaign, batch, control=control, emit=lambda _: None)
    registry.append_event(batch.batch_id, {"kind": "state", "state": "training", "reason": "explicit relaunch"})
    fresh = ResearchControl(tmp_path / "control", run_id="execution-two", nonce="u" * 32)
    fresh.initialize()
    status = trainer.run_batch(campaign, batch, control=fresh, emit=lambda _: None)
    assert status.state == "waiting"
    assert status.batch_attempt_count == 4
    assert original_db.read_bytes() == original_bytes
    assert control.path.read_bytes() == original_control
    assert control.read() is ControlState.STOPPED
    with registry._locked():
        after, _, _ = registry._read()
    assert [event.attempt_id for event in after.events[batch.batch_id] if event.kind == "attempt"] == original_ids
    assert sum(event.outcome == "completed" for event in after.events[batch.batch_id]) == 1
    assert sum(event.outcome == "interrupted" for event in after.events[batch.batch_id]) == 3


def test_paused_control_can_resume_same_execution_after_explicit_authorization(tmp_path):
    campaign, _, _, registry, batch = reserved(tmp_path)
    control = ResearchControl(tmp_path / "control", run_id="paused-execution", nonce="t" * 32)
    control.initialize()
    control.request(ControlState.PAUSED)
    trainer = LearningTrainer(registry)
    assert trainer.run_batch(campaign, batch, control=control, emit=lambda _: None).state == "paused"
    control.request(ControlState.RUNNING)
    assert trainer.run_batch(campaign, batch, control=control, emit=lambda _: None).state == "paused"
    registry.append_event(batch.batch_id, {"kind": "state", "state": "training", "reason": "explicit resume"})
    status = trainer.run_batch(campaign, batch, control=control, emit=lambda _: None)
    assert status.state == "waiting"
    assert status.batch_attempt_count == 0


def test_renamed_campaign_cannot_evaluate_an_overlapping_final_interval(tmp_path, monkeypatch):
    campaign, protocol, observations, registry, batch = reserved(tmp_path, training_count=1440)
    registry.reserve_holdout(batch.batch_id, "d" * 64)
    registry.append_event(batch.batch_id, {"kind": "completion", "state": "waiting", "reason": "exposed"})
    now = batch.created_at + timedelta(days=1)
    row = observations[-1].model_copy(
        update={"source_key": "later", "provider_at": now, "received_at": now, "available_at": now}
    )
    append_observations(campaign.source_directory, protocol, (row,))
    renamed = campaign.model_copy(update={"campaign_id": "renamed", "created_at": now})
    registry.register(renamed)
    source = read_learning_source(renamed, now=now)
    other = LearningScheduler(registry).next_batch(
        renamed, symbol="BTCUSDT", data_fingerprint=source.data_fingerprint, through=now, now=now
    )
    assert other.validation_end == batch.validation_end
    seed_training_results(renamed, other, registry)
    evaluator, calls = phase_evaluator(other, registry)
    monkeypatch.setattr(training, "evaluate_candidate_payload", evaluator)
    control = ResearchControl(tmp_path / "other-control", run_id=other.batch_id, nonce="t" * 32)
    control.initialize()
    status = LearningTrainer(registry).run_batch(renamed, other, control=control, emit=lambda _: None)
    assert status.state == "waiting"
    assert status.attempt_count == 4
    assert [phase for phase, _ in calls] == ["validation"]
    with registry._locked():
        state, _, _ = registry._read()
    assert len(state.exposures) == 1


def test_simultaneous_workers_recheck_completion_after_waiting_for_batch_lock(tmp_path, monkeypatch):
    campaign, _, _, registry, batch = reserved(tmp_path)
    control = ResearchControl(tmp_path / "control", run_id=batch.batch_id, nonce="t" * 32)
    control.initialize()
    trainer = LearningTrainer(registry)
    original, barrier, thread = trainer._state, Barrier(2), local()

    def synchronized_initial_read():
        state = original()
        if not getattr(thread, "read", False):
            thread.read = True
            barrier.wait(timeout=10)
        return state

    monkeypatch.setattr(trainer, "_state", synchronized_initial_read)
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [
            executor.submit(trainer.run_batch, campaign, batch, control=control, emit=lambda _: None) for _ in range(2)
        ]
        statuses = [job.result(timeout=20) for job in jobs]
    assert statuses[0] == statuses[1]
    assert statuses[0].state == "waiting"


def test_linked_training_database_cannot_redirect_worker_writes(tmp_path):
    campaign, _, _, registry, batch = reserved(tmp_path)
    directory = registry.root / "batches" / canonical_hash(batch.batch_id)
    directory.mkdir(parents=True)
    source = campaign.source_directory / "protocol.json"
    before = source.read_bytes()
    (directory / f"training-{canonical_hash(batch.batch_id)}.duckdb").symlink_to(source)
    control = ResearchControl(tmp_path / "control", run_id=batch.batch_id, nonce="t" * 32)
    control.initialize()
    with pytest.raises(ValueError, match="linked"):
        LearningTrainer(registry).run_batch(campaign, batch, control=control, emit=lambda _: None)
    assert source.read_bytes() == before


def test_out_of_bounds_generated_attempt_consumes_budget_and_remains_invalid(tmp_path):
    campaign, _, _, registry, batch = reserved(tmp_path, training_count=1440, maximum_lag=0)
    control = ResearchControl(tmp_path / "control", run_id=batch.batch_id, nonce="t" * 32)
    control.initialize()
    status = LearningTrainer(registry).run_batch(campaign, batch, control=control, emit=lambda _: None)
    with registry._locked():
        state, _, _ = registry._read()
    results = [event for event in state.events[batch.batch_id] if event.kind == "attempt_result"]
    assert status.batch_attempt_count == 4
    assert len(results) == 4
    assert any(event.outcome == "invalid" and "lag" in event.payload["error"] for event in results)


def test_missing_validation_history_finishes_waiting_without_exposure(tmp_path):
    campaign, _, _, registry, batch = reserved(tmp_path, training_count=1440, observations_per_day=0)
    seed_training_results(campaign, batch, registry)
    control = ResearchControl(tmp_path / "control", run_id=batch.batch_id, nonce="t" * 32)
    control.initialize()
    status = LearningTrainer(registry).run_batch(campaign, batch, control=control, emit=lambda _: None)
    assert status.state == "waiting"
    assert "no_available_observations" in status.reason
    with registry._locked():
        state, _, _ = registry._read()
    assert not state.exposures
    assert state.batch_finished(batch.batch_id)
