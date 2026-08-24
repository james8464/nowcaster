from __future__ import annotations

import pytest

from src.deep_research.stress import REQUIRED_SCENARIOS, evaluate_stress_matrix


def test_stress_matrix_is_seeded_complete_and_conservative() -> None:
    gross_returns = [0.003, -0.001, 0.002, 0.004, -0.002] * 20
    costs = [0.0005] * len(gross_returns)

    first = evaluate_stress_matrix(gross_returns, costs, seed=9, liquidity_observed=True)
    second = evaluate_stress_matrix(gross_returns, costs, seed=9, liquidity_observed=True)

    assert first == second
    assert {scenario.name for scenario in first.scenarios} == set(REQUIRED_SCENARIOS)
    baseline = first.by_name("baseline")
    skipped = first.by_name("skipped_best_trades")
    assert skipped.cumulative_return <= baseline.cumulative_return
    assert first.evidence_grade == "observed_liquidity"


def test_missing_liquidity_inputs_lower_the_evidence_grade() -> None:
    report = evaluate_stress_matrix([0.01, -0.005] * 20, [0.001] * 40, seed=1, liquidity_observed=False)
    assert report.evidence_grade == "conservative_default_liquidity"
    assert "observed liquidity" in report.caveat


@pytest.mark.parametrize(
    ("returns", "costs"),
    [([], []), ([0.1], []), ([0.1, float("nan")], [0.01, 0.01]), ([0.1], [-0.01])],
)
def test_stress_matrix_rejects_incomplete_or_nonfinite_inputs(returns: list[float], costs: list[float]) -> None:
    with pytest.raises(ValueError):
        evaluate_stress_matrix(returns, costs)
