from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from src.strategies.engine import decision_to_signal_frame
from src.strategies.engine import generate_current_decision as _generate_current_decision
from src.strategies.ensemble import (
    EnsembleConfig,
    EnsembleDecision,
    EvidenceWeight,
)
from src.strategies.ensemble import combine_current_signals as _combine_current_signals
from src.strategies.ensemble import compute_evidence_weights as _compute_evidence_weights
from src.strategies.ensemble import fixed_share_update as _fixed_share_update
from src.strategies.types import BarInterval, StrategyFamily, StrategyMode, canonical_hash
from src.strategies.validation import (
    EvaluationStatus,
    PromotionDecision,
    RobustnessEvidence,
    StrategyEvaluation,
    ValidationConfig,
    ValidationTier,
)

AS_OF = datetime(2026, 8, 22, 12, tzinfo=UTC)
TEST_VALIDATION_CONFIG = ValidationConfig(
    final_test_fraction=0.2,
    minimum_train_observations=4,
    validation_observations=2,
    tier=ValidationTier.PROMOTION,
)


def compute_evidence_weights(
    evaluations: Sequence[StrategyEvaluation],
    *,
    as_of: datetime,
    config: EnsembleConfig,
    validation_config: ValidationConfig = TEST_VALIDATION_CONFIG,
) -> tuple[EvidenceWeight, ...]:
    return _compute_evidence_weights(
        evaluations,
        as_of=as_of,
        config=config,
        validation_config=validation_config,
    )


def combine_current_signals(
    evaluations: Sequence[StrategyEvaluation],
    weights: Sequence[EvidenceWeight],
    *,
    as_of: datetime,
    config: EnsembleConfig,
    validation_config: ValidationConfig = TEST_VALIDATION_CONFIG,
) -> EnsembleDecision:
    return _combine_current_signals(
        evaluations,
        weights,
        as_of=as_of,
        config=config,
        validation_config=validation_config,
    )


def generate_current_decision(
    evaluations: Sequence[StrategyEvaluation],
    resolved_outcomes: pd.DataFrame,
    as_of: datetime,
    *,
    config: EnsembleConfig,
) -> EnsembleDecision:
    return _generate_current_decision(
        evaluations,
        resolved_outcomes,
        as_of,
        config=config,
        validation_config=TEST_VALIDATION_CONFIG,
    )


def fixed_share_update(
    weights: Sequence[EvidenceWeight],
    resolved_outcomes: pd.DataFrame,
    *,
    evaluations: Sequence[StrategyEvaluation],
    as_of: datetime,
    config: EnsembleConfig,
    validation_config: ValidationConfig = TEST_VALIDATION_CONFIG,
) -> tuple[EvidenceWeight, ...]:
    return _fixed_share_update(
        weights,
        resolved_outcomes,
        evaluations=evaluations,
        as_of=as_of,
        config=config,
        validation_config=validation_config,
    )


def _evaluation(
    strategy_id: str,
    family: StrategyFamily,
    *,
    sharpe: float = 1.0,
    signal: int = 1,
    strength: float = 1.0,
    edge: float = 0.01,
    cost: float = 0.001,
    uncertainty: float = 0.001,
    promoted: bool = True,
    causal: bool = True,
    trial_sharpes: tuple[float, ...] = (0.1, 0.2, 0.3, 0.4),
) -> StrategyEvaluation:
    evidence_time = datetime(2026, 8, 21, 12, tzinfo=UTC)
    trial_rows = tuple(
        {
            "trial_id": f"trial-{index}",
            "sharpe": sharpe_value,
            "training_end": evidence_time,
            "evaluated_at": evidence_time + timedelta(hours=1),
        }
        for index, sharpe_value in enumerate(trial_sharpes, start=1)
    )
    fold_rows = (
        {
            "fold": 0,
            "validation_start": evidence_time - timedelta(hours=2),
            "validation_end": evidence_time - timedelta(hours=1),
            "evaluated_at": evidence_time,
            "sharpe": 0.8,
            "calibration_error": 0.1,
        },
        {
            "fold": 1,
            "validation_start": evidence_time,
            "validation_end": evidence_time + timedelta(hours=1),
            "evaluated_at": evidence_time + timedelta(hours=1),
            "sharpe": 0.6,
            "calibration_error": 0.1,
        },
    )
    promotion = PromotionDecision(promoted, () if promoted else ("promotion failed",))
    promotion_inputs = {
        "status": EvaluationStatus.EVALUATED.value,
        "development_sharpe": sharpe,
        "downside_risk": 0.02,
        "maximum_drawdown": -0.1,
        "calibration_error": 0.1,
        "fold_stability": 1.0,
        "cost_survives": True,
        "observations": 100,
        "effective_observations": 100.0,
        "trades": 20,
        "dsr_probability": 0.9,
        "bootstrap_probability": 0.9,
        "lower_net_edge": 0.005,
        "rolling_holdout_returns": (),
        "trial_sharpes": trial_sharpes,
        "causal_audit_passed": causal,
        "robustness_available": True,
        "median_walk_forward_net_edge": 0.005,
        "pbo_probability": 0.25,
        "parameter_neighborhood_stable": True,
        "parameter_neighbor_positive_fraction": 0.75,
        "parameter_neighbor_median_ratio": 0.8,
    }
    promotion_record = {"promoted": promotion.promoted, "reasons": promotion.reasons}
    sealed_provenance = {
        "sealed": True,
        "sealed_boundary": "2026-08-22T11:00:00+00:00",
        "trial_source": "timestamped_trial_evidence",
        "trial_evidence_hash": canonical_hash(trial_rows),
        "fold_evidence_hash": canonical_hash(fold_rows),
        "trials": trial_rows,
        "folds": fold_rows,
        "promotion_inputs": promotion_inputs,
        "promotion_decision": promotion_record,
        "promotion_evidence_hash": canonical_hash(
            {"promotion_inputs": promotion_inputs, "promotion_decision": promotion_record}
        ),
    }
    evaluation = StrategyEvaluation(
        strategy_id=strategy_id,
        strategy_version="1.0.0-deadbeef0000",
        family=family,
        status=EvaluationStatus.EVALUATED,
        status_reason="evaluation completed",
        promotion=promotion,
        development_sharpe=sharpe,
        final_sharpe=-99.0,
        downside_risk=0.02,
        development_maximum_drawdown=-0.1,
        calibration_error=0.1,
        fold_stability=1.0,
        cost_survives=True,
        observations=100,
        effective_observations=100.0,
        bootstrap_probability=0.9,
        lower_net_edge=0.005,
        trades=20,
        dsr_probability=0.9,
        trial_sharpes=trial_sharpes,
        causal_audit_passed=causal,
        robustness=RobustnessEvidence(0.005, 0.25, True, 0.75, 0.8),
        current_signal=signal,
        current_strength=strength,
        current_probability=0.75 if signal > 0 else 0.25 if signal < 0 else 0.5,
        economic_evidence_status="authenticated",
        expected_edge=edge,
        expected_cost=cost,
        uncertainty=uncertainty,
        decision_timestamp=AS_OF,
        data_through=AS_OF,
        dataset_hash="d" * 64,
        symbol="AAA",
        interval=BarInterval.ONE_HOUR,
        mode=StrategyMode.PAPER,
        **(
            {"evidence_provenance": sealed_provenance}
            if "evidence_provenance" in {item.name for item in fields(StrategyEvaluation)}
            else {}
        ),
    )
    return _with_root_snapshot(evaluation)


