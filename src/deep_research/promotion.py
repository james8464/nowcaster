from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReliabilityThresholds:
    minimum_trades: int = 300
    minimum_deflated_sharpe_probability: float = 0.99
    minimum_bootstrap_positive_probability: float = 0.99
    maximum_backtest_overfitting_probability: float = 0.10
    minimum_parameter_stability: float = 0.80
    maximum_drawdown: float = 0.10
    maximum_profit_concentration: float = 0.50
    minimum_score_improvement: float = 0.01
    minimum_effective_observations: int = 300
    minimum_rolling_holdouts: int = 3
    minimum_global_trials: int = 2

    def __post_init__(self) -> None:
        if (
            min(
                self.minimum_trades,
                self.minimum_effective_observations,
                self.minimum_rolling_holdouts,
                self.minimum_global_trials,
            )
            < 1
        ):
            raise ValueError("reliability evidence counts must be positive")
        probability_values = (
            self.minimum_deflated_sharpe_probability,
            self.minimum_bootstrap_positive_probability,
            self.maximum_backtest_overfitting_probability,
            self.minimum_parameter_stability,
            self.maximum_drawdown,
            self.maximum_profit_concentration,
        )
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in probability_values):
            raise ValueError("reliability probability and risk thresholds must be finite and in [0, 1]")
        if not math.isfinite(self.minimum_score_improvement) or self.minimum_score_improvement < 0:
            raise ValueError("minimum_score_improvement must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ReliabilityEvidence:
    trade_count: int
    fold_net_returns: tuple[float, ...]
    fold_net_sharpes: tuple[float, ...]
    doubled_cost_return: float
    deflated_sharpe_probability: float
    bootstrap_positive_probability: float
    backtest_overfitting_probability: float
    parameter_stability: float
    maximum_drawdown: float
    profit_concentration: float
    sealed_test_return: float
    causal_audit_passed: bool
    provenance_audit_passed: bool
    coverage_complete: bool
    execution_audit_passed: bool
    candidate_score: float
    incumbent_score: float
    validation_tier: str = "unavailable"
    effective_sample_size: float = math.nan
    lower_net_edge: float = math.nan
    rolling_holdout_returns: tuple[float, ...] = ()
    global_trial_count: int = 0


@dataclass(frozen=True, slots=True)
class ReliabilityDecision:
    promoted: bool
    outcome: str
    score: float | None
    failed_gates: tuple[str, ...]


def _finite(value: float) -> bool:
    return not isinstance(value, bool) and math.isfinite(value)


def evaluate_research_promotion(
    evidence: ReliabilityEvidence,
    thresholds: ReliabilityThresholds | None = None,
) -> ReliabilityDecision:
    thresholds = thresholds or ReliabilityThresholds()
    failures: list[str] = []

    if evidence.validation_tier != "promotion":
        failures.append("promotion validation tier is required")
    if not _finite(evidence.effective_sample_size):
        failures.append("effective observations are unavailable")
    elif evidence.effective_sample_size < thresholds.minimum_effective_observations:
        failures.append(f"minimum {thresholds.minimum_effective_observations} effective observations not met")
    if not _finite(evidence.lower_net_edge):
        failures.append("lower net-edge confidence bound is unavailable")
    elif evidence.lower_net_edge <= 0:
        failures.append("positive lower net-edge confidence bound not met")
    if len(evidence.rolling_holdout_returns) < thresholds.minimum_rolling_holdouts or not all(
        _finite(value) for value in evidence.rolling_holdout_returns
    ):
        failures.append(f"minimum {thresholds.minimum_rolling_holdouts} rolling sealed holdouts not met")
    elif min(evidence.rolling_holdout_returns) <= 0:
        failures.append("rolling sealed holdouts are not uniformly positive")
    if evidence.global_trial_count < thresholds.minimum_global_trials:
        failures.append("global trial ledger is incomplete")

    if evidence.trade_count < thresholds.minimum_trades:
        failures.append(f"minimum {thresholds.minimum_trades} closed trades not met")
    if not evidence.fold_net_returns or not all(_finite(value) for value in evidence.fold_net_returns):
        failures.append("walk-forward net returns are unavailable")
    elif statistics.median(evidence.fold_net_returns) <= 0:
        failures.append("positive median walk-forward net return not met")
    if not evidence.fold_net_sharpes or not all(_finite(value) for value in evidence.fold_net_sharpes):
        failures.append("walk-forward net Sharpes are unavailable")
    elif statistics.median(evidence.fold_net_sharpes) <= 0:
        failures.append("positive median walk-forward net Sharpe not met")
    if not _finite(evidence.doubled_cost_return):
        failures.append("doubled-cost return is unavailable")
    elif evidence.doubled_cost_return <= 0:
        failures.append("positive doubled-cost return not met")

    probability_gates = (
        (
            evidence.deflated_sharpe_probability,
            thresholds.minimum_deflated_sharpe_probability,
            "Deflated Sharpe probability",
            "minimum",
        ),
        (
            evidence.bootstrap_positive_probability,
            thresholds.minimum_bootstrap_positive_probability,
            "bootstrap probability",
            "minimum",
        ),
        (
            evidence.backtest_overfitting_probability,
            thresholds.maximum_backtest_overfitting_probability,
            "backtest-overfitting probability",
            "maximum",
        ),
        (
            evidence.parameter_stability,
            thresholds.minimum_parameter_stability,
            "parameter stability",
            "minimum",
        ),
    )
    for value, threshold, label, direction in probability_gates:
        if not _finite(value):
            failures.append(f"{label} is unavailable")
        elif direction == "minimum" and value < threshold:
            failures.append(f"{label} is below {threshold:.2f}")
        elif direction == "maximum" and value > threshold:
            failures.append(f"{label} exceeds {threshold:.2f}")

    if not _finite(evidence.maximum_drawdown):
        failures.append("maximum drawdown is unavailable")
    elif abs(evidence.maximum_drawdown) > thresholds.maximum_drawdown:
        failures.append(f"maximum drawdown exceeds {thresholds.maximum_drawdown:.0%}")
    if not _finite(evidence.profit_concentration):
        failures.append("profit concentration is unavailable")
    elif evidence.profit_concentration >= thresholds.maximum_profit_concentration:
        failures.append(f"profit concentration is not below {thresholds.maximum_profit_concentration:.0%}")
    if not _finite(evidence.sealed_test_return):
        failures.append("sealed final-test return is unavailable")
    elif evidence.sealed_test_return <= 0:
        failures.append("positive sealed final-test return not met")

    audit_gates = (
        (evidence.causal_audit_passed, "causal audit failed"),
        (evidence.provenance_audit_passed, "provenance audit failed"),
        (evidence.coverage_complete, "dataset coverage is incomplete"),
        (evidence.execution_audit_passed, "execution audit failed"),
    )
    failures.extend(reason for passed, reason in audit_gates if not passed)

    if not _finite(evidence.candidate_score) or not _finite(evidence.incumbent_score):
        failures.append("champion comparison score is unavailable")
    elif evidence.candidate_score < evidence.incumbent_score + thresholds.minimum_score_improvement:
        failures.append("material improvement over the incumbent was not demonstrated")

    promoted = not failures
    return ReliabilityDecision(
        promoted=promoted,
        outcome="research_champion_found" if promoted else "no_reliable_strategy_found",
        score=evidence.candidate_score if _finite(evidence.candidate_score) else None,
        failed_gates=tuple(failures),
    )


__all__ = [
    "ReliabilityDecision",
    "ReliabilityEvidence",
    "ReliabilityThresholds",
    "evaluate_research_promotion",
]
