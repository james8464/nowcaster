from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd

from src.backtest.intraday import IntradayBacktestResult
from src.backtest.metrics import calculate_backtest_metrics
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, StrategyFamily, StrategyMode, StrategySpec
from src.strategies.validation import (
    EvaluationRequest,
    EvaluationStatus,
    FoldEvidence,
    StrategyRunEvidence,
    TrialEvidence,
    ValidationConfig,
    evaluate_registry,
    make_outer_folds,
    run_frozen_protocol,
    select_final_boundary,
)


def _timeline(periods: int = 12) -> pd.DataFrame:
    decisions = pd.date_range("2026-08-21 09:00", periods=periods, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "decision_timestamp": decisions,
            "outcome_available_at": decisions + pd.Timedelta(hours=2),
            "feature": range(periods),
            "label": [1, -1] * (periods // 2),
        }
    )


def _registry(*strategy_ids: str) -> StrategyRegistry:
    registry = StrategyRegistry()
    for position, strategy_id in enumerate(strategy_ids):
        registry.register(
            StrategySpec(
                strategy_id=strategy_id,
                family=(StrategyFamily.TREND if position % 2 == 0 else StrategyFamily.MEAN_REVERSION),
                version="1.0.0",
                intervals=(BarInterval.ONE_HOUR,),
                warmup_bars=1,
                parameters={},
            ),
            lambda *_: pd.DataFrame(),
        )
    return registry


def _backtest(final_returns: tuple[float, float] = (0.03, -0.01)) -> IntradayBacktestResult:
    timestamps = _timeline(10)["decision_timestamp"]
    returns = [0.01, -0.002, 0.012, -0.001, 0.014, -0.003, 0.016, -0.002, *final_returns]
    curve = pd.DataFrame(
        {
            "timestamp": timestamps,
            "net_return": returns,
            "gross_return": returns,
            "cost_return": 0.0,
            "turnover": 0.1,
            "gross_exposure": 0.5,
        }
    )
    trades = pd.DataFrame({"execution_timestamp": [timestamps.iloc[1]], "side": ["buy"]})
    metrics = calculate_backtest_metrics(curve, trades, periods_per_year=252)
    return IntradayBacktestResult(curve, trades, pd.DataFrame(), metrics)


def _signals() -> pd.DataFrame:
    timestamps = _timeline(10)["decision_timestamp"]
    return pd.DataFrame(
        {
            "decision_timestamp": timestamps,
            "data_through": timestamps,
            "signal": 1,
            "strength": 0.8,
        }
    )


def _timestamped_evidence(backtest: IntradayBacktestResult | None = None) -> StrategyRunEvidence:
    return StrategyRunEvidence(
        backtest=backtest or _backtest(),
        signals=_signals(),
        trial_evidence=(
            TrialEvidence(
                "trial-1", 0.1, datetime(2026, 8, 21, 14, tzinfo=UTC), datetime(2026, 8, 21, 15, tzinfo=UTC)
            ),
            TrialEvidence(
                "trial-2", 0.2, datetime(2026, 8, 21, 14, tzinfo=UTC), datetime(2026, 8, 21, 15, tzinfo=UTC)
            ),
            TrialEvidence(
                "trial-3", 0.3, datetime(2026, 8, 21, 15, tzinfo=UTC), datetime(2026, 8, 21, 16, tzinfo=UTC)
            ),
            TrialEvidence(
                "trial-4", 0.4, datetime(2026, 8, 21, 15, tzinfo=UTC), datetime(2026, 8, 21, 16, tzinfo=UTC)
            ),
        ),
        fold_evidence=(
            FoldEvidence(
                0,
                datetime(2026, 8, 21, 13, tzinfo=UTC),
                datetime(2026, 8, 21, 14, tzinfo=UTC),
                datetime(2026, 8, 21, 15, tzinfo=UTC),
                0.8,
                0.1,
            ),
            FoldEvidence(
                1,
                datetime(2026, 8, 21, 15, tzinfo=UTC),
                datetime(2026, 8, 21, 16, tzinfo=UTC),
                datetime(2026, 8, 21, 16, tzinfo=UTC),
                0.6,
                0.2,
            ),
        ),
        expected_edge=0.02,
        expected_cost=0.001,
        uncertainty=0.001,
    )


def _evaluation_request(registry: StrategyRegistry, runs: dict[str, StrategyRunEvidence]) -> EvaluationRequest:
    return EvaluationRequest(
        registry=registry,
        runs=runs,
        chronology=_timeline(10)["decision_timestamp"],
        as_of=datetime(2026, 8, 21, 18, tzinfo=UTC),
        mode=StrategyMode.FROZEN,
        dataset_hash="d" * 64,
        symbol="AAA",
        interval=BarInterval.ONE_HOUR,
        config=ValidationConfig(
            final_test_fraction=0.2,
            minimum_train_observations=4,
            validation_observations=2,
            minimum_dsr_probability=0,
            maximum_drawdown=1,
        ),
    )