def _with_root_snapshot(evaluation: StrategyEvaluation) -> StrategyEvaluation:
    chronology = tuple(pd.date_range("2026-08-21 08:00", periods=10, freq="h", tz="UTC").to_pydatetime())
    fold_records = (
        {
            "fold": 0,
            "validation_start": chronology[4],
            "validation_end": chronology[5],
            "evaluated_at": chronology[5],
            "sharpe": 0.8,
            "calibration_error": 0.1,
        },
        {
            "fold": 1,
            "validation_start": chronology[6],
            "validation_end": chronology[7],
            "evaluated_at": chronology[7],
            "sharpe": 0.6,
            "calibration_error": 0.1,
        },
    )
    provenance = dict(evaluation.evidence_provenance)
    validation_config = {
        "tier": "promotion",
        "final_test_fraction": 0.2,
        "minimum_train_observations": 4,
        "validation_observations": 2,
        "forecast_horizon_seconds": 0.0,
        "publication_delay_seconds": 0.0,
        "embargo_seconds": 0.0,
        "periods_per_year": 252,
        "minimum_trades": 1,
        "minimum_development_observations": 5,
        "maximum_drawdown": 0.5,
        "minimum_dsr_probability": 0.5,
        "maximum_pbo_probability": 0.5,
        "minimum_parameter_neighbor_positive_fraction": 0.5,
        "minimum_parameter_neighbor_median_ratio": 0.5,
        "minimum_effective_observations": 0,
        "minimum_bootstrap_probability": 0.0,
        "minimum_rolling_holdouts": 0,
    }
    snapshot = {
        "schema_version": 3,
        "context": {
            "dataset_hash": evaluation.dataset_hash,
            "strategy_id": evaluation.strategy_id,
            "strategy_version": evaluation.strategy_version,
            "family": evaluation.family.value,
            "symbol": evaluation.symbol,
            "interval": evaluation.interval.value,
            "mode": evaluation.mode.value,
        },
        "chronology": chronology,
        "chronology_hash": canonical_hash(chronology),
        "outcome_availability": chronology,
        "outcome_availability_hash": canonical_hash(chronology),
        "validation_config": validation_config,
        "validation_policy_hash": canonical_hash(validation_config),
        "evaluated_as_of": chronology[-1],
        "promotion_evidence_through": chronology[7],
        "final_boundary": {
            "final_start": chronology[8],
            "development_index": tuple(range(8)),
            "final_index": (8, 9),
        },
        "fold_plan": (
            {
                "fold": 0,
                "train_index": (0, 1, 2, 3),
                "validation_index": (4, 5),
                "inner_folds": (),
            },
            {
                "fold": 1,
                "train_index": (0, 1, 2, 3, 4, 5),
                "validation_index": (6, 7),
                "inner_folds": ({"train_index": (0, 1, 2, 3), "validation_index": (4, 5)},),
            },
        ),
        "trial_records": provenance["trials"],
        "fold_records": fold_records,
        "derived": provenance["promotion_inputs"],
        "promotion": provenance["promotion_decision"],
    }
    provenance.update(
        {
            "sealed_boundary": chronology[8].isoformat(),
            "folds": fold_records,
            "fold_evidence_hash": canonical_hash(fold_records),
            "validation_snapshot": snapshot,
            "validation_snapshot_hash": canonical_hash(snapshot),
        }
    )
    return replace(evaluation, evidence_provenance=provenance)


def _with_context(outcomes: pd.DataFrame, evaluations: tuple[StrategyEvaluation, ...]) -> pd.DataFrame:
    by_strategy = {evaluation.strategy_id: evaluation for evaluation in evaluations}
    result = outcomes.copy()
    result["dataset_hash"] = result["strategy_id"].map(lambda strategy_id: by_strategy[strategy_id].dataset_hash)
    result["strategy_version"] = result["strategy_id"].map(
        lambda strategy_id: by_strategy[strategy_id].strategy_version
    )
    result["symbol"] = result["strategy_id"].map(lambda strategy_id: by_strategy[strategy_id].symbol)
    result["interval"] = result["strategy_id"].map(lambda strategy_id: by_strategy[strategy_id].interval.value)
    result["mode"] = result["strategy_id"].map(lambda strategy_id: by_strategy[strategy_id].mode.value)
    return result


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_thaw(item) for item in value)
    return value


def _with_mode(evaluation: StrategyEvaluation, mode: StrategyMode) -> StrategyEvaluation:
    provenance = _thaw(evaluation.evidence_provenance)
    assert isinstance(provenance, dict)
    snapshot = dict(provenance["validation_snapshot"])
    context = dict(snapshot["context"])
    context["mode"] = mode.value
    snapshot["context"] = context
    provenance["validation_snapshot"] = snapshot
    provenance["validation_snapshot_hash"] = canonical_hash(snapshot)
    return replace(evaluation, mode=mode, evidence_provenance=provenance)


def _forge_offline_weight_state(
    weights: tuple[EvidenceWeight, ...],
    *,
    current_values: tuple[float, ...] | None = None,
    base_values: tuple[float, ...] | None = None,
    prior_values: tuple[float, ...] | None = None,
) -> tuple[EvidenceWeight, ...]:
    snapshot = _thaw(weights[0].provenance["weight_cohort_snapshot"])
    assert isinstance(snapshot, dict)
    members = [dict(member) for member in snapshot["members"]]
    current_rows = [dict(row) for row in snapshot["current_weights"]]
    public_values = current_values or tuple(weight.weight for weight in weights)
    public_priors = prior_values or tuple(weight.prior_weight for weight in weights)
    for index, (member, row) in enumerate(zip(members, current_rows, strict=True)):
        row["weight"] = public_values[index]
        if base_values is not None:
            member["base_weight"] = base_values[index]
        if prior_values is not None:
            member["prior_weight"] = prior_values[index]
    snapshot["members"] = tuple(members)
    snapshot["current_weights"] = tuple(current_rows)
    snapshot["current_weights_hash"] = canonical_hash(snapshot["current_weights"])
    snapshot_hash = canonical_hash(snapshot)
    forged = []
    for index, weight in enumerate(weights):
        provenance = _thaw(weight.provenance)
        assert isinstance(provenance, dict)
        provenance["weight_cohort_snapshot"] = snapshot
        provenance["weight_cohort_hash"] = snapshot_hash
        forged.append(
            replace(
                weight,
                weight=public_values[index],
                prior_weight=public_priors[index],
                provenance=provenance,
            )
        )
    return tuple(forged)


def _replace_online_state(
    weights: tuple,
    state: object,
) -> tuple:
    changed = []
    for weight in weights:
        provenance = _thaw(weight.provenance)
        assert isinstance(provenance, dict)
        provenance["online_state"] = state
        changed.append(replace(weight, provenance=provenance))
    return tuple(changed)


def _with_future_online_history(
    weights: tuple[EvidenceWeight, ...],
    *,
    as_of: datetime,
) -> tuple[EvidenceWeight, ...]:
    cohort = _thaw(weights[0].provenance["weight_cohort_snapshot"])
    assert isinstance(cohort, dict)
    members = tuple(cohort["members"])
    identity = {
        "strategy_id": weights[0].strategy_id,
        "dataset_hash": weights[0].dataset_hash,
        "strategy_version": weights[0].strategy_version,
        "symbol": weights[0].symbol,
        "interval": weights[0].interval.value,
        "mode": weights[0].mode.value,
        "decision_timestamp": as_of.isoformat(),
        "outcome_available_at": (as_of + timedelta(hours=1)).isoformat(),
    }
    record = {
        "outcome_id": canonical_hash(identity),
        **identity,
        "signal": 1,
        "realized_return": 0.25,
        "cost": 0.0,
    }
    history = (record,)
    current_rows = tuple(
        {
            "strategy_id": weight.strategy_id,
            "weight": weight.weight,
            "effective_at": weight.effective_at.isoformat(),
            "outcomes_through": None,
        }
        for weight in weights
    )
    config_payload = cohort["ensemble_config"]
    state = {
        "config": config_payload,
        "config_hash": canonical_hash(config_payload),
        "cohort_hash": weights[0].provenance["weight_cohort_hash"],
        "base_weights": tuple(
            {"strategy_id": member["strategy_id"], "weight": member["base_weight"]} for member in members
        ),
        "processed_outcome_ids": (record["outcome_id"],),
        "processed_outcomes": history,
        "processed_outcomes_hash": canonical_hash(history),
        "adaptive_learning_rates": (),
        "cumulative_mixability_gap": 0.0,
        "current_weights": current_rows,
        "current_weights_hash": canonical_hash(current_rows),
    }
    state["state_hash"] = canonical_hash(state)
    assert state["state_hash"] == canonical_hash({key: value for key, value in state.items() if key != "state_hash"})
    return _replace_online_state(weights, state)


