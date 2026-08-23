from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from src.database.engine import Database
from src.learning.grammar import RuleNode
from src.learning.promotion import ForwardEvidence, promote_candidate
from src.learning.search import (
    FitnessPenalties,
    FoldMetrics,
    LearningExperiment,
    RuleCandidate,
    discover_rules,
)
from src.strategies.types import BarInterval
from src.strategies.validation import PromotionDecision, WalkForwardFold


def _bars() -> pd.DataFrame:
    timestamps = pd.date_range("2026-08-20T09:00:00Z", periods=10, freq="h")
    return pd.DataFrame(
        {
            "decision_timestamp": timestamps,
            "available_at": timestamps,
            "outcome_available_at": timestamps + pd.Timedelta(minutes=30),
            "finalized": True,
            "rsi": [20, 30, 40, 50, 60, 70, 80, 70, 50, 30],
            "volume": [80, 90, 100, 110, 120, 130, 120, 110, 100, 90],
            "forward_return": [0.01, -0.002, 0.012, -0.003, 0.008] * 2,
        }
    )


def _experiment(database: Database, evaluator=None, **changes: object) -> LearningExperiment:
    values: dict[str, object] = {
        "learning_run_id": "durable-learning-1",
        "dataset_hash": "a" * 64,
        "symbol": "AAA",
        "interval": BarInterval.ONE_HOUR,
        "started_at": datetime(2026, 8, 20, 19, tzinfo=UTC),
        "as_of": datetime(2026, 8, 20, 20, tzinfo=UTC),
        "sealed_final_start": datetime(2026, 8, 21, tzinfo=UTC),
        "seed": 19,
        "evaluation_budget": 4,
        "inner_folds": (
            WalkForwardFold(tuple(range(4)), (4, 5)),
            WalkForwardFold(tuple(range(6)), (6, 7)),
        ),
        "indicators": ("rsi", "volume"),
        "thresholds": (30.0, 50.0, 70.0, 100.0),
        "database": database,
        "evaluator": evaluator,
    }
    values.update(changes)
    return LearningExperiment(**values)


def _good_metrics(_: RuleCandidate, __: pd.DataFrame, ___: pd.DataFrame) -> FoldMetrics:
    return FoldMetrics(net_sharpe=1.0, maximum_drawdown=-0.1, turnover=0.2)


def test_experiment_resume_is_idempotent_and_uses_the_persisted_ledger(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'resume.duckdb'}")
    database.initialize()
    calls = 0

    def evaluator(*args) -> FoldMetrics:
        nonlocal calls
        calls += 1
        return _good_metrics(*args)

    experiment = _experiment(database, evaluator)
    first = discover_rules(experiment, _bars())
    calls_after_first = calls
    second = discover_rules(experiment, _bars())

    assert first.trials == second.trials
    assert calls_after_first == 8
    assert calls == calls_after_first
    assert database.scalar(
        "select count(*) from learning_trials where learning_run_id = :run_id",
        {"run_id": experiment.learning_run_id},
    ) == first.trial_count == 4
    assert database.scalar(
        "select count(*) from discovered_rules where learning_run_id = :run_id",
        {"run_id": experiment.learning_run_id},
    ) == 1


def test_evaluator_failures_are_append_only_ledger_rows_and_count_as_trials(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'failure.duckdb'}")
    database.initialize()

    def evaluator(*_) -> FoldMetrics:
        raise RuntimeError("cost engine unavailable")

    result = discover_rules(_experiment(database, evaluator, evaluation_budget=3), _bars())
    persisted = database.frame(
        "select status, error_summary from learning_trials where learning_run_id = :run_id order by evaluated_at",
        {"run_id": result.learning_run_id},
    )

    assert result.trial_count == len(persisted) == 3
    assert persisted["status"].tolist() == ["failed"] * 3
    assert persisted["error_summary"].str.contains("cost engine unavailable").all()
    assert database.scalar(
        "select count(*) from discovered_rules where learning_run_id = :run_id",
        {"run_id": result.learning_run_id},
    ) == 0


def test_invalid_candidate_query_is_persisted_and_cannot_bypass_grammar_caps(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'invalid.duckdb'}")
    database.initialize()
    calls = 0

    def evaluator(*_) -> FoldMetrics:
        nonlocal calls
        calls += 1
        return FoldMetrics(1.0, -0.1, 0.2)

    atom = RuleNode.compare("gt", RuleNode.indicator("rsi", lag=1), RuleNode.number(50))
    too_deep = RuleNode.negate(RuleNode.negate(atom))
    result = discover_rules(
        _experiment(
            database,
            evaluator,
            evaluation_budget=1,
            max_depth=3,
            seed_rules=(too_deep,),
        ),
        _bars(),
    )

    assert result.trial_count == 1
    assert result.trials[0].status == "invalid"
    assert "depth" in result.trials[0].error_summary
    assert calls == 0
    assert database.scalar(
        "select count(*) from learning_trials where status = 'invalid' and learning_run_id = :run_id",
        {"run_id": result.learning_run_id},
    ) == 1


