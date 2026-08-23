from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
import pytest

from src.learning.search import (
    FitnessPenalties,
    FoldMetrics,
    LearningExperiment,
    RuleCandidate,
    calculate_fitness,
    discover_rules,
)
from src.strategies.types import BarInterval
from src.strategies.validation import WalkForwardFold


def _bars() -> pd.DataFrame:
    timestamps = pd.date_range("2026-08-20T09:00:00Z", periods=12, freq="h")
    return pd.DataFrame(
        {
            "decision_timestamp": timestamps,
            "available_at": timestamps,
            "outcome_available_at": timestamps + pd.Timedelta(minutes=30),
            "finalized": True,
            "rsi": [25, 35, 45, 55, 65, 75, 85, 70, 55, 40, 30, 20],
            "volume": [90, 100, 110, 105, 120, 130, 125, 115, 105, 95, 85, 80],
            "forward_return": [0.01, -0.002, 0.012, -0.004] * 3,
        }
    )


def _experiment(**changes: object) -> LearningExperiment:
    values: dict[str, object] = {
        "learning_run_id": "learning-run-1",
        "dataset_hash": "d" * 64,
        "symbol": "AAA",
        "interval": BarInterval.ONE_HOUR,
        "started_at": datetime(2026, 8, 20, 21, tzinfo=UTC),
        "as_of": datetime(2026, 8, 20, 22, tzinfo=UTC),
        "sealed_final_start": datetime(2026, 8, 21, tzinfo=UTC),
        "seed": 41,
        "evaluation_budget": 5,
        "inner_folds": (
            WalkForwardFold(tuple(range(4)), (4, 5)),
            WalkForwardFold(tuple(range(6)), (6, 7)),
        ),
        "indicators": ("rsi", "volume"),
        "thresholds": (30.0, 50.0, 70.0, 100.0),
    }
    values.update(changes)
    return LearningExperiment(**values)


def test_fixed_seed_is_deterministic_across_input_and_configuration_order() -> None:
    first = discover_rules(_experiment(), _bars())
    shuffled = _bars().sample(frac=1, random_state=7).reset_index(drop=True)
    second = discover_rules(
        _experiment(indicators=("volume", "rsi"), thresholds=(100.0, 70.0, 50.0, 30.0)),
        shuffled,
    )

    assert [(trial.trial_id, trial.candidate.candidate_hash, trial.fitness) for trial in first.trials] == [
        (trial.trial_id, trial.candidate.candidate_hash, trial.fitness) for trial in second.trials
    ]
    assert first.best_candidate == second.best_candidate


def test_every_candidate_query_is_ledgered_and_failed_trials_are_retained() -> None:
    calls = 0

    def evaluator(candidate: RuleCandidate, train: pd.DataFrame, validation: pd.DataFrame) -> FoldMetrics:
        nonlocal calls
        calls += 1
        if candidate.rule.render().startswith("rsi"):
            raise RuntimeError("intentional evaluator failure")
        return FoldMetrics(net_sharpe=1.0, maximum_drawdown=-0.1, turnover=0.2)

    result = discover_rules(_experiment(evaluator=evaluator, evaluation_budget=8), _bars())

    assert result.trial_count == len(result.trials) == 8
    assert calls >= result.trial_count
    assert any(
        trial.status == "failed" and "intentional evaluator failure" in trial.error_summary
        for trial in result.trials
    )
    assert [trial.ordinal for trial in result.trials] == list(range(8))
    assert len({trial.trial_id for trial in result.trials}) == 8


def test_fitness_is_median_inner_net_sharpe_less_all_declared_penalties() -> None:
    candidate = RuleCandidate.from_rule(
        _experiment(evaluation_budget=1),
        rule=_experiment().seed_rules[0],
    )
    penalties = FitnessPenalties(drawdown=0.5, turnover=0.25, instability=0.1, complexity=0.01)
    folds = (
        FoldMetrics(net_sharpe=1.0, maximum_drawdown=-0.1, turnover=0.2),
        FoldMetrics(net_sharpe=3.0, maximum_drawdown=-0.2, turnover=0.4),
    )

    fitness = calculate_fitness(candidate, folds, penalties)

    assert fitness == pytest.approx(2.0 - 0.5 * 0.15 - 0.25 * 0.3 - 0.1 * 1.0 - 0.01 * 3)