def _feedback_fixture() -> tuple[tuple[StrategyEvaluation, ...], EnsembleConfig, tuple, pd.DataFrame]:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(
        maximum_strategy_weight=0.8,
        maximum_family_weight=0.8,
        fixed_share=0.1,
        learning_rate=10,
    )
    initial = compute_evidence_weights(evaluations, as_of=datetime(2026, 8, 22, 9, tzinfo=UTC), config=config)
    outcomes = _with_context(
        pd.DataFrame(
            {
                "strategy_id": ["alpha", "beta", "gamma"],
                "decision_timestamp": pd.to_datetime(["2026-08-22 09:00Z"] * 3),
                "outcome_available_at": pd.to_datetime(["2026-08-22 10:00Z"] * 3),
                "signal": [1, 1, 1],
                "realized_return": [1.0, -1.0, 0.0],
                "cost": [0.0, 0.0, 0.0],
            }
        ),
        evaluations,
    )
    return evaluations, config, initial, outcomes


def test_weights_are_nonnegative_normalized_shrunk_and_obey_strategy_and_family_caps() -> None:
    evaluations = (
        _evaluation("trend_fast", StrategyFamily.TREND, sharpe=2.0),
        _evaluation("trend_slow", StrategyFamily.TREND, sharpe=1.5),
        _evaluation("reversion", StrategyFamily.MEAN_REVERSION, sharpe=0.8),
        _evaluation("session", StrategyFamily.SESSION, sharpe=0.4),
    )
    config = EnsembleConfig(equal_weight_shrinkage=0.4, maximum_strategy_weight=0.35, maximum_family_weight=0.55)

    weights = compute_evidence_weights(evaluations, as_of=AS_OF, config=config)

    values = {weight.strategy_id: weight.weight for weight in weights}
    assert sum(values.values()) == pytest.approx(1)
    assert min(values.values()) >= 0
    assert max(values.values()) <= 0.35 + 1e-12
    assert values["trend_fast"] + values["trend_slow"] <= 0.55 + 1e-12

    equal = compute_evidence_weights(evaluations, as_of=AS_OF, config=replace(config, equal_weight_shrinkage=1))
    assert [weight.weight for weight in equal] == pytest.approx([0.25, 0.25, 0.25, 0.25])
    raw = compute_evidence_weights(evaluations, as_of=AS_OF, config=replace(config, equal_weight_shrinkage=0))
    prior_distance = sum(abs(weight.weight - 0.25) for weight in weights)
    raw_distance = sum(abs(weight.weight - 0.25) for weight in raw)
    assert prior_distance < raw_distance


def test_failed_or_noncausal_strategies_and_missing_trial_vectors_receive_zero_weight() -> None:
    evaluations = (
        _evaluation("eligible_a", StrategyFamily.TREND),
        _evaluation("eligible_b", StrategyFamily.MEAN_REVERSION),
        _evaluation("eligible_c", StrategyFamily.SESSION),
        _evaluation("promotion_failed", StrategyFamily.VOLATILITY_VOLUME, promoted=False),
        _evaluation("causal_failed", StrategyFamily.RELATIVE_VALUE, causal=False),
        _evaluation("no_observed_trials", StrategyFamily.RELATIVE_VALUE, trial_sharpes=()),
    )

    weights = compute_evidence_weights(
        evaluations,
        as_of=AS_OF,
        config=EnsembleConfig(maximum_strategy_weight=0.5, maximum_family_weight=0.6),
    )

    values = {weight.strategy_id: weight.weight for weight in weights}
    assert values["promotion_failed"] == 0
    assert values["causal_failed"] == 0
    assert values["no_observed_trials"] == 0
    assert sum(values.values()) == pytest.approx(1)


def test_weights_ignore_final_holdout_metrics_and_use_actual_trial_sharpe_dispersion() -> None:
    concentrated = _evaluation("concentrated", StrategyFamily.TREND, trial_sharpes=(0.1, 0.11, 0.12, 0.13))
    dispersed = _evaluation("dispersed", StrategyFamily.MEAN_REVERSION, trial_sharpes=(-1.0, 0.0, 1.0, 2.0))
    third = _evaluation("third", StrategyFamily.SESSION, trial_sharpes=(0.1, 0.2, 0.3, 0.4))
    config = EnsembleConfig(equal_weight_shrinkage=0, maximum_strategy_weight=0.8, maximum_family_weight=0.8)

    before = compute_evidence_weights((concentrated, dispersed, third), as_of=AS_OF, config=config)
    after = compute_evidence_weights(
        (replace(concentrated, final_sharpe=9_999), replace(dispersed, final_sharpe=-9_999), third),
        as_of=AS_OF,
        config=config,
    )

    assert before == after
    values = {weight.strategy_id: weight.weight for weight in before}
    assert values["concentrated"] > values["dispersed"]


def test_weighting_rejects_aggregates_that_do_not_match_sealed_evidence() -> None:
    valid = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    forged = (replace(valid[0], trial_sharpes=(9.0, 9.1, 9.2, 9.3)), *valid[1:])
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8)

    weights = compute_evidence_weights(forged, as_of=AS_OF, config=config)

    assert {weight.strategy_id: weight.weight for weight in weights}["alpha"] == 0


def test_weighting_rejects_incomplete_fold_plan_even_when_one_fold_is_positive_and_rehashed() -> None:
    evaluations = tuple(
        _with_root_snapshot(evaluation)
        for evaluation in (
            _evaluation("alpha", StrategyFamily.TREND),
            _evaluation("beta", StrategyFamily.MEAN_REVERSION),
            _evaluation("gamma", StrategyFamily.SESSION),
        )
    )
    provenance = dict(evaluations[0].evidence_provenance)
    snapshot = dict(provenance["validation_snapshot"])
    snapshot["fold_records"] = (snapshot["fold_records"][0],)
    provenance["folds"] = snapshot["fold_records"]
    provenance["fold_evidence_hash"] = canonical_hash(snapshot["fold_records"])
    provenance["validation_snapshot"] = snapshot
    provenance["validation_snapshot_hash"] = canonical_hash(snapshot)
    forged = (replace(evaluations[0], evidence_provenance=provenance), *evaluations[1:])

    weights = compute_evidence_weights(
        forged,
        as_of=AS_OF,
        config=EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8),
    )

    assert {weight.strategy_id: weight.weight for weight in weights}["alpha"] == 0


