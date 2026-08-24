from __future__ import annotations

import pytest

from src.deep_research.promotion import (
    ReliabilityEvidence,
    ReliabilityThresholds,
    evaluate_research_promotion,
)


def _evidence(**changes: object) -> ReliabilityEvidence:
    values: dict[str, object] = {
        "trade_count": 350,
        "fold_net_returns": (0.02, 0.03, 0.01, 0.025),
        "fold_net_sharpes": (1.1, 1.3, 1.0, 1.2),
        "doubled_cost_return": 0.04,
        "deflated_sharpe_probability": 0.995,
        "bootstrap_positive_probability": 0.996,
        "backtest_overfitting_probability": 0.05,
        "parameter_stability": 0.9,
        "maximum_drawdown": 0.08,
        "profit_concentration": 0.4,
        "sealed_test_return": 0.02,
        "causal_audit_passed": True,
        "provenance_audit_passed": True,
        "coverage_complete": True,
        "execution_audit_passed": True,
        "candidate_score": 1.1,
        "incumbent_score": 1.0,
    }
    values.update(changes)
    return ReliabilityEvidence(**values)  # type: ignore[arg-type]


def test_candidate_must_pass_every_default_gate_before_research_promotion() -> None:
    decision = evaluate_research_promotion(_evidence(), ReliabilityThresholds())

    assert decision.promoted is True
    assert decision.outcome == "research_champion_found"
    assert decision.failed_gates == ()


@pytest.mark.parametrize(
    ("change", "expected_gate"),
    [
        ({"trade_count": 299}, "minimum 300 closed trades"),
        ({"fold_net_returns": (0.1, -0.2)}, "positive median walk-forward net return"),
        ({"fold_net_sharpes": (0.1, -0.2)}, "positive median walk-forward net Sharpe"),
        ({"doubled_cost_return": 0.0}, "positive doubled-cost return"),
        ({"deflated_sharpe_probability": 0.989}, "Deflated Sharpe probability"),
        ({"bootstrap_positive_probability": 0.989}, "bootstrap probability"),
        ({"backtest_overfitting_probability": 0.101}, "backtest-overfitting probability"),
        ({"parameter_stability": 0.79}, "parameter stability"),
        ({"maximum_drawdown": 0.101}, "maximum drawdown"),
        ({"profit_concentration": 0.5}, "profit concentration"),
        ({"sealed_test_return": 0.0}, "positive sealed final-test return"),
        ({"causal_audit_passed": False}, "causal audit"),
        ({"candidate_score": 1.009}, "material improvement"),
    ],
)
def test_each_hard_gate_fails_closed(change: dict[str, object], expected_gate: str) -> None:
    decision = evaluate_research_promotion(_evidence(**change), ReliabilityThresholds())

    assert decision.promoted is False
    assert any(expected_gate in reason for reason in decision.failed_gates)


def test_nonfinite_evidence_is_unavailable_instead_of_favorable() -> None:
    decision = evaluate_research_promotion(
        _evidence(deflated_sharpe_probability=float("nan")),
        ReliabilityThresholds(),
    )

    assert decision.promoted is False
    assert "Deflated Sharpe probability is unavailable" in decision.failed_gates