def test_search_never_accepts_sealed_final_rows_or_columns() -> None:
    final_row = _bars().iloc[[-1]].copy()
    final_row["decision_timestamp"] = pd.Timestamp("2026-08-21T00:00:00Z")
    final_row["available_at"] = final_row["decision_timestamp"]
    final_row["outcome_available_at"] = final_row["decision_timestamp"] + pd.Timedelta(minutes=30)

    with pytest.raises(ValueError, match="sealed final"):
        discover_rules(_experiment(), pd.concat([_bars(), final_row], ignore_index=True))

    labels = _bars().assign(sealed_final_label=999)
    with pytest.raises(ValueError, match="sealed or final"):
        discover_rules(_experiment(), labels)


def test_search_uses_only_inner_chronological_folds() -> None:
    observed: list[tuple[pd.Timestamp, pd.Timestamp, tuple[str, ...]]] = []

    def evaluator(_: RuleCandidate, train: pd.DataFrame, validation: pd.DataFrame) -> FoldMetrics:
        observed.append(
            (
                train["decision_timestamp"].max(),
                validation["decision_timestamp"].min(),
                tuple(validation.columns),
            )
        )
        return FoldMetrics(net_sharpe=0.5, maximum_drawdown=-0.05, turnover=0.1)

    discover_rules(_experiment(evaluator=evaluator, evaluation_budget=2), _bars())

    assert len(observed) == 4
    assert all(train_end < validation_start for train_end, validation_start, _ in observed)
    assert all("sealed_final_label" not in columns for _, _, columns in observed)


def test_high_fitness_does_not_stop_before_the_fixed_evaluation_budget() -> None:
    evaluations = 0

    def evaluator(_: RuleCandidate, __: pd.DataFrame, ___: pd.DataFrame) -> FoldMetrics:
        nonlocal evaluations
        evaluations += 1
        return FoldMetrics(net_sharpe=100.0, maximum_drawdown=0.0, turnover=0.0)

    result = discover_rules(_experiment(evaluator=evaluator, evaluation_budget=9), _bars())

    assert result.trial_count == 9
    assert evaluations == 9 * len(_experiment().inner_folds)
    assert result.stopped_reason == "evaluation_budget_exhausted"


def test_non_utc_or_unfinalized_development_evidence_fails_closed() -> None:
    non_utc = _bars()
    non_utc["decision_timestamp"] = non_utc["decision_timestamp"].dt.tz_convert("Europe/London")
    with pytest.raises(ValueError, match="explicit UTC"):
        discover_rules(_experiment(), non_utc)

    unfinalized = _bars()
    unfinalized.loc[3, "finalized"] = False
    with pytest.raises(ValueError, match="finalized"):
        discover_rules(_experiment(), unfinalized)

    with pytest.raises(ValueError, match="explicit UTC"):
        replace(_experiment(), as_of=datetime(2026, 8, 20, 22))


def test_each_indicator_row_must_be_available_by_its_decision_time() -> None:
    late = _bars()
    late.loc[4, "available_at"] = late.loc[4, "decision_timestamp"] + pd.Timedelta(hours=1)

    with pytest.raises(ValueError, match="available by its decision"):
        discover_rules(_experiment(), late)


def test_malformed_inner_chronology_fails_before_any_candidate_query() -> None:
    calls = 0

    def evaluator(_: RuleCandidate, __: pd.DataFrame, ___: pd.DataFrame) -> FoldMetrics:
        nonlocal calls
        calls += 1
        return FoldMetrics(1.0, -0.1, 0.2)

    malformed = _experiment(
        evaluator=evaluator,
        inner_folds=(WalkForwardFold((4, 5), (2, 3)),),
    )

    with pytest.raises(ValueError, match="strictly chronological"):
        discover_rules(malformed, _bars())
    assert calls == 0