def test_weighting_rejects_rehashed_boundary_substitution_against_bound_chronology() -> None:
    evaluations = tuple(
        _with_root_snapshot(evaluation)
        for evaluation in (
            _evaluation("alpha", StrategyFamily.TREND),
            _evaluation("beta", StrategyFamily.MEAN_REVERSION),
            _evaluation("gamma", StrategyFamily.SESSION),
        )
    )
    provenance = dict(evaluations[0].evidence_provenance)
    snapshot = dict(provenance["validation_snapshot"])
    chronology = snapshot["chronology"]
    snapshot["final_boundary"] = {
        "final_start": chronology[9],
        "development_index": tuple(range(9)),
        "final_index": (9,),
    }
    provenance["sealed_boundary"] = chronology[9].isoformat()
    provenance["validation_snapshot"] = snapshot
    provenance["validation_snapshot_hash"] = canonical_hash(snapshot)
    forged = (replace(evaluations[0], evidence_provenance=provenance), *evaluations[1:])

    weights = compute_evidence_weights(
        forged,
        as_of=AS_OF,
        config=EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8),
    )

    assert {weight.strategy_id: weight.weight for weight in weights}["alpha"] == 0


def test_weighting_rejects_shifted_provenance_boundary_even_when_root_snapshot_is_unchanged() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    provenance = dict(evaluations[0].evidence_provenance)
    provenance["sealed_boundary"] = "2026-08-22T12:00:00+00:00"
    forged = (replace(evaluations[0], evidence_provenance=provenance), *evaluations[1:])

    weights = compute_evidence_weights(
        forged,
        as_of=AS_OF,
        config=EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8),
    )

    assert {weight.strategy_id: weight.weight for weight in weights}["alpha"] == 0


@pytest.mark.parametrize("schema_version", [None, 999])
def test_weighting_rejects_missing_or_unknown_root_snapshot_schema(schema_version: int | None) -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    provenance = dict(evaluations[0].evidence_provenance)
    snapshot = dict(provenance["validation_snapshot"])
    if schema_version is None:
        snapshot.pop("schema_version")
    else:
        snapshot["schema_version"] = schema_version
    provenance["validation_snapshot"] = snapshot
    provenance["validation_snapshot_hash"] = canonical_hash(snapshot)
    forged = (replace(evaluations[0], evidence_provenance=provenance), *evaluations[1:])

    weights = compute_evidence_weights(
        forged,
        as_of=AS_OF,
        config=EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8),
    )

    assert {weight.strategy_id: weight.weight for weight in weights}["alpha"] == 0


def test_weighting_recomputes_promotion_and_rejects_rehashed_zero_trade_forgery() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    provenance = dict(evaluations[0].evidence_provenance)
    promotion_inputs = dict(provenance["promotion_inputs"])
    promotion_inputs["trades"] = 0
    snapshot = dict(provenance["validation_snapshot"])
    snapshot["derived"] = promotion_inputs
    provenance["promotion_inputs"] = promotion_inputs
    provenance["promotion_evidence_hash"] = canonical_hash(
        {"promotion_inputs": promotion_inputs, "promotion_decision": provenance["promotion_decision"]}
    )
    provenance["validation_snapshot"] = snapshot
    provenance["validation_snapshot_hash"] = canonical_hash(snapshot)
    forged_alpha = replace(evaluations[0], trades=0, evidence_provenance=provenance)

    weights = compute_evidence_weights(
        (forged_alpha, *evaluations[1:]),
        as_of=AS_OF,
        config=EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8),
    )

    assert {weight.strategy_id: weight.weight for weight in weights}["alpha"] == 0


def test_weighting_recomputes_maximum_drawdown_gate_from_the_root_snapshot() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    provenance = dict(evaluations[0].evidence_provenance)
    promotion_inputs = dict(provenance["promotion_inputs"])
    promotion_inputs["maximum_drawdown"] = -0.9
    snapshot = dict(provenance["validation_snapshot"])
    snapshot["derived"] = promotion_inputs
    provenance["promotion_inputs"] = promotion_inputs
    provenance["promotion_evidence_hash"] = canonical_hash(
        {"promotion_inputs": promotion_inputs, "promotion_decision": provenance["promotion_decision"]}
    )
    provenance["validation_snapshot"] = snapshot
    provenance["validation_snapshot_hash"] = canonical_hash(snapshot)
    forged_alpha = replace(
        evaluations[0],
        development_maximum_drawdown=-0.9,
        evidence_provenance=provenance,
    )

    weights = compute_evidence_weights(
        (forged_alpha, *evaluations[1:]),
        as_of=AS_OF,
        config=EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8),
    )

    assert {weight.strategy_id: weight.weight for weight in weights}["alpha"] == 0


def test_weighting_rejects_sealed_evidence_created_after_requested_as_of() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )

    weights = compute_evidence_weights(
        evaluations,
        as_of=datetime(2026, 8, 20, 12, tzinfo=UTC),
        config=EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8),
        validation_config=TEST_VALIDATION_CONFIG,
    )

    assert all(weight.weight == 0 for weight in weights)


def test_weighting_rejects_rehashed_embedded_policy_forgery_under_trusted_policy() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    provenance = dict(evaluations[0].evidence_provenance)
    snapshot = dict(provenance["validation_snapshot"])
    embedded = dict(snapshot["validation_config"])
    embedded["maximum_drawdown"] = 1.0
    snapshot["validation_config"] = embedded
    snapshot["validation_policy_hash"] = canonical_hash(embedded)
    provenance["validation_snapshot"] = snapshot
    provenance["validation_snapshot_hash"] = canonical_hash(snapshot)
    forged = (replace(evaluations[0], evidence_provenance=provenance), *evaluations[1:])

    weights = compute_evidence_weights(
        forged,
        as_of=AS_OF,
        config=EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8),
        validation_config=TEST_VALIDATION_CONFIG,
    )

    assert {weight.strategy_id: weight.weight for weight in weights}["alpha"] == 0


def test_fixed_share_updates_only_resolved_outcomes_and_conserves_mass() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8, fixed_share=0.1, learning_rate=2)
    initial = compute_evidence_weights(evaluations, as_of=datetime(2026, 8, 22, 10, tzinfo=UTC), config=config)
    outcomes = _with_context(
        pd.DataFrame(
            {
                "strategy_id": ["alpha", "beta", "gamma", "alpha", "beta", "gamma"],
                "decision_timestamp": pd.to_datetime(["2026-08-22 10:00Z"] * 3 + ["2026-08-22 11:00Z"] * 3),
                "outcome_available_at": pd.to_datetime(["2026-08-22 11:00Z"] * 3 + ["2026-08-22 13:00Z"] * 3),
                "signal": [1, 1, 1, 1, 1, 1],
                "realized_return": [0.02, -0.02, 0.0, -1.0, 1.0, 1.0],
                "cost": [0.001] * 6,
            }
        ),
        evaluations,
    )

    first = fixed_share_update(
        initial,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 11, tzinfo=UTC),
        config=config,
    )
    changed = outcomes.copy()
    changed.loc[changed["outcome_available_at"] > pd.Timestamp("2026-08-22 11:00Z"), "realized_return"] *= -100
    unchanged = fixed_share_update(
        initial,
        changed,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 11, tzinfo=UTC),
        config=config,
    )

    assert first == unchanged
    assert sum(weight.weight for weight in first) == pytest.approx(1)
    assert min(weight.weight for weight in first) >= 0
    assert {weight.effective_at for weight in first} == {datetime(2026, 8, 22, 11, tzinfo=UTC)}
    assert {weight.outcomes_through for weight in first} == {datetime(2026, 8, 22, 11, tzinfo=UTC)}
    values = {weight.strategy_id: weight.weight for weight in first}
    assert values["alpha"] > values["beta"]


