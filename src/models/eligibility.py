from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EligibilityAssessment:
    eligible: bool
    posture: str
    score: float
    reasons: tuple[str, ...]


def assess_signal_eligibility(
    *,
    signed_strength: float,
    interval_width_ratio: float,
    model_agreement: float,
    completeness: float,
    extrapolation: float,
    observations: int,
) -> EligibilityAssessment:
    reasons: list[str] = []
    if interval_width_ratio > 0.5:
        reasons.append("Prediction interval is too wide for an eligible research signal")
    if model_agreement < 0.6:
        reasons.append("Eligible models do not agree on direction")
    if completeness < 0.8:
        reasons.append("Point-in-time feature coverage is incomplete")
    if extrapolation > 0.5:
        reasons.append("Feature values are too far outside prior training support")
    if observations < 60:
        reasons.append("Training and calibration sample is too small")
    if abs(signed_strength) < 0.5:
        reasons.append("Signal magnitude does not clear the declared threshold")
    score = 100 * (
        0.30 * (1 - min(max(interval_width_ratio, 0.0), 1.0))
        + 0.25 * min(max(model_agreement, 0.0), 1.0)
        + 0.20 * min(max(completeness, 0.0), 1.0)
        + 0.15 * (1 - min(max(extrapolation, 0.0), 1.0))
        + 0.10 * min(max(observations / 200, 0.0), 1.0)
    )
    if reasons:
        return EligibilityAssessment(False, "abstain", score, tuple(reasons))
    posture = "long_research" if signed_strength > 0 else "short_research"
    return EligibilityAssessment(True, posture, score, ())