def test_final_boundary_is_selected_from_complete_chronology_before_filtering() -> None:
    chronology = _timeline(10)
    eligible = chronology.loc[chronology["feature"].isin([0, 1, 2, 8, 9])]

    boundary = select_final_boundary(chronology["decision_timestamp"], final_test_fraction=0.2)

    assert boundary.final_start == pd.Timestamp("2026-08-21 17:00:00+00:00")
    assert boundary.development_index == tuple(range(8))
    assert boundary.final_index == (8, 9)
    filtered_boundary = select_final_boundary(eligible["decision_timestamp"], final_test_fraction=0.2)
    assert filtered_boundary.final_start != boundary.final_start


def test_outer_folds_are_chronological_and_purge_labels_through_the_full_embargo() -> None:
    data = _timeline()
    config = ValidationConfig(
        final_test_fraction=0.25,
        minimum_train_observations=4,
        validation_observations=2,
        forecast_horizon=timedelta(hours=1),
        publication_delay=timedelta(hours=1),
        embargo=timedelta(minutes=30),
    )
    boundary = select_final_boundary(data["decision_timestamp"], final_test_fraction=config.final_test_fraction)

    folds = make_outer_folds(data, boundary=boundary, config=config)

    assert [fold.validation_index for fold in folds] == [(4, 5), (6, 7), (8,)]
    for fold in folds:
        training = data.iloc[list(fold.train_index)]
        validation = data.iloc[list(fold.validation_index)]
        validation_start = validation["decision_timestamp"].min()
        assert training["decision_timestamp"].max() < validation_start
        assert training["outcome_available_at"].max() <= validation_start - pd.Timedelta(hours=2)
        assert validation["decision_timestamp"].max() < boundary.final_start


def test_each_later_outer_fold_contains_only_development_inner_folds() -> None:
    data = _timeline()
    config = ValidationConfig(
        final_test_fraction=0.25,
        minimum_train_observations=4,
        validation_observations=2,
        forecast_horizon=timedelta(hours=1),
        publication_delay=timedelta(hours=1),
    )
    boundary = select_final_boundary(data["decision_timestamp"], final_test_fraction=config.final_test_fraction)

    folds = make_outer_folds(data, boundary=boundary, config=config)

    assert [len(fold.inner_folds) for fold in folds] == [0, 1, 2]
    for outer in folds:
        outer_start = data.iloc[list(outer.validation_index)]["decision_timestamp"].min()
        for inner in outer.inner_folds:
            inner_training = data.iloc[list(inner.train_index)]
            inner_validation = data.iloc[list(inner.validation_index)]
            inner_start = inner_validation["decision_timestamp"].min()
            assert inner_validation["decision_timestamp"].max() < outer_start
            assert inner_training["outcome_available_at"].max() <= inner_start - pd.Timedelta(hours=2)


def test_frozen_predictions_are_invariant_to_mutated_final_labels_and_labels_are_not_features() -> None:
    original = _timeline(10)
    config = ValidationConfig(
        final_test_fraction=0.2,
        minimum_train_observations=3,
        validation_observations=2,
        forecast_horizon=timedelta(hours=1),
        publication_delay=timedelta(hours=1),
    )
    observed_feature_columns: list[tuple[str, ...]] = []

    def predictor(train_features: pd.DataFrame, train_labels: pd.Series, inference: pd.DataFrame) -> list[float]:
        observed_feature_columns.extend((tuple(train_features.columns), tuple(inference.columns)))
        return [float(train_labels.mean())] * len(inference)

    first = run_frozen_protocol(
        original,
        feature_columns=("feature",),
        label_column="label",
        predictor=predictor,
        config=config,
    )
    changed = original.copy()
    changed.loc[8:, "label"] = [9_999, -9_999]
    second = run_frozen_protocol(
        changed,
        feature_columns=("feature",),
        label_column="label",
        predictor=predictor,
        config=config,
    )

    assert first.final_predictions["prediction"].tolist() == [0.2, 0.2]
    pd.testing.assert_frame_equal(first.final_predictions, second.final_predictions)
    assert set(observed_feature_columns) == {("feature",)}