def test_adahedge_learning_rate_adapts_to_accumulated_mixability_gap() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(
        maximum_strategy_weight=0.8,
        maximum_family_weight=0.8,
        fixed_share=0.1,
        learning_rate=10,
    )
    initial = compute_evidence_weights(evaluations, as_of=datetime(2026, 8, 22, 9, tzinfo=UTC), config=config)
    outcomes = _with_context(
        pd.DataFrame(
            {
                "strategy_id": ["alpha", "beta", "gamma"] * 2,
                "decision_timestamp": pd.to_datetime(["2026-08-22 09:00Z"] * 3 + ["2026-08-22 10:00Z"] * 3),
                "outcome_available_at": pd.to_datetime(["2026-08-22 10:00Z"] * 3 + ["2026-08-22 11:00Z"] * 3),
                "signal": [1] * 6,
                "realized_return": [1.0, -1.0, 0.0, -1.0, 1.0, 0.0],
                "cost": [0.0] * 6,
            }
        ),
        evaluations,
    )

    updated = fixed_share_update(
        initial,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 11, tzinfo=UTC),
        config=config,
    )

    online_state = updated[0].provenance["online_state"]
    rates = online_state["adaptive_learning_rates"]
    assert len(rates) == 2
    assert rates[0] == 10
    assert 0 < rates[1] < rates[0]
    assert online_state["cumulative_mixability_gap"] > 0


def test_adahedge_state_makes_batch_and_incremental_updates_equivalent() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(
        maximum_strategy_weight=0.8,
        maximum_family_weight=0.8,
        fixed_share=0.1,
        learning_rate=10,
    )
    initial = compute_evidence_weights(evaluations, as_of=datetime(2026, 8, 22, 9, tzinfo=UTC), config=config)
    outcomes = _with_context(
        pd.DataFrame(
            {
                "strategy_id": ["alpha", "beta", "gamma"] * 2,
                "decision_timestamp": pd.to_datetime(["2026-08-22 09:00Z"] * 3 + ["2026-08-22 10:00Z"] * 3),
                "outcome_available_at": pd.to_datetime(["2026-08-22 10:00Z"] * 3 + ["2026-08-22 11:00Z"] * 3),
                "signal": [1] * 6,
                "realized_return": [1.0, -1.0, 0.0, -1.0, 1.0, 0.0],
                "cost": [0.0] * 6,
            }
        ),
        evaluations,
    )

    batch = fixed_share_update(
        initial,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 11, tzinfo=UTC),
        config=config,
    )
    first = fixed_share_update(
        initial,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
        config=config,
    )
    incremental = fixed_share_update(
        first,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 11, tzinfo=UTC),
        config=config,
    )

    assert incremental == batch


def test_same_timestamp_partial_feedback_is_partition_invariant_and_replay_idempotent() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(
        maximum_strategy_weight=0.8,
        maximum_family_weight=0.8,
        fixed_share=0.1,
        learning_rate=10,
    )
    initial = compute_evidence_weights(evaluations, as_of=datetime(2026, 8, 22, 9, tzinfo=UTC), config=config)
    outcomes = _with_context(
        pd.DataFrame(
            {
                "strategy_id": ["alpha", "beta", "gamma"],
                "decision_timestamp": pd.to_datetime(["2026-08-22 09:00Z"] * 3),
                "outcome_available_at": pd.to_datetime(["2026-08-22 10:00Z"] * 3),
                "signal": [1, 1, 1],
                "realized_return": [1.0, -1.0, 0.0],
                "cost": [0.0, 0.0, 0.0],
            }
        ),
        evaluations,
    )

    batch = fixed_share_update(
        initial,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
        config=config,
    )
    partial = fixed_share_update(
        initial,
        outcomes.iloc[[0]],
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
        config=config,
    )
    incremental = fixed_share_update(
        partial,
        outcomes.iloc[[1, 2]],
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
        config=config,
    )
    replayed = fixed_share_update(
        incremental,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
        config=config,
    )

    assert incremental == batch
    assert replayed == batch
    assert len(batch[0].provenance["online_state"]["processed_outcome_ids"]) == 3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("realized_return", 0.25),
        ("dataset_hash", "x" * 64),
        ("outcome_available_at", "2026-08-22T12:00:00+00:00"),
        ("signal", 2),
        ("cost", -0.1),
        ("decision_timestamp", "2026-08-22T11:00:00+00:00"),
    ],
)
def test_feedback_rejects_mutated_or_noncausal_persisted_outcome_records(field: str, value: object) -> None:
    evaluations, config, initial, outcomes = _feedback_fixture()
    updated = fixed_share_update(
        initial,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
        config=config,
    )
    state = _thaw(updated[0].provenance["online_state"])
    assert isinstance(state, dict)
    records = list(state["processed_outcomes"])
    record = dict(records[0])
    record[field] = value
    records[0] = record
    state["processed_outcomes"] = tuple(records)
    tampered = _replace_online_state(updated, state)
    next_outcome = _with_context(
        pd.DataFrame(
            {
                "strategy_id": ["alpha"],
                "decision_timestamp": pd.to_datetime(["2026-08-22 10:00Z"]),
                "outcome_available_at": pd.to_datetime(["2026-08-22 11:00Z"]),
                "signal": [1],
                "realized_return": [0.1],
                "cost": [0.0],
            }
        ),
        evaluations,
    )

    with pytest.raises(ValueError, match="persisted outcome"):
        fixed_share_update(
            tampered,
            next_outcome,
            evaluations=evaluations,
            as_of=datetime(2026, 8, 22, 11, tzinfo=UTC),
            config=config,
        )


def test_feedback_rejects_as_of_rollback_before_the_weight_snapshot() -> None:
    evaluations, config, initial, outcomes = _feedback_fixture()
    updated = fixed_share_update(
        initial,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
        config=config,
    )

    with pytest.raises(ValueError, match="as_of"):
        fixed_share_update(
            updated,
            pd.DataFrame(),
            evaluations=evaluations,
            as_of=datetime(2026, 8, 22, 9, tzinfo=UTC),
            config=config,
        )


@pytest.mark.parametrize("mutation", ["base_weight", "prior", "cohort"])
def test_feedback_rejects_mutated_original_weight_cohort(mutation: str) -> None:
    evaluations, config, initial, outcomes = _feedback_fixture()
    updated = fixed_share_update(
        initial,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
        config=config,
    )
    if mutation == "base_weight":
        state = _thaw(updated[0].provenance["online_state"])
        assert isinstance(state, dict)
        rows = list(state["base_weights"])
        rows[0] = {**dict(rows[0]), "weight": 0.01}
        state["base_weights"] = tuple(rows)
        tampered = _replace_online_state(updated, state)
    elif mutation == "prior":
        tampered = (replace(updated[0], prior_weight=0.01), *updated[1:])
    else:
        tampered = (replace(updated[0], strategy_version="forged-version"), *updated[1:])

    with pytest.raises(ValueError, match="cohort|identity"):
        fixed_share_update(
            tampered,
            pd.DataFrame(),
            evaluations=evaluations,
            as_of=datetime(2026, 8, 22, 11, tzinfo=UTC),
            config=config,
        )


def test_empty_feedback_and_combination_reject_mutated_offline_current_mass() -> None:
    evaluations, config, initial, _outcomes = _feedback_fixture()
    tampered = (replace(initial[0], weight=99.0), *initial[1:])

    with pytest.raises(ValueError, match="weight|mass|cohort"):
        fixed_share_update(
            tampered,
            pd.DataFrame(),
            evaluations=evaluations,
            as_of=AS_OF,
            config=config,
        )
    with pytest.raises(ValueError, match="weight|mass|cohort"):
        combine_current_signals(
            evaluations,
            tampered,
            as_of=AS_OF,
            config=config,
            validation_config=TEST_VALIDATION_CONFIG,
        )


