from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReadinessInputs:
    trades: int
    development_sharpe: float
    final_test_sharpe: float
    probability_positive: float
    deflated_sharpe_probability: float | None
    cost_stress_return: float
    subperiod_positive_fraction: float
    maximum_drawdown: float


@dataclass(frozen=True)
class ReadinessAssessment:
    readiness: str
    score: float
    reasons: tuple[str, ...]
    inputs: ReadinessInputs


def evaluate_readiness(inputs: ReadinessInputs) -> ReadinessAssessment:
    reasons: list[str] = []
    hard_failures: list[str] = []
    if inputs.trades < 100:
        reasons.append("The out-of-sample trade sample is below 100")
        if inputs.trades < 60:
            hard_failures.append("Sample is too small for promotion")
    if inputs.development_sharpe < 0.75:
        reasons.append("Development Sharpe is below the declared 0.75 gate")
    if inputs.final_test_sharpe < 0.5:
        reasons.append("Isolated final-test Sharpe is below the declared 0.5 gate")
        if inputs.final_test_sharpe <= 0:
            hard_failures.append("Final-test risk-adjusted return is non-positive")
    if inputs.probability_positive < 0.95:
        reasons.append("Block-bootstrap probability of a positive mean is below 95%")
    if inputs.deflated_sharpe_probability is None:
        reasons.append("Deflated Sharpe is unavailable without observed candidate trial Sharpes")
    elif inputs.deflated_sharpe_probability < 0.95:
        reasons.append("Deflated Sharpe probability is below 95% after trial adjustment")
    if inputs.cost_stress_return <= 0:
        reasons.append("Performance does not survive the declared high-cost stress")
        hard_failures.append("High-cost stress return is non-positive")
    if inputs.subperiod_positive_fraction < 0.75:
        reasons.append("Fewer than 75% of subperiods are profitable")
    if inputs.maximum_drawdown < -0.3:
        reasons.append("Maximum drawdown exceeds the 30% risk limit")
        hard_failures.append("Drawdown exceeds the hard risk limit")

    gates = 8 - len(reasons)
    score = max(0.0, min(100.0, gates / 8 * 100))
    if hard_failures:
        readiness = "not_ready"
    elif not reasons:
        readiness = "decision_ready"
    else:
        readiness = "research_only"
    return ReadinessAssessment(readiness, score, tuple([*hard_failures, *reasons]), inputs)