def test_candidate_space_exhaustion_fills_fixed_budget_with_budget_stop_rows(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'budget-stop.duckdb'}")
    database.initialize()
    result = discover_rules(
        _experiment(
            database,
            _good_metrics,
            evaluation_budget=5,
            indicators=("rsi",),
            thresholds=(50.0,),
            maximum_lag=1,
        ),
        _bars(),
    )

    assert result.trial_count == 5
    assert [trial.status for trial in result.trials] == [
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
        "budget_stop",
    ]
    assert database.scalar(
        "select count(*) from learning_trials where learning_run_id = :run_id",
        {"run_id": result.learning_run_id},
    ) == 5


def test_resume_authenticates_the_complete_search_contract(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'contract.duckdb'}")
    database.initialize()
    experiment = _experiment(database, _good_metrics, evaluation_budget=1)
    discover_rules(experiment, _bars())

    changed = replace(experiment, penalties=FitnessPenalties(complexity=0.2))

    with pytest.raises(ValueError, match="search contract"):
        discover_rules(changed, _bars())

    changed_evaluator = replace(experiment, evaluator_version="2")
    with pytest.raises(ValueError, match="search contract"):
        discover_rules(changed_evaluator, _bars())


def test_discovered_rules_are_immutable_run_versioned_shadow_candidates(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'versions.duckdb'}")
    database.initialize()

    first = discover_rules(_experiment(database, _good_metrics, evaluation_budget=1), _bars())
    second = discover_rules(
        _experiment(
            database,
            _good_metrics,
            learning_run_id="durable-learning-2",
            started_at=datetime(2026, 8, 20, 19, 0, 1, tzinfo=UTC),
        ),
        _bars(),
    )
    persisted = database.frame(
        "select learning_run_id, rule_hash, rule_version, state, rule from discovered_rules order by learning_run_id"
    )

    assert first.best_candidate is not None and second.best_candidate is not None
    assert first.best_candidate.candidate_hash == second.best_candidate.candidate_hash
    assert first.best_candidate.version != second.best_candidate.version
    assert persisted["state"].tolist() == ["shadow", "shadow"]
    assert persisted["rule_hash"].nunique() == 1
    assert persisted["rule_version"].nunique() == 2
    assert first.best_candidate.state == second.best_candidate.state == "shadow"


def _forward(candidate: RuleCandidate, **changes: object) -> ForwardEvidence:
    assert candidate.evidence_through is not None
    values: dict[str, object] = {
        "candidate_hash": candidate.candidate_hash,
        "candidate_version": candidate.version,
        "period_start": candidate.evidence_through + timedelta(hours=1),
        "period_end": candidate.evidence_through + timedelta(hours=2),
        "evaluated_at": candidate.evidence_through + timedelta(hours=3),
        "causal_audit_passed": True,
        "causal_audited_at": candidate.evidence_through + timedelta(hours=2, minutes=30),
        "validation": PromotionDecision(True, ()),
        "outer_block_inspected": True,
        "outer_block_consumed": False,
    }
    values.update(changes)
    return ForwardEvidence(**values)


def test_promotion_requires_a_new_forward_period_normal_gates_and_causal_audit(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'promotion.duckdb'}")
    database.initialize()
    result = discover_rules(_experiment(database, _good_metrics, evaluation_budget=1), _bars())
    candidate = result.best_candidate
    assert candidate is not None and candidate.evidence_through is not None

    stale = _forward(candidate, period_start=candidate.evidence_through)
    failed_audit = _forward(candidate, causal_audit_passed=False)
    failed_gates = _forward(candidate, validation=PromotionDecision(False, ("drawdown gate failed",)))

    assert promote_candidate(candidate, stale).promoted is False
    assert promote_candidate(candidate, failed_audit).promoted is False
    assert promote_candidate(candidate, failed_gates).reasons == ("drawdown gate failed",)
    assert promote_candidate(candidate, _forward(candidate)).promoted is True


def test_promotion_rejects_consumed_evidence_active_rule_mutation_and_malformed_time(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'promotion-guards.duckdb'}")
    database.initialize()
    candidate = discover_rules(_experiment(database, _good_metrics, evaluation_budget=1), _bars()).best_candidate
    assert candidate is not None and candidate.evidence_through is not None

    consumed = _forward(candidate, outer_block_consumed=True)
    active = replace(candidate, state="active")

    assert promote_candidate(candidate, consumed).promoted is False
    assert promote_candidate(active, _forward(active)).promoted is False
    assert active.state == "active"
    with pytest.raises(ValueError, match="explicit UTC"):
        _forward(candidate, evaluated_at=datetime(2026, 8, 20, 23))


def test_promotion_is_pure_and_consumption_is_caller_supplied_state(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'pure-promotion.duckdb'}")
    database.initialize()
    candidate = discover_rules(_experiment(database, _good_metrics, evaluation_budget=1), _bars()).best_candidate
    assert candidate is not None
    evidence = _forward(candidate)

    first = promote_candidate(candidate, evidence)
    repeated = promote_candidate(candidate, evidence)

    assert first == repeated == PromotionDecision(True, ())
    consumed = replace(evidence, outer_block_consumed=True)
    assert promote_candidate(candidate, consumed) == PromotionDecision(
        False, ("forward outer block has already been consumed",)
    )