def test_combination_rejects_mutated_authenticated_online_current_mass() -> None:
    evaluations, config, initial, outcomes = _feedback_fixture()
    updated = fixed_share_update(
        initial,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
        config=config,
    )
    delta = min(updated[1].weight / 2, 0.01)
    tampered = (
        replace(updated[0], weight=updated[0].weight + delta),
        replace(updated[1], weight=updated[1].weight - delta),
        *updated[2:],
    )
    literal = (replace(updated[0], weight=0.99), *updated[1:])

    with pytest.raises(ValueError, match="weight|mass|state"):
        fixed_share_update(
            literal,
            pd.DataFrame(),
            evaluations=evaluations,
            as_of=AS_OF,
            config=config,
        )
    with pytest.raises(ValueError, match="weight|mass|state"):
        combine_current_signals(
            evaluations,
            tampered,
            as_of=AS_OF,
            config=config,
            validation_config=TEST_VALIDATION_CONFIG,
        )


def test_combination_rederivation_rejects_self_consistent_public_hash_reconstruction() -> None:
    evaluations, config, initial, outcomes = _feedback_fixture()
    offline_values = (initial[0].weight + 0.01, initial[1].weight - 0.01, initial[2].weight)
    snapshot = _thaw(initial[0].provenance["weight_cohort_snapshot"])
    assert isinstance(snapshot, dict)
    members = [dict(member) for member in snapshot["members"]]
    current_rows = [dict(row) for row in snapshot["current_weights"]]
    for member, row, value in zip(members, current_rows, offline_values, strict=True):
        member["base_weight"] = value
        row["weight"] = value
    snapshot["members"] = tuple(members)
    snapshot["current_weights"] = tuple(current_rows)
    snapshot["current_weights_hash"] = canonical_hash(snapshot["current_weights"])
    snapshot_hash = canonical_hash(snapshot)
    forged_offline = []
    for weight, value in zip(initial, offline_values, strict=True):
        provenance = _thaw(weight.provenance)
        assert isinstance(provenance, dict)
        provenance["weight_cohort_snapshot"] = snapshot
        provenance["weight_cohort_hash"] = snapshot_hash
        forged_offline.append(replace(weight, weight=value, provenance=provenance))

    with pytest.raises(ValueError, match="rederived evidence"):
        combine_current_signals(
            evaluations,
            tuple(forged_offline),
            as_of=AS_OF,
            config=config,
            validation_config=TEST_VALIDATION_CONFIG,
        )

    updated = fixed_share_update(
        initial,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
        config=config,
    )
    online_values = (updated[0].weight, updated[1].weight + 0.01, updated[2].weight - 0.01)
    state = _thaw(updated[0].provenance["online_state"])
    assert isinstance(state, dict)
    state_rows = [dict(row) for row in state["current_weights"]]
    for row, value in zip(state_rows, online_values, strict=True):
        row["weight"] = value
    state["current_weights"] = tuple(state_rows)
    state["current_weights_hash"] = canonical_hash(state["current_weights"])
    state.pop("state_hash")
    state["state_hash"] = canonical_hash(state)
    forged_online = []
    for weight, value in zip(updated, online_values, strict=True):
        provenance = _thaw(weight.provenance)
        assert isinstance(provenance, dict)
        provenance["online_state"] = state
        forged_online.append(replace(weight, weight=value, provenance=provenance))

    with pytest.raises(ValueError, match="authenticated replay"):
        combine_current_signals(
            evaluations,
            tuple(forged_online),
            as_of=AS_OF,
            config=config,
            validation_config=TEST_VALIDATION_CONFIG,
        )


@pytest.mark.parametrize(
    "mutation",
    ["current_99", "sum", "cap", "base", "prior"],
)
def test_direct_feedback_rederives_trusted_offline_state_before_empty_return(mutation: str) -> None:
    evaluations, config, initial, _outcomes = _feedback_fixture()
    if mutation == "current_99":
        forged = _forge_offline_weight_state(initial, current_values=(99.0, initial[1].weight, initial[2].weight))
    elif mutation == "sum":
        forged = _forge_offline_weight_state(initial, current_values=(0.2, 0.2, 0.2))
    elif mutation == "cap":
        forged = _forge_offline_weight_state(initial, current_values=(0.9, 0.05, 0.05))
    elif mutation == "base":
        forged = _forge_offline_weight_state(
            initial,
            current_values=(0.4, 0.3, 0.3),
            base_values=(0.4, 0.3, 0.3),
        )
    else:
        forged = _forge_offline_weight_state(initial, prior_values=(0.5, 0.25, 0.25))

    with pytest.raises(ValueError, match="weight|mass|cap|rederived"):
        fixed_share_update(
            forged,
            pd.DataFrame(),
            evaluations=evaluations,
            as_of=AS_OF,
            config=config,
            validation_config=TEST_VALIDATION_CONFIG,
        )


def test_direct_feedback_requires_trusted_evaluations_and_validation_policy() -> None:
    evaluations, config, initial, _outcomes = _feedback_fixture()

    with pytest.raises(TypeError):
        fixed_share_update(initial, pd.DataFrame(), as_of=AS_OF, config=config)
    with pytest.raises(TypeError):
        _fixed_share_update(
            initial,
            pd.DataFrame(),
            evaluations=evaluations,
            as_of=AS_OF,
            config=config,
        )


def test_direct_feedback_empty_and_authenticated_online_replay_are_exact() -> None:
    evaluations, config, initial, outcomes = _feedback_fixture()

    empty = fixed_share_update(
        initial,
        pd.DataFrame(),
        evaluations=evaluations,
        as_of=AS_OF,
        config=config,
        validation_config=TEST_VALIDATION_CONFIG,
    )
    updated = fixed_share_update(
        initial,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
        config=config,
        validation_config=TEST_VALIDATION_CONFIG,
    )
    replayed = fixed_share_update(
        updated,
        pd.DataFrame(),
        evaluations=evaluations,
        as_of=AS_OF,
        config=config,
        validation_config=TEST_VALIDATION_CONFIG,
    )

    assert empty == initial
    assert replayed == updated


@pytest.mark.parametrize("weights", [(0.99, 0.005, 0.005), (0.2, 0.2, 0.2)])
def test_combination_rejects_current_mass_cap_or_normalization_failure(weights: tuple[float, ...]) -> None:
    evaluations, config, initial, _outcomes = _feedback_fixture()
    tampered = tuple(replace(weight, weight=value) for weight, value in zip(initial, weights, strict=True))

    with pytest.raises(ValueError, match="weight|mass|cap|normalized"):
        combine_current_signals(
            evaluations,
            tampered,
            as_of=AS_OF,
            config=config,
            validation_config=TEST_VALIDATION_CONFIG,
        )


def test_outcome_feedback_requires_exact_strategy_and_dataset_context() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8)
    weights = compute_evidence_weights(evaluations, as_of=datetime(2026, 8, 22, 9, tzinfo=UTC), config=config)
    outcomes = _with_context(
        pd.DataFrame(
            {
                "strategy_id": ["alpha", "beta", "gamma"],
                "decision_timestamp": pd.to_datetime(["2026-08-22 09:00Z"] * 3),
                "outcome_available_at": pd.to_datetime(["2026-08-22 10:00Z"] * 3),
                "signal": [1, 1, 1],
                "realized_return": [0.1, -0.1, 0.0],
                "cost": [0.0, 0.0, 0.0],
            }
        ),
        evaluations,
    )
    outcomes.loc[outcomes["strategy_id"] == "alpha", "dataset_hash"] = "x" * 64

    with pytest.raises(ValueError, match="outcome context"):
        fixed_share_update(
            weights,
            outcomes,
            evaluations=evaluations,
            as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
            config=config,
        )


