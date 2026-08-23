from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from src.backtest.costs import CostAssumptions
from src.backtest.execution import ExecutionAssumptions
from src.learning.grammar import RuleNode
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
    open_timestamps = pd.date_range("2026-08-20T09:00:00Z", periods=12, freq="h")
    timestamps = open_timestamps + pd.Timedelta(hours=1)
    opens = [100, 101, 99, 102, 98, 103, 97, 104, 96, 105, 95, 106]
    closes = [101, 99, 102, 98, 103, 97, 104, 96, 105, 95, 106, 94]
    return pd.DataFrame(
        {
            "symbol": "AAA",
            "open_timestamp": open_timestamps,
            "close_timestamp": timestamps,
            "open": opens,
            "high": [max(open_, close) + 1 for open_, close in zip(opens, closes, strict=True)],
            "low": [min(open_, close) - 1 for open_, close in zip(opens, closes, strict=True)],
            "close": closes,
            "decision_timestamp": timestamps,
            "available_at": timestamps,
            "outcome_available_at": timestamps + pd.Timedelta(minutes=30),
            "finalized": True,
            "rsi": [25, 35, 45, 55, 65, 75, 85, 70, 55, 40, 30, 20],
            "volume": [900_000, 1_000_000, 1_100_000, 1_050_000] * 3,
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


def test_fixed_seed_candidate_sequence_is_stable_in_a_fresh_process() -> None:
    def evaluator(*_) -> FoldMetrics:
        return FoldMetrics(1.0, -0.1, 0.2)

    expected = discover_rules(_experiment(evaluator=evaluator), _bars())
    program = textwrap.dedent(
        """
        import json
        from datetime import UTC, datetime
        import pandas as pd
        from src.learning.search import FoldMetrics, LearningExperiment, discover_rules
        from src.strategies.types import BarInterval
        from src.strategies.validation import WalkForwardFold

        timestamps = pd.date_range("2026-08-20T10:00:00Z", periods=12, freq="h")
        bars = pd.DataFrame({
            "decision_timestamp": timestamps,
            "available_at": timestamps,
            "outcome_available_at": timestamps + pd.Timedelta(minutes=30),
            "finalized": True,
            "rsi": [25, 35, 45, 55, 65, 75, 85, 70, 55, 40, 30, 20],
            "volume": [900000, 1000000, 1100000, 1050000] * 3,
            "forward_return": [0.01, -0.002, 0.012, -0.004] * 3,
        })
        def evaluator(*_):
            return FoldMetrics(1.0, -0.1, 0.2)
        experiment = LearningExperiment(
            learning_run_id="learning-run-1", dataset_hash="d" * 64, symbol="AAA",
            interval=BarInterval.ONE_HOUR,
            started_at=datetime(2026, 8, 20, 21, tzinfo=UTC),
            as_of=datetime(2026, 8, 20, 22, tzinfo=UTC),
            sealed_final_start=datetime(2026, 8, 21, tzinfo=UTC), seed=41,
            evaluation_budget=5,
            inner_folds=(WalkForwardFold(tuple(range(4)), (4, 5)), WalkForwardFold(tuple(range(6)), (6, 7))),
            indicators=("volume", "rsi"), thresholds=(100.0, 70.0, 50.0, 30.0),
            evaluator=evaluator,
        )
        result = discover_rules(experiment, bars.sample(frac=1, random_state=9))
        print(json.dumps([(trial.candidate.candidate_hash, trial.fitness) for trial in result.trials]))
        """
    )

    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", program],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == [
        [trial.candidate.candidate_hash, trial.fitness] for trial in expected.trials
    ]


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


@pytest.mark.parametrize(
    ("seed_rule", "error"),
    [
        (
            RuleNode.compare(
                "gt", RuleNode.indicator("future_label", lag=1), RuleNode.number(50)
            ),
            "declared indicator",
        ),
        (
            RuleNode.compare("gt", RuleNode.indicator("rsi", lag=99), RuleNode.number(50)),
            "maximum lag",
        ),
        (
            RuleNode.compare(
                "gt",
                RuleNode.indicator("rsi", lag=1, parameters=(("window", 999),)),
                RuleNode.number(50),
            ),
            "parameters",
        ),
        (
            RuleNode.compare("gt", RuleNode.indicator("rsi", lag=1), RuleNode.number(51)),
            "threshold",
        ),
    ],
)
def test_seed_terminals_cannot_escape_declared_domains(seed_rule: RuleNode, error: str) -> None:
    calls = 0

    def evaluator(_: RuleCandidate, __: pd.DataFrame, ___: pd.DataFrame) -> FoldMetrics:
        nonlocal calls
        calls += 1
        return FoldMetrics(1.0, -0.1, 0.2)

    result = discover_rules(
        _experiment(evaluator=evaluator, evaluation_budget=1, seed_rules=(seed_rule,)), _bars()
    )

    assert calls == 0
    assert result.trials[0].status == "invalid"
    assert error in str(result.trials[0].error_summary)


def test_custom_evaluator_receives_only_typed_allowlisted_evidence() -> None:
    observed: list[tuple[str, ...]] = []

    def evaluator(_: RuleCandidate, train: pd.DataFrame, validation: pd.DataFrame) -> FoldMetrics:
        observed.extend((tuple(train.columns), tuple(validation.columns)))
        assert "future_label" not in train
        assert "future_label" not in validation
        return FoldMetrics(1.0, -0.1, 0.2)

    bars = _bars().assign(future_label=10_000, unrelated_secret=-999)
    discover_rules(_experiment(evaluator=evaluator, evaluation_budget=1), bars)

    assert observed
    assert set(observed[0]) == {
        "decision_timestamp",
        "available_at",
        "outcome_available_at",
        "finalized",
        "rsi",
        "volume",
        "forward_return",
    }


def test_seed_rule_order_and_duplicates_do_not_change_search() -> None:
    first_rule = RuleNode.compare("gt", RuleNode.indicator("rsi", lag=1), RuleNode.number(50))
    equivalent = RuleNode.compare("lt", RuleNode.number(50), RuleNode.indicator("rsi", lag=1))
    second_rule = RuleNode.compare("lt", RuleNode.indicator("volume", lag=1), RuleNode.number(100))

    def evaluator(*_) -> FoldMetrics:
        return FoldMetrics(1.0, -0.1, 0.2)

    first = discover_rules(
        _experiment(
            evaluator=evaluator,
            evaluation_budget=2,
            seed_rules=(first_rule, second_rule, equivalent),
        ),
        _bars(),
    )
    second = discover_rules(
        _experiment(
            evaluator=evaluator,
            evaluation_budget=2,
            seed_rules=(second_rule, equivalent, first_rule),
        ),
        _bars(),
    )

    assert [trial.candidate.candidate_hash for trial in first.trials] == [
        trial.candidate.candidate_hash for trial in second.trials
    ]
    assert [trial.fitness for trial in first.trials] == [trial.fitness for trial in second.trials]
    assert [trial.candidate.rule.render() for trial in first.trials] == [
        trial.candidate.rule.render() for trial in second.trials
    ]


def test_outcome_must_be_strictly_later_than_its_decision() -> None:
    equal = _bars()
    equal.loc[4, "outcome_available_at"] = equal.loc[4, "decision_timestamp"]

    with pytest.raises(ValueError, match="strictly after"):
        discover_rules(_experiment(evaluator=lambda *_: FoldMetrics(1.0, -0.1, 0.2)), equal)


def test_inner_fitness_selects_parents_and_changes_later_candidates() -> None:
    rsi = RuleNode.compare("gt", RuleNode.indicator("rsi", lag=1), RuleNode.number(50))
    volume = RuleNode.compare("gt", RuleNode.indicator("volume", lag=1), RuleNode.number(100))

    def prefer(preferred_hash: str):
        def evaluator(candidate: RuleCandidate, *_: pd.DataFrame) -> FoldMetrics:
            return FoldMetrics(
                10.0 if candidate.candidate_hash == preferred_hash else 0.0,
                -0.1,
                0.2,
            )

        return evaluator

    rsi_hash = RuleCandidate.from_rule(_experiment(evaluation_budget=1), rsi).candidate_hash
    volume_hash = RuleCandidate.from_rule(_experiment(evaluation_budget=1), volume).candidate_hash
    common = {
        "evaluation_budget": 5,
        "seed_rules": (rsi, volume),
    }

    prefer_rsi = discover_rules(_experiment(evaluator=prefer(rsi_hash), **common), _bars())
    prefer_volume = discover_rules(_experiment(evaluator=prefer(volume_hash), **common), _bars())

    assert [trial.candidate.candidate_hash for trial in prefer_rsi.trials[:4]] == [
        trial.candidate.candidate_hash for trial in prefer_volume.trials[:4]
    ]
    assert prefer_rsi.trials[4].candidate.candidate_hash != prefer_volume.trials[4].candidate.candidate_hash


def test_default_fitness_uses_versioned_task4_execution_and_cost_assumptions() -> None:
    seed_rule = RuleNode.compare("gt", RuleNode.indicator("rsi", lag=1), RuleNode.number(50))
    short_rule = RuleNode.compare("gt", RuleNode.indicator("rsi", lag=1), RuleNode.number(100))

    def fitness(assumptions: ExecutionAssumptions, rule: RuleNode = seed_rule) -> float:
        result = discover_rules(
            _experiment(
                learning_run_id=f"costs-{assumptions.costs.half_spread_bps}-{assumptions.latency}",
                evaluation_budget=1,
                seed_rules=(rule,),
                execution_assumptions=assumptions,
            ),
            _bars(),
        )
        assert result.trials[0].fitness is not None
        return result.trials[0].fitness

    baseline = fitness(ExecutionAssumptions())
    spread = fitness(ExecutionAssumptions(costs=CostAssumptions(half_spread_bps=4)))
    funding = fitness(ExecutionAssumptions(costs=CostAssumptions(funding_bps_per_period=5)))
    short_baseline = fitness(ExecutionAssumptions(), short_rule)
    borrow = fitness(
        ExecutionAssumptions(costs=CostAssumptions(borrow_bps_per_period=5)), short_rule
    )
    costly = CostAssumptions(
        taker_fee_bps=3,
        half_spread_bps=4,
        slippage_bps=2,
        funding_bps_per_period=1,
        borrow_bps_per_period=5,
    )
    with_costs = fitness(ExecutionAssumptions(costs=costly))
    doubled = fitness(
        ExecutionAssumptions(
            costs=CostAssumptions(
                taker_fee_bps=6,
                half_spread_bps=8,
                slippage_bps=4,
                funding_bps_per_period=2,
                borrow_bps_per_period=10,
            )
        )
    )
    delayed = fitness(ExecutionAssumptions(costs=costly, latency=timedelta(hours=2)))

    assert spread != baseline
    assert funding != baseline
    assert borrow != short_baseline
    assert doubled != with_costs
    assert delayed != with_costs


def test_default_fold_metrics_exclude_prepended_training_only_returns() -> None:
    seed_rule = RuleNode.compare("gt", RuleNode.indicator("rsi", lag=1), RuleNode.number(50))
    base_bars = _bars().iloc[:8].copy()
    base = discover_rules(
        _experiment(
            evaluation_budget=1,
            inner_folds=(WalkForwardFold(tuple(range(4)), (4, 5)),),
            seed_rules=(seed_rule,),
        ),
        base_bars,
    )
    prepended = base_bars.iloc[:2].copy()
    for column in (
        "open_timestamp",
        "close_timestamp",
        "decision_timestamp",
        "available_at",
        "outcome_available_at",
    ):
        prepended[column] -= pd.Timedelta(hours=2)
    extended = pd.concat([prepended, base_bars], ignore_index=True)
    with_extra_train = discover_rules(
        _experiment(
            evaluation_budget=1,
            inner_folds=(WalkForwardFold(tuple(range(6)), (6, 7)),),
            seed_rules=(seed_rule,),
        ),
        extended,
    )

    assert with_extra_train.trials[0].fold_metrics == base.trials[0].fold_metrics
    assert with_extra_train.trials[0].fitness == base.trials[0].fitness
