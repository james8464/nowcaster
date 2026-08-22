from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from src.strategies.ensemble import (
    EnsembleConfig,
    combine_current_signals,
    compute_evidence_weights,
    fixed_share_update,
)
from src.strategies.types import BarInterval, StrategyFamily, StrategyMode
from src.strategies.validation import EvaluationStatus, PromotionDecision, StrategyEvaluation

AS_OF = datetime(2026, 8, 22, 12, tzinfo=UTC)


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
    return StrategyEvaluation(
        strategy_id=strategy_id,
        strategy_version="1.0.0-deadbeef0000",
        family=family,
        status=EvaluationStatus.EVALUATED,
        status_reason="evaluation completed",
        promotion=PromotionDecision(promoted, () if promoted else ("promotion failed",)),
        development_sharpe=sharpe,
        final_sharpe=-99.0,
        downside_risk=0.02,
        calibration_error=0.1,
        fold_stability=0.8,
        cost_survives=True,
        observations=100,
        trades=20,
        dsr_probability=0.9,
        trial_sharpes=trial_sharpes,
        causal_audit_passed=causal,
        current_signal=signal,
        current_strength=strength,
        current_probability=0.75 if signal > 0 else 0.25 if signal < 0 else 0.5,
        expected_edge=edge,
        expected_cost=cost,
        uncertainty=uncertainty,
        decision_timestamp=AS_OF,
        data_through=AS_OF,
        dataset_hash="d" * 64,
        symbol="AAA",
        interval=BarInterval.ONE_HOUR,
        mode=StrategyMode.PAPER,
    )


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


def test_fixed_share_updates_only_resolved_outcomes_and_conserves_mass() -> None:
    evaluations = (
        _evaluation("alpha", StrategyFamily.TREND),
        _evaluation("beta", StrategyFamily.MEAN_REVERSION),
        _evaluation("gamma", StrategyFamily.SESSION),
    )
    config = EnsembleConfig(maximum_strategy_weight=0.8, maximum_family_weight=0.8, fixed_share=0.1, learning_rate=2)
    initial = compute_evidence_weights(evaluations, as_of=datetime(2026, 8, 22, 10, tzinfo=UTC), config=config)
    outcomes = pd.DataFrame(
        {
            "strategy_id": ["alpha", "beta", "gamma", "alpha", "beta", "gamma"],
            "decision_timestamp": pd.to_datetime(["2026-08-22 10:00Z"] * 3 + ["2026-08-22 11:00Z"] * 3),
            "outcome_available_at": pd.to_datetime(["2026-08-22 11:00Z"] * 3 + ["2026-08-22 13:00Z"] * 3),
            "signal": [1, 1, 1, 1, 1, 1],
            "realized_return": [0.02, -0.02, 0.0, -1.0, 1.0, 1.0],
            "cost": [0.001] * 6,
        }
    )

    first = fixed_share_update(initial, outcomes, as_of=datetime(2026, 8, 22, 11, tzinfo=UTC), config=config)
    changed = outcomes.copy()
    changed.loc[changed["outcome_available_at"] > pd.Timestamp("2026-08-22 11:00Z"), "realized_return"] *= -100
    unchanged = fixed_share_update(initial, changed, as_of=datetime(2026, 8, 22, 11, tzinfo=UTC), config=config)

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
    outcomes = pd.DataFrame(
        {
            "strategy_id": ["alpha", "beta", "gamma"] * 2,
            "decision_timestamp": pd.to_datetime(["2026-08-22 09:00Z"] * 3 + ["2026-08-22 10:00Z"] * 3),
            "outcome_available_at": pd.to_datetime(["2026-08-22 10:00Z"] * 3 + ["2026-08-22 11:00Z"] * 3),
            "signal": [1] * 6,
            "realized_return": [1.0, -1.0, 0.0, -1.0, 1.0, 0.0],
            "cost": [0.0] * 6,
        }
    )

    updated = fixed_share_update(
        initial,
        outcomes,
        as_of=datetime(2026, 8, 22, 11, tzinfo=UTC),
        config=config,
    )

    rates = updated[0].provenance["adaptive_learning_rates"]
    assert len(rates) == 2
    assert rates[0] == 10
    assert 0 < rates[1] < rates[0]
    assert updated[0].provenance["cumulative_mixability_gap"] > 0


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


def test_current_decision_rejects_component_data_from_after_the_as_of_boundary() -> None:
    evaluations = (
        replace(_evaluation("future", StrategyFamily.TREND), data_through=AS_OF + timedelta(seconds=1)),
        _evaluation("causal", StrategyFamily.MEAN_REVERSION),
    )
    config = EnsembleConfig(maximum_strategy_weight=1, maximum_family_weight=1)
    weights = compute_evidence_weights(evaluations, as_of=AS_OF, config=config)

    with pytest.raises(ValueError, match="future component data"):
        combine_current_signals(evaluations, weights, as_of=AS_OF, config=config)