def test_fixed_share_directly_rejects_frozen_and_mixed_weight_modes() -> None:
    evaluations = tuple(
        replace(evaluation, mode=StrategyMode.FROZEN)
        for evaluation in (
            _evaluation("alpha", StrategyFamily.TREND),
            _evaluation("beta", StrategyFamily.MEAN_REVERSION),
            _evaluation("gamma", StrategyFamily.SESSION),
        )
    )
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8)
    frozen = compute_evidence_weights(evaluations, as_of=datetime(2026, 8, 22, 9, tzinfo=UTC), config=config)
    empty = pd.DataFrame()

    with pytest.raises(ValueError, match="frozen"):
        fixed_share_update(
            frozen,
            empty,
            evaluations=evaluations,
            as_of=AS_OF,
            config=config,
        )
    mixed = (frozen[0], replace(frozen[1], mode=StrategyMode.PAPER), frozen[2])
    with pytest.raises(ValueError, match="homogeneous"):
        fixed_share_update(
            mixed,
            empty,
            evaluations=evaluations,
            as_of=AS_OF,
            config=config,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("signal", 2, "signals"),
        ("realized_return", float("inf"), "finite"),
        ("cost", -0.01, "non-negative"),
    ],
)
def test_online_outcome_validation_rejects_invalid_numeric_state(field: str, value: float, message: str) -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8)
    weights = compute_evidence_weights(evaluations, as_of=datetime(2026, 8, 22, 9, tzinfo=UTC), config=config)
    outcomes = _with_context(
        pd.DataFrame(
            {
                "strategy_id": ["alpha", "beta", "gamma"],
                "decision_timestamp": pd.to_datetime(["2026-08-22 09:00Z"] * 3),
                "outcome_available_at": pd.to_datetime(["2026-08-22 10:00Z"] * 3),
                "signal": [1, 1, 1],
                "realized_return": [0.1, -0.1, 0.0],
                "cost": [0.0, 0.0, 0.0],
            }
        ),
        evaluations,
    )
    outcomes.loc[0, field] = value

    with pytest.raises(ValueError, match=message):
        fixed_share_update(
            weights,
            outcomes,
            evaluations=evaluations,
            as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
            config=config,
        )


@pytest.mark.parametrize("timestamp_column", ["decision_timestamp", "outcome_available_at"])
def test_online_outcome_validation_rejects_naive_causal_timestamps(timestamp_column: str) -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8)
    weights = compute_evidence_weights(evaluations, as_of=datetime(2026, 8, 22, 9, tzinfo=UTC), config=config)
    outcomes = _with_context(
        pd.DataFrame(
            {
                "strategy_id": ["alpha", "beta", "gamma"],
                "decision_timestamp": [datetime(2026, 8, 22, 9, tzinfo=UTC)] * 3,
                "outcome_available_at": [datetime(2026, 8, 22, 10, tzinfo=UTC)] * 3,
                "signal": [1, 1, 1],
                "realized_return": [0.1, -0.1, 0.0],
                "cost": [0.0, 0.0, 0.0],
            }
        ),
        evaluations,
    )
    timestamps = list(outcomes[timestamp_column].dt.to_pydatetime())
    timestamps[0] = datetime(2026, 8, 22, 9)
    outcomes[timestamp_column] = pd.Series(timestamps, dtype=object)

    with pytest.raises(ValueError, match="timezone-aware"):
        fixed_share_update(
            weights,
            outcomes,
            evaluations=evaluations,
            as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
            config=config,
        )


@pytest.mark.parametrize(
    ("evaluations", "config", "expected_reason"),
    [
        (
            (_evaluation("only", StrategyFamily.TREND),),
            EnsembleConfig(minimum_breadth=2, maximum_strategy_weight=1, maximum_family_weight=1),
            "minimum_breadth",
        ),
        (
            (
                _evaluation("long", StrategyFamily.TREND, signal=1),
                _evaluation("short", StrategyFamily.MEAN_REVERSION, signal=-1),
            ),
            EnsembleConfig(
                minimum_breadth=2,
                minimum_vote_margin=0.2,
                maximum_strategy_weight=1,
                maximum_family_weight=1,
            ),
            "vote_margin",
        ),
        (
            (
                _evaluation("a", StrategyFamily.TREND, edge=0.001, cost=0.001, uncertainty=0.001),
                _evaluation("b", StrategyFamily.MEAN_REVERSION, edge=0.001, cost=0.001, uncertainty=0.001),
            ),
            EnsembleConfig(minimum_breadth=2, maximum_strategy_weight=1, maximum_family_weight=1),
            "cost_buffer",
        ),
    ],
)
def test_current_decision_abstains_on_breadth_margin_or_cost_buffer(
    evaluations: tuple[StrategyEvaluation, ...],
    config: EnsembleConfig,
    expected_reason: str,
) -> None:
    weights = compute_evidence_weights(evaluations, as_of=AS_OF, config=config)

    decision = combine_current_signals(evaluations, weights, as_of=AS_OF, config=config)

    assert decision.signal == 0
    assert decision.status == "abstain"
    assert expected_reason in decision.reasons


def test_current_decision_requires_a_calibrated_probability_and_emits_long_after_all_gates() -> None:
    evaluations = (
        _evaluation("a", StrategyFamily.TREND),
        _evaluation("b", StrategyFamily.MEAN_REVERSION),
        _evaluation("c", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(
        minimum_breadth=2,
        minimum_vote_margin=0.2,
        minimum_probability=0.7,
        maximum_strategy_weight=0.6,
        maximum_family_weight=0.7,
    )
    weights = compute_evidence_weights(evaluations, as_of=AS_OF, config=config)

    decision = combine_current_signals(evaluations, weights, as_of=AS_OF, config=config)

    assert decision.signal == 1
    assert decision.status == "long"
    assert decision.breadth == 3
    assert decision.vote_margin == pytest.approx(1)
    assert decision.probability == pytest.approx(0.75)
    assert decision.expected_net_edge > 0
    assert len(decision.decision_hash) == 64


def test_current_decision_explicitly_abstains_when_fold_fitted_calibration_is_unavailable() -> None:
    evaluations = tuple(
        replace(_evaluation(name, family), calibration_status="unavailable")
        for name, family in (
            ("a", StrategyFamily.TREND),
            ("b", StrategyFamily.MEAN_REVERSION),
            ("c", StrategyFamily.SESSION),
        )
    )
    config = EnsembleConfig(
        minimum_breadth=2,
        maximum_strategy_weight=0.6,
        maximum_family_weight=0.7,
    )
    weights = compute_evidence_weights(evaluations, as_of=AS_OF, config=config)

    decision = combine_current_signals(evaluations, weights, as_of=AS_OF, config=config)

    assert decision.signal == 0
    assert decision.status == "abstain"
    assert "calibrated_decision_capability_unavailable" in decision.reasons


def test_current_decision_explicitly_abstains_when_cost_evidence_is_unavailable() -> None:
    evaluations = tuple(
        replace(_evaluation(name, family), economic_evidence_status="unavailable")
        for name, family in (
            ("a", StrategyFamily.TREND),
            ("b", StrategyFamily.MEAN_REVERSION),
        )
    )
    config = EnsembleConfig(minimum_breadth=2, maximum_strategy_weight=1, maximum_family_weight=1)
    weights = compute_evidence_weights(evaluations, as_of=AS_OF, config=config)

    decision = combine_current_signals(evaluations, weights, as_of=AS_OF, config=config)

    assert decision.signal == 0
    assert "economic_cost_evidence_unavailable" in decision.reasons


def test_current_decision_rejects_component_data_from_after_the_as_of_boundary() -> None:
    evaluations = (
        replace(_evaluation("future", StrategyFamily.TREND), data_through=AS_OF + timedelta(seconds=1)),
        _evaluation("causal", StrategyFamily.MEAN_REVERSION),
    )
    config = EnsembleConfig(maximum_strategy_weight=1, maximum_family_weight=1)
    weights = compute_evidence_weights(evaluations, as_of=AS_OF, config=config)

    with pytest.raises(ValueError, match="future component data"):
        combine_current_signals(evaluations, weights, as_of=AS_OF, config=config)


def test_current_decision_rejects_future_frozen_weight_snapshot_before_mode_branch() -> None:
    evaluations = tuple(
        _with_mode(evaluation, StrategyMode.FROZEN)
        for evaluation in (
            _evaluation("alpha", StrategyFamily.TREND),
            _evaluation("beta", StrategyFamily.MEAN_REVERSION),
            _evaluation("gamma", StrategyFamily.SESSION),
        )
    )
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8)
    weights = compute_evidence_weights(
        evaluations,
        as_of=AS_OF + timedelta(hours=1),
        config=config,
    )

    with pytest.raises(ValueError, match="as_of|future|chronology"):
        combine_current_signals(evaluations, weights, as_of=AS_OF, config=config)


def test_current_decision_rejects_frozen_future_online_history_before_mode_branch() -> None:
    paper_evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    frozen_evaluations = tuple(_with_mode(evaluation, StrategyMode.FROZEN) for evaluation in paper_evaluations)
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8)
    paper = _with_future_online_history(
        compute_evidence_weights(paper_evaluations, as_of=AS_OF, config=config),
        as_of=AS_OF,
    )
    frozen = _with_future_online_history(
        compute_evidence_weights(frozen_evaluations, as_of=AS_OF, config=config),
        as_of=AS_OF,
    )

    assert all(weight.effective_at <= AS_OF and weight.outcomes_through is None for weight in frozen)
    with pytest.raises(ValueError, match="persisted outcome chronology"):
        combine_current_signals(paper_evaluations, paper, as_of=AS_OF, config=config)
    with pytest.raises(ValueError, match="frozen.*online state"):
        combine_current_signals(frozen_evaluations, frozen, as_of=AS_OF, config=config)


