from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from src.backtest.execution import ExecutionAssumptions
from src.backtest.intraday import RiskLimits, run_intraday_backtest
from src.database.engine import Database
from src.strategies.engine import decision_to_signal_frame, generate_current_decision
from src.strategies.ensemble import EnsembleConfig
from src.strategies.library import StrategyContext
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, StrategyFamily, StrategyMode, StrategySpec
from src.strategies.validation import (
    EvaluationRequest,
    FoldEvidence,
    StrategyRunEvidence,
    TrialEvidence,
    ValidationConfig,
    evaluate_registry,
)

VALIDATION_CONFIG = ValidationConfig(
    final_test_fraction=0.2,
    minimum_train_observations=4,
    validation_observations=2,
    minimum_dsr_probability=0,
    maximum_drawdown=1,
)


def _bars() -> pd.DataFrame:
    closes = [100, 110, 108, 120, 117, 130, 126, 140, 135, 150, 144, 160, 153, 170]
    opens = [100, *closes[:-1]]
    timestamps = pd.date_range("2026-08-21 09:00", periods=len(closes), freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "symbol": "AAA",
            "open_timestamp": timestamps,
            "close_timestamp": timestamps + pd.Timedelta(hours=1),
            "available_at": timestamps + pd.Timedelta(hours=1),
            "finalized": True,
            "open": [float(value) for value in opens],
            "high": [float(max(opening, close)) for opening, close in zip(opens, closes, strict=True)],
            "low": [float(min(opening, close)) for opening, close in zip(opens, closes, strict=True)],
            "close": [float(value) for value in closes],
            "volume": 100_000.0,
            "halted": False,
        }
    )


def _constant_generator(spec: StrategySpec, bars: pd.DataFrame, _: StrategyContext) -> pd.DataFrame:
    strength = float(spec.parameters["strength"])
    return pd.DataFrame(
        {
            "decision_timestamp": bars["close_timestamp"],
            "data_through": bars["close_timestamp"],
            "signal": 1,
            "strength": strength,
            "reason": "literal causal long fixture",
        }
    )


def _registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    for strategy_id, family, strength in (
        ("trend", StrategyFamily.TREND, 0.8),
        ("reversion", StrategyFamily.MEAN_REVERSION, 0.7),
        ("session", StrategyFamily.SESSION, 0.9),
    ):
        registry.register(
            StrategySpec(
                strategy_id=strategy_id,
                family=family,
                version="1.0.0",
                intervals=(BarInterval.ONE_HOUR,),
                warmup_bars=1,
                parameters={"strength": strength},
            ),
            _constant_generator,
        )
    return registry


def _evaluations() -> tuple:
    bars = _bars().iloc[:-1].copy()
    registry = _registry()
    runs: dict[str, StrategyRunEvidence] = {}
    for offset, registered in enumerate(registry.enabled()):
        signals = registered.generator(registered.spec, bars, StrategyContext())
        backtest = run_intraday_backtest(
            bars,
            signals,
            ExecutionAssumptions(lot_size=0.01),
            RiskLimits(initial_cash=10_000),
            strategy_id=registered.spec.strategy_id,
            symbol="AAA",
        )
        runs[registered.spec.strategy_id] = StrategyRunEvidence(
            backtest=backtest,
            signals=signals,
            trial_evidence=tuple(
                TrialEvidence(
                    f"trial-{trial}",
                    sharpe + offset * 0.01,
                    datetime(2026, 8, 21, 18, tzinfo=UTC),
                    datetime(2026, 8, 21, 19, tzinfo=UTC),
                )
                for trial, sharpe in enumerate((0.1, 0.2, 0.3, 0.4), start=1)
            ),
            fold_evidence=(
                FoldEvidence(
                    0,
                    datetime(2026, 8, 21, 14, tzinfo=UTC),
                    datetime(2026, 8, 21, 15, tzinfo=UTC),
                    datetime(2026, 8, 21, 16, tzinfo=UTC),
                    0.8,
                    0.1,
                ),
                FoldEvidence(
                    1,
                    datetime(2026, 8, 21, 16, tzinfo=UTC),
                    datetime(2026, 8, 21, 17, tzinfo=UTC),
                    datetime(2026, 8, 21, 18, tzinfo=UTC),
                    0.6,
                    0.2,
                ),
                FoldEvidence(
                    2,
                    datetime(2026, 8, 21, 18, tzinfo=UTC),
                    datetime(2026, 8, 21, 19, tzinfo=UTC),
                    datetime(2026, 8, 21, 19, tzinfo=UTC),
                    0.7,
                    0.15,
                ),
            ),
            expected_edge=0.02,
            expected_cost=0.001,
            uncertainty=0.001,
        )
    as_of = bars.iloc[-1]["close_timestamp"].to_pydatetime()
    request = EvaluationRequest(
        registry=registry,
        runs=runs,
        chronology=bars["close_timestamp"],
        outcome_availability=bars["available_at"],
        as_of=as_of,
        mode=StrategyMode.PAPER,
        dataset_hash="d" * 64,
        symbol="AAA",
        interval=BarInterval.ONE_HOUR,
        config=VALIDATION_CONFIG,
    )
    evaluations = evaluate_registry(request)
    assert all(evaluation.promotion.promoted for evaluation in evaluations)
    return evaluations