def test_walk_forward_predictions_change_only_after_an_outcome_becomes_observable() -> None:
    data = _timeline(10)
    config = ValidationConfig(
        final_test_fraction=0.2,
        minimum_train_observations=3,
        validation_observations=2,
        forecast_horizon=timedelta(hours=1),
        publication_delay=timedelta(hours=1),
    )

    def predictor(_: pd.DataFrame, train_labels: pd.Series, inference: pd.DataFrame) -> list[float]:
        return [float(train_labels.sum())] * len(inference)

    result = run_frozen_protocol(
        data,
        feature_columns=("feature",),
        label_column="label",
        predictor=predictor,
        config=config,
    )

    # At 17:00, only labels available by 15:00 are admitted by the two-hour embargo.
    assert result.final_training_index == (0, 1, 2, 3, 4)


def test_registry_evaluation_distinguishes_unavailable_from_failed_runs() -> None:
    request = EvaluationRequest(
        registry=_registry("unavailable", "failed", "missing"),
        runs={
            "unavailable": StrategyRunEvidence(unavailable_reason="licensed feed not configured"),
            "failed": StrategyRunEvidence(error_summary="backtest raised"),
        },
        chronology=_timeline(10)["decision_timestamp"],
        as_of=datetime(2026, 8, 21, 19, tzinfo=UTC),
        mode=StrategyMode.FROZEN,
        dataset_hash="d" * 64,
        symbol="AAA",
        interval=BarInterval.ONE_HOUR,
    )

    evaluations = evaluate_registry(request)

    assert [evaluation.status for evaluation in evaluations] == [
        EvaluationStatus.UNAVAILABLE,
        EvaluationStatus.FAILED,
        EvaluationStatus.UNAVAILABLE,
    ]
    assert evaluations[0].promotion.promoted is False
    assert evaluations[1].promotion.promoted is False
    assert evaluations[2].status_reason == "no run evidence supplied"


def test_unsealed_caller_aggregates_cannot_promote_or_receive_evidence_weight() -> None:
    registry = _registry("legacy")
    legacy = StrategyRunEvidence(
        backtest=_backtest(),
        signals=_signals(),
        trial_sharpes=(0.1, 0.2, 0.3, 0.4),
        fold_stability=1.0,
        calibration_error=0.0,
    )

    evaluation = evaluate_registry(_evaluation_request(registry, {"legacy": legacy}))[0]

    assert evaluation.status is EvaluationStatus.FAILED
    assert evaluation.promotion.promoted is False
    assert "unsealed aggregate evidence" in evaluation.status_reason


def test_timestamped_development_evidence_is_invariant_to_final_outcome_mutation() -> None:
    registry = _registry("sealed")
    original = evaluate_registry(_evaluation_request(registry, {"sealed": _timestamped_evidence()}))[0]
    changed = evaluate_registry(
        _evaluation_request(registry, {"sealed": _timestamped_evidence(_backtest((-0.9, 2.0)))})
    )[0]

    assert original.status is EvaluationStatus.EVALUATED
    assert original.promotion == changed.promotion
    assert original.development_sharpe == changed.development_sharpe
    assert original.calibration_error == changed.calibration_error == 0.15
    assert original.fold_stability == changed.fold_stability == 1
    assert original.trial_sharpes == changed.trial_sharpes == (0.1, 0.2, 0.3, 0.4)
    assert original.evidence_provenance == changed.evidence_provenance
    assert original.evidence_provenance["sealed_boundary"] == "2026-08-21T17:00:00+00:00"
    assert original.final_sharpe != changed.final_sharpe


def test_trial_evidence_at_or_after_final_boundary_is_rejected() -> None:
    registry = _registry("contaminated")
    evidence = _timestamped_evidence()
    contaminated_trial = replace(
        evidence.trial_evidence[-1],
        evaluated_at=datetime(2026, 8, 21, 17, tzinfo=UTC),
    )
    evidence = replace(evidence, trial_evidence=(*evidence.trial_evidence[:-1], contaminated_trial))

    evaluation = evaluate_registry(_evaluation_request(registry, {"contaminated": evidence}))[0]

    assert evaluation.status is EvaluationStatus.FAILED
    assert "sealed final boundary" in evaluation.status_reason


def test_malformed_trial_evidence_fails_only_its_strategy_without_aborting_registry() -> None:
    registry = _registry("malformed", "valid")
    malformed = _timestamped_evidence()
    malformed_trial = replace(malformed.trial_evidence[0], sharpe=float("nan"))
    malformed = replace(malformed, trial_evidence=(malformed_trial, *malformed.trial_evidence[1:]))

    evaluations = evaluate_registry(
        _evaluation_request(registry, {"malformed": malformed, "valid": _timestamped_evidence()})
    )

    assert evaluations[0].status is EvaluationStatus.FAILED
    assert "malformed trial evidence" in evaluations[0].status_reason
    assert evaluations[0].promotion.promoted is False
    assert evaluations[1].status is EvaluationStatus.EVALUATED
