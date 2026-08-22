from __future__ import annotations

from src.models.eligibility import assess_signal_eligibility


def test_wide_interval_forces_abstention():
    result = assess_signal_eligibility(
        signed_strength=1.2,
        interval_width_ratio=0.8,
        model_agreement=0.9,
        completeness=1.0,
        extrapolation=0.0,
        observations=100,
    )

    assert result.posture == "abstain"
    assert "interval" in result.reasons[0].lower()


def test_eligible_direction_requires_all_evidence_gates():
    result = assess_signal_eligibility(
        signed_strength=-1.1,
        interval_width_ratio=0.15,
        model_agreement=0.85,
        completeness=0.95,
        extrapolation=0.1,
        observations=200,
    )

    assert result.eligible
    assert result.posture == "short_research"
    assert result.score > 70