def _outcomes(as_of: datetime, evaluations: tuple) -> pd.DataFrame:
    resolved_at = as_of - timedelta(hours=1)
    unresolved_at = as_of + timedelta(hours=1)
    outcomes = pd.DataFrame(
        {
            "strategy_id": ["trend", "reversion", "session", "trend", "reversion", "session"],
            "decision_timestamp": [as_of - timedelta(hours=3)] * 3 + [as_of] * 3,
            "outcome_available_at": [resolved_at] * 3 + [unresolved_at] * 3,
            "signal": [1] * 6,
            "realized_return": [0.02, -0.01, 0.01, -10.0, 10.0, -10.0],
            "cost": [0.001] * 6,
        }
    )
    by_strategy = {evaluation.strategy_id: evaluation for evaluation in evaluations}
    outcomes["dataset_hash"] = outcomes["strategy_id"].map(lambda item: by_strategy[item].dataset_hash)
    outcomes["strategy_version"] = outcomes["strategy_id"].map(lambda item: by_strategy[item].strategy_version)
    outcomes["symbol"] = outcomes["strategy_id"].map(lambda item: by_strategy[item].symbol)
    outcomes["interval"] = outcomes["strategy_id"].map(lambda item: by_strategy[item].interval.value)
    outcomes["mode"] = outcomes["strategy_id"].map(lambda item: by_strategy[item].mode.value)
    return outcomes


def test_current_unlabeled_inference_is_deterministic_traceable_and_persists_resolved_provenance(tmp_path) -> None:
    evaluations = _evaluations()
    as_of = evaluations[0].decision_timestamp
    assert as_of is not None
    outcomes = _outcomes(as_of, evaluations)
    assert not (outcomes["decision_timestamp"] == as_of).loc[
        outcomes["outcome_available_at"] <= pd.Timestamp(as_of)
    ].any()
    database = Database.from_url(f"duckdb:///{tmp_path / 'ensemble.duckdb'}")
    database.initialize()
    config = EnsembleConfig(
        equal_weight_shrinkage=0.5,
        maximum_strategy_weight=0.5,
        maximum_family_weight=0.6,
        minimum_breadth=2,
    )

    first = generate_current_decision(
        evaluations,
        outcomes,
        as_of,
        config=config,
        validation_config=VALIDATION_CONFIG,
        database=database,
    )
    changed = outcomes.copy()
    changed.loc[changed["outcome_available_at"] > pd.Timestamp(as_of), "realized_return"] *= -1_000
    second = generate_current_decision(
        evaluations,
        changed,
        as_of,
        config=config,
        validation_config=VALIDATION_CONFIG,
        database=database,
    )

    assert first.signal == 1
    assert first.decision_hash == second.decision_hash
    assert first.weights == second.weights
    assert sum(weight.weight for weight in first.weights) == pytest.approx(1)
    assert {weight.outcomes_through for weight in first.weights} == {as_of - timedelta(hours=1)}
    persisted = database.frame(
        "SELECT strategy_id, effective_at, weight, evidence FROM ensemble_weights ORDER BY strategy_id"
    )
    assert len(persisted) == 3
    assert pd.to_datetime(persisted["effective_at"], utc=True).eq(pd.Timestamp(as_of - timedelta(hours=1))).all()
    assert all(row["multiple_testing_source"] == "observed_trial_sharpes" for row in persisted["evidence"])
    assert all(len(row["trial_sharpes"]) == 4 for row in persisted["evidence"])

    execution_bars = _bars().iloc[-2:].copy()
    signal_frame = decision_to_signal_frame(first, symbol="AAA")
    execution = run_intraday_backtest(
        execution_bars,
        signal_frame,
        ExecutionAssumptions(lot_size=0.01),
        RiskLimits(initial_cash=10_000),
    )
    assert not execution.trade_ledger.empty
    assert execution.trade_ledger["decision_timestamp"].eq(pd.Timestamp(first.as_of)).all()
    assert set(execution.trade_ledger["side"]) == {"buy"}
    assert execution.trade_ledger["source_decision_hashes"].map(
        lambda hashes: hashes == (first.decision_hash,)
    ).all()
    assert execution.trade_ledger["decision_hash"].notna().all()


def test_frozen_current_decision_never_applies_outcome_feedback() -> None:
    evaluations = tuple(replace(evaluation, mode=StrategyMode.FROZEN) for evaluation in _evaluations())
    as_of = evaluations[0].decision_timestamp
    assert as_of is not None
    outcomes = _outcomes(as_of, evaluations)
    reversed_outcomes = outcomes.copy()
    reversed_outcomes["realized_return"] *= -1
    config = EnsembleConfig(maximum_strategy_weight=0.5, maximum_family_weight=0.6)

    first = generate_current_decision(
        evaluations,
        outcomes,
        as_of,
        config=config,
        validation_config=VALIDATION_CONFIG,
    )
    second = generate_current_decision(
        evaluations,
        reversed_outcomes,
        as_of,
        config=config,
        validation_config=VALIDATION_CONFIG,
    )

    assert first.weights == second.weights
    assert first.decision_hash == second.decision_hash
