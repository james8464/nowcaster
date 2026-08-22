from __future__ import annotations

from src.backtest.readiness import ReadinessInputs, evaluate_readiness


def test_readiness_requires_sample_stability_costs_and_final_test() -> None:
    ready = evaluate_readiness(
        ReadinessInputs(
            trades=250,
            development_sharpe=1.2,
            final_test_sharpe=0.8,
            probability_positive=0.98,
            deflated_sharpe_probability=0.96,
            cost_stress_return=0.05,
            subperiod_positive_fraction=0.8,
            maximum_drawdown=-0.15,
        )
    )
    assert ready.readiness == "decision_ready"
    failed = evaluate_readiness(ready.inputs.__class__(**{**ready.inputs.__dict__, "trades": 20}))
    assert failed.readiness == "not_ready"
    assert any("sample" in reason.lower() for reason in failed.reasons)


def test_borderline_evidence_remains_research_only() -> None:
    assessment = evaluate_readiness(
        ReadinessInputs(
            trades=120,
            development_sharpe=0.7,
            final_test_sharpe=0.2,
            probability_positive=0.91,
            deflated_sharpe_probability=0.82,
            cost_stress_return=0.01,
            subperiod_positive_fraction=0.6,
            maximum_drawdown=-0.24,
        )
    )
    assert assessment.readiness == "research_only"


def test_unavailable_deflated_sharpe_is_an_explicit_conservative_readiness_reason() -> None:
    assessment = evaluate_readiness(
        ReadinessInputs(
            trades=120,
            development_sharpe=1.0,
            final_test_sharpe=0.8,
            probability_positive=0.98,
            deflated_sharpe_probability=None,
            cost_stress_return=0.1,
            subperiod_positive_fraction=1.0,
            maximum_drawdown=-0.1,
        )
    )

    assert assessment.readiness == "research_only"
    assert any("Deflated Sharpe is unavailable" in reason for reason in assessment.reasons)
