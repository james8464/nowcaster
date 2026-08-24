from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from src.backtest.intraday import IntradayBacktestResult
from src.backtest.metrics import calculate_backtest_metrics
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, StrategyFamily, StrategyMode, StrategySpec
from src.strategies.validation import (
    EvaluationRequest,
    EvaluationStatus,
    FoldEvidence,
    RobustnessEvidence,
    StrategyRunEvidence,
    TrialEvidence,
    ValidationConfig,
    calculate_fold_calibration_error,
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
            "decision_timestamp": timestamps,
            "outcome_available_at": timestamps + pd.Timedelta(hours=2),
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


def test_final_segment_uses_decision_to_outcome_mapping_at_the_boundary() -> None:
    registry = _registry("mapped")
    backtest = _backtest(final_returns=(0.03, 0.01))
    curve = backtest.equity_curve.copy()
    curve["timestamp"] = pd.to_datetime(curve["timestamp"], utc=True) + pd.Timedelta(hours=1)
    curve.loc[7, "net_return"] = -0.9
    curve.loc[7, "gross_return"] = -0.9
    mapped = IntradayBacktestResult(
        curve,
        backtest.trade_ledger,
        backtest.rejection_ledger,
        calculate_backtest_metrics(curve, backtest.trade_ledger, periods_per_year=252),
    )

    evaluation = evaluate_registry(_evaluation_request(registry, {"mapped": _timestamped_evidence(mapped)}))[0]

    assert evaluation.final_sharpe == pytest.approx(22.44994432064365)


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
            TrialEvidence("trial-1", 0.1, datetime(2026, 8, 21, 14, tzinfo=UTC), datetime(2026, 8, 21, 15, tzinfo=UTC)),
            TrialEvidence("trial-2", 0.2, datetime(2026, 8, 21, 14, tzinfo=UTC), datetime(2026, 8, 21, 15, tzinfo=UTC)),
            TrialEvidence("trial-3", 0.3, datetime(2026, 8, 21, 15, tzinfo=UTC), datetime(2026, 8, 21, 16, tzinfo=UTC)),
            TrialEvidence("trial-4", 0.4, datetime(2026, 8, 21, 15, tzinfo=UTC), datetime(2026, 8, 21, 16, tzinfo=UTC)),
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
        ),
        robustness=RobustnessEvidence(
            median_walk_forward_net_edge=0.005,
            pbo_probability=0.25,
            parameter_neighborhood_stable=True,
            parameter_neighbor_positive_fraction=0.75,
            parameter_neighbor_median_ratio=0.8,
        ),
        expected_edge=0.02,
        expected_cost=0.001,
        uncertainty=0.001,
    )


def test_promotion_robustness_gates_fail_closed_and_accept_exact_pbo_boundary() -> None:
    registry = _registry("robust")
    missing = evaluate_registry(
        _evaluation_request(
            registry,
            {"robust": replace(_timestamped_evidence(), robustness=None)},
        )
    )[0]
    boundary = evaluate_registry(
        _evaluation_request(
            registry,
            {
                "robust": replace(
                    _timestamped_evidence(),
                    robustness=RobustnessEvidence(
                        median_walk_forward_net_edge=0.001,
                        pbo_probability=0.5,
                        parameter_neighborhood_stable=True,
                        parameter_neighbor_positive_fraction=0.5,
                        parameter_neighbor_median_ratio=0.5,
                    ),
                )
            },
        )
    )[0]
    negative = evaluate_registry(
        _evaluation_request(
            registry,
            {
                "robust": replace(
                    _timestamped_evidence(),
                    robustness=RobustnessEvidence(
                        median_walk_forward_net_edge=0.0,
                        pbo_probability=0.500001,
                        parameter_neighborhood_stable=False,
                        parameter_neighbor_positive_fraction=0.49,
                        parameter_neighbor_median_ratio=0.49,
                    ),
                )
            },
        )
    )[0]

    assert "robustness diagnostics are unavailable" in missing.promotion.reasons
    assert not any("PBO" in reason or "median walk-forward" in reason for reason in boundary.promotion.reasons)
    assert "median walk-forward net edge is not positive" in negative.promotion.reasons
    assert "CSCV/PBO gate failed" in negative.promotion.reasons
    assert "parameter-neighborhood stability failed" in negative.promotion.reasons


def test_fold_fitted_decision_calibration_uses_only_development_outcomes() -> None:
    registry = _registry("calibrated")
    base = _backtest(final_returns=(0.8, -0.8))
    signals = _signals().copy()
    signals.loc[1::2, "signal"] = -1
    signals.loc[3, "signal"] = 1
    evidence = replace(_timestamped_evidence(base), signals=signals)
    first = evaluate_registry(_evaluation_request(registry, {"calibrated": evidence}))[0]
    changed_curve = base.equity_curve.copy()
    changed_curve.loc[8:, ["net_return", "gross_return"]] *= -100
    changed = IntradayBacktestResult(
        changed_curve,
        base.trade_ledger,
        base.rejection_ledger,
        calculate_backtest_metrics(changed_curve, base.trade_ledger, periods_per_year=252),
    )
    second = evaluate_registry(_evaluation_request(registry, {"calibrated": replace(evidence, backtest=changed)}))[0]

    assert first.calibration_status == "calibrated"
    assert first.current_probability == pytest.approx(0.25)
    assert first.expected_edge == pytest.approx(0.04 / 6)
    assert first.current_probability == second.current_probability
    assert first.expected_edge == second.expected_edge
    assert first.expected_cost == second.expected_cost
    assert first.uncertainty == second.uncertainty