@pytest.mark.parametrize("online_state", [None, {}])
def test_current_decision_rejects_any_present_online_state_on_frozen_weights(online_state: object) -> None:
    evaluations = tuple(
        _with_mode(evaluation, StrategyMode.FROZEN)
        for evaluation in (
            _evaluation("alpha", StrategyFamily.TREND),
            _evaluation("beta", StrategyFamily.MEAN_REVERSION),
            _evaluation("gamma", StrategyFamily.SESSION),
        )
    )
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8)
    weights = compute_evidence_weights(evaluations, as_of=AS_OF, config=config)
    with_state = _replace_online_state(weights, online_state)

    with pytest.raises(ValueError, match="frozen.*online state"):
        combine_current_signals(evaluations, with_state, as_of=AS_OF, config=config)


def test_current_decision_accepts_frozen_weights_without_online_state() -> None:
    evaluations = tuple(
        _with_mode(evaluation, StrategyMode.FROZEN)
        for evaluation in (
            _evaluation("alpha", StrategyFamily.TREND),
            _evaluation("beta", StrategyFamily.MEAN_REVERSION),
            _evaluation("gamma", StrategyFamily.SESSION),
        )
    )
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8)
    weights = compute_evidence_weights(evaluations, as_of=AS_OF, config=config)

    decision = combine_current_signals(evaluations, weights, as_of=AS_OF, config=config)

    assert decision.mode is StrategyMode.FROZEN
    assert decision.signal == 1
    assert all("online_state" not in weight.provenance for weight in weights)


def test_actionable_positive_weight_component_requires_decision_and_data_timestamps() -> None:
    evaluations = (
        replace(
            _evaluation("missing", StrategyFamily.TREND),
            decision_timestamp=None,
            data_through=None,
        ),
        _evaluation("causal", StrategyFamily.MEAN_REVERSION),
    )
    config = EnsembleConfig(maximum_strategy_weight=1, maximum_family_weight=1)
    weights = compute_evidence_weights(evaluations, as_of=AS_OF, config=config)

    with pytest.raises(ValueError, match="actionable component timestamps"):
        combine_current_signals(evaluations, weights, as_of=AS_OF, config=config)


def test_engine_requires_homogeneous_context_and_decision_carries_that_context() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8)
    mixed = (evaluations[0], replace(evaluations[1], dataset_hash="x" * 64), evaluations[2])

    with pytest.raises(ValueError, match="homogeneous evaluation context"):
        generate_current_decision(mixed, pd.DataFrame(), AS_OF, config=config)

    decision = generate_current_decision(evaluations, pd.DataFrame(), AS_OF, config=config)
    assert decision.dataset_hash == "d" * 64
    assert decision.symbol == "AAA"
    assert decision.interval is BarInterval.ONE_HOUR
    assert decision.mode is StrategyMode.PAPER
    assert decision.component_versions == (
        ("alpha", "1.0.0-deadbeef0000"),
        ("beta", "1.0.0-deadbeef0000"),
        ("gamma", "1.0.0-deadbeef0000"),
    )
    with pytest.raises(ValueError, match="does not match decision context"):
        decision_to_signal_frame(decision, symbol="BBB")
    with pytest.raises(ValueError, match="explicit data_through"):
        decision_to_signal_frame(replace(decision, data_through=None), symbol="AAA")


def test_decision_hash_is_permutation_invariant_and_covers_weight_provenance() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8)
    first_weights = compute_evidence_weights(evaluations, as_of=AS_OF, config=config)
    first = combine_current_signals(evaluations, first_weights, as_of=AS_OF, config=config)
    reversed_evaluations = tuple(reversed(evaluations))
    reversed_weights = compute_evidence_weights(reversed_evaluations, as_of=AS_OF, config=config)
    permuted = combine_current_signals(reversed_evaluations, reversed_weights, as_of=AS_OF, config=config)

    assert first.decision_hash == permuted.decision_hash
    shifted_weights = tuple(
        replace(
            weight,
            effective_at=AS_OF - timedelta(hours=1),
            outcomes_through=AS_OF - timedelta(hours=1),
        )
        for weight in first_weights
    )
    with pytest.raises(ValueError, match="current mass"):
        combine_current_signals(evaluations, shifted_weights, as_of=AS_OF, config=config)


def test_decision_hash_is_derived_and_execution_rejects_tampering() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8)
    weights = compute_evidence_weights(evaluations, as_of=AS_OF, config=config)
    decision = combine_current_signals(evaluations, weights, as_of=AS_OF, config=config)

    altered = replace(decision, signal=-1, status="short")

    assert altered.decision_hash != decision.decision_hash
    object.__setattr__(altered, "decision_hash", decision.decision_hash)
    with pytest.raises(ValueError, match="decision hash"):
        decision_to_signal_frame(altered, symbol="AAA")


def test_weight_provenance_is_deeply_immutable() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8)
    weights = compute_evidence_weights(
        evaluations,
        as_of=datetime(2026, 8, 22, 9, tzinfo=UTC),
        config=config,
    )
    trials = weights[0].provenance["trial_sharpes"]
    assert isinstance(trials, tuple)
    with pytest.raises(AttributeError):
        trials.append(99)

    outcomes = _with_context(
        pd.DataFrame(
            {
                "strategy_id": ["alpha", "beta", "gamma"],
                "decision_timestamp": pd.to_datetime(["2026-08-22 09:00Z"] * 3),
                "outcome_available_at": pd.to_datetime(["2026-08-22 10:00Z"] * 3),
                "signal": [1, 1, 1],
                "realized_return": [0.1, -0.1, 0.0],
                "cost": [0.0, 0.0, 0.0],
            }
        ),
        evaluations,
    )
    updated = fixed_share_update(
        weights,
        outcomes,
        evaluations=evaluations,
        as_of=datetime(2026, 8, 22, 10, tzinfo=UTC),
        config=config,
    )
    online_state = updated[0].provenance["online_state"]
    with pytest.raises(TypeError):
        online_state["cumulative_mixability_gap"] = 0
    assert isinstance(online_state["adaptive_learning_rates"], tuple)