def test_fold_calibration_is_scored_at_mapped_outcome_rows() -> None:
    decisions = pd.to_datetime(["2026-08-21T10:00:00Z", "2026-08-21T11:00:00Z"], utc=True)
    signals = pd.DataFrame(
        {
            "decision_timestamp": decisions,
            "signal": [1, -1],
            "strength": [0.8, 0.6],
        }
    )
    outcomes = pd.DataFrame(
        {
            "decision_timestamp": decisions,
            "outcome_available_at": decisions + pd.Timedelta(hours=1),
            "net_return": [0.01, -0.02],
        }
    )

    error = calculate_fold_calibration_error(signals, outcomes, decisions)

    assert error == pytest.approx(0.025)


def test_fold_calibration_causally_carries_sparse_transition_signals() -> None:
    decisions = pd.date_range("2026-08-21T10:00:00Z", periods=3, freq="h")
    signals = pd.DataFrame(
        {
            "decision_timestamp": [decisions[0]],
            "signal": [1],
            "strength": [0.8],
        }
    )
    outcomes = pd.DataFrame(
        {
            "decision_timestamp": decisions,
            "outcome_available_at": decisions + pd.Timedelta(hours=1),
            "net_return": [0.01, -0.01, 0.02],
        }
    )

    error = calculate_fold_calibration_error(signals, outcomes, decisions)

    assert error == pytest.approx((0.01 + 0.81 + 0.01) / 3)


def _evaluation_request(registry: StrategyRegistry, runs: dict[str, StrategyRunEvidence]) -> EvaluationRequest:
    return EvaluationRequest(
        registry=registry,
        runs=runs,
        chronology=_timeline(10)["decision_timestamp"],
        outcome_availability=_timeline(10)["outcome_available_at"],
        as_of=datetime(2026, 8, 21, 20, tzinfo=UTC),
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


def test_final_boundary_rejects_naive_decision_chronology() -> None:
    chronology = [datetime(2026, 8, 21, hour) for hour in range(9, 19)]

    with pytest.raises(ValueError, match="explicit UTC"):
        select_final_boundary(chronology, final_test_fraction=0.2)


def test_registry_rejects_chronology_or_availability_after_requested_as_of() -> None:
    request = _evaluation_request(_registry("future"), {"future": _timestamped_evidence()})
    request = replace(request, as_of=datetime(2026, 8, 21, 16, tzinfo=UTC))

    with pytest.raises(ValueError, match="as_of"):
        evaluate_registry(request)


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

    assert [fold.validation_index for fold in folds] == [(4, 5), (6,)]
    for fold in folds:
        training = data.iloc[list(fold.train_index)]
        validation = data.iloc[list(fold.validation_index)]
        validation_start = validation["decision_timestamp"].min()
        assert training["decision_timestamp"].max() < validation_start
        assert training["outcome_available_at"].max() <= validation_start - pd.Timedelta(hours=2)
        assert validation["decision_timestamp"].max() < boundary.final_start


def test_outer_folds_reject_naive_outcome_availability_before_conversion() -> None:
    data = _timeline(10)
    data["outcome_available_at"] = [datetime(2026, 8, 21, hour) for hour in range(11, 21)]
    config = ValidationConfig(final_test_fraction=0.2, minimum_train_observations=4, validation_observations=2)
    boundary = select_final_boundary(data["decision_timestamp"], final_test_fraction=0.2)

    with pytest.raises(ValueError, match="explicit UTC"):
        make_outer_folds(data, boundary=boundary, config=config)


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

    assert [len(fold.inner_folds) for fold in folds] == [0, 1]
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
        outcome_availability=_timeline(10)["outcome_available_at"],
        as_of=datetime(2026, 8, 21, 20, tzinfo=UTC),
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
    assert original.calibration_error == changed.calibration_error == 0.1
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


@pytest.mark.parametrize("evidence_kind", ["trial", "fold"])
def test_registry_rejects_naive_trial_and_fold_evidence_timestamps(evidence_kind: str) -> None:
    registry = _registry("naive")
    evidence = _timestamped_evidence()
    if evidence_kind == "trial":
        bad_trial = replace(evidence.trial_evidence[0], evaluated_at=datetime(2026, 8, 21, 15))
        evidence = replace(evidence, trial_evidence=(bad_trial, *evidence.trial_evidence[1:]))
    else:
        bad_fold = replace(evidence.fold_evidence[0], validation_start=datetime(2026, 8, 21, 13))
        evidence = replace(evidence, fold_evidence=(bad_fold, *evidence.fold_evidence[1:]))

    evaluation = evaluate_registry(_evaluation_request(registry, {"naive": evidence}))[0]

    assert evaluation.status is EvaluationStatus.FAILED
    assert "explicit UTC" in evaluation.status_reason


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
