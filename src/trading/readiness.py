from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

from src.strategies.types import canonical_hash
from src.trading.forward import ForwardCohortIdentity, ForwardDailyEvidence


class ReadinessPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_equity_sessions: int = Field(default=60, ge=60)
    minimum_crypto_days: int = Field(default=90, ge=90)
    minimum_closed_trades: int = Field(default=100, ge=100)
    minimum_bootstrap_probability: Decimal = Field(default=Decimal("0.95"), ge=0, le=1)
    minimum_deflated_sharpe_probability: Decimal = Field(default=Decimal("0.95"), ge=0, le=1)
    maximum_pbo: Decimal = Field(default=Decimal("0.40"), ge=0, le=1)
    minimum_parameter_stability: Decimal = Field(default=Decimal("0.70"), ge=0, le=1)
    maximum_slippage_model_error: Decimal = Field(default=Decimal("0.20"), ge=0)
    receipt_hours: int = Field(default=24, ge=1, le=24)


class ReadinessGate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    passed: bool
    detail: str


class ReadinessReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: str
    cohort_hash: str
    evidence_hash: str
    policy_hash: str
    gates: tuple[ReadinessGate, ...]
    issued_at: datetime
    expires_at: datetime

    def valid_at(self, instant: datetime, *, cohort_hash: str) -> bool:
        return (
            instant.tzinfo is not None
            and instant.utcoffset() == timedelta(0)
            and self.issued_at <= instant < self.expires_at
            and cohort_hash == self.cohort_hash
            and all(gate.passed for gate in self.gates)
        )


class ReadinessEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    gates: tuple[ReadinessGate, ...]
    receipt: ReadinessReceipt | None

    def gate(self, name: str) -> ReadinessGate:
        return next(item for item in self.gates if item.name == name)


class ReadinessEvaluator:
    def __init__(self, policy: ReadinessPolicy | None = None):
        self.policy = policy or ReadinessPolicy()

    def evaluate(
        self,
        cohort: ForwardCohortIdentity,
        evidence: tuple[ForwardDailyEvidence, ...],
        robustness: dict[str, object],
        *,
        as_of: datetime,
    ) -> ReadinessEvaluation:
        if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
            raise ValueError("readiness evaluation requires explicit UTC")
        ordered = tuple(sorted(evidence, key=lambda item: (item.period_start, item.period_end)))
        periods_required = (
            self.policy.minimum_equity_sessions if cohort.asset_class == "equity" else self.policy.minimum_crypto_days
        )
        trades = sum(item.closed_trades for item in ordered)
        cohort_matches = bool(ordered) and all(item.cohort_hash == cohort.cohort_hash for item in ordered)
        operational = cohort_matches and all(
            item.status == "complete"
            and item.reconciliation_mismatches == 0
            and item.health_breakers == 0
            and item.execution_model_status == "calibrated"
            and item.execution_observations >= item.closed_trades
            and item.execution_effective_observations >= item.closed_trades
            and item.execution_error_upper_ratio is not None
            and item.execution_error_upper_ratio <= self.policy.maximum_slippage_model_error
            for item in ordered
        )
        stressed_total = sum((item.stressed_net_return or Decimal(0) for item in ordered), Decimal(0))
        paper_total = sum((item.paper_net_return or Decimal(0) for item in ordered), Decimal(0))
        robustness_match = robustness.get("cohort_hash") == cohort.cohort_hash
        causal = robustness_match and robustness.get("causal_passed") is True
        try:
            robustness_passed = robustness_match and all(
                (
                    Decimal(str(robustness["bootstrap_probability_positive"]))
                    >= self.policy.minimum_bootstrap_probability,
                    Decimal(str(robustness["deflated_sharpe_probability"]))
                    >= self.policy.minimum_deflated_sharpe_probability,
                    Decimal(str(robustness["pbo"])) <= self.policy.maximum_pbo,
                    Decimal(str(robustness["parameter_stability"])) >= self.policy.minimum_parameter_stability,
                    Decimal(str(robustness["slippage_model_error"])) <= self.policy.maximum_slippage_model_error,
                )
            )
        except (InvalidOperation, KeyError, TypeError, ValueError):
            robustness_passed = False
        gates = tuple(
            sorted(
                (
                    ReadinessGate(
                        name="cohort_integrity",
                        passed=cohort_matches and robustness_match,
                        detail="all evidence identities match the frozen cohort",
                    ),
                    ReadinessGate(
                        name="minimum_forward_observations",
                        passed=len(ordered) >= periods_required and trades >= self.policy.minimum_closed_trades,
                        detail=(
                            f"{len(ordered)}/{periods_required} periods; "
                            f"{trades}/{self.policy.minimum_closed_trades} trades"
                        ),
                    ),
                    ReadinessGate(
                        name="operational_integrity",
                        passed=operational,
                        detail="no unresolved reconciliation or health breaker",
                    ),
                    ReadinessGate(
                        name="causal_integrity",
                        passed=causal,
                        detail="authenticated causal evidence matches cohort",
                    ),
                    ReadinessGate(
                        name="positive_paper_edge",
                        passed=paper_total > 0,
                        detail="aggregate observed paper return must be positive",
                    ),
                    ReadinessGate(
                        name="stressed_net_edge",
                        passed=stressed_total > 0,
                        detail="aggregate return remains positive under live-cost stress",
                    ),
                    ReadinessGate(
                        name="robustness",
                        passed=bool(robustness_passed),
                        detail="bootstrap, DSR, PBO, stability, and slippage gates",
                    ),
                ),
                key=lambda item: item.name,
            )
        )
        if not all(gate.passed for gate in gates):
            return ReadinessEvaluation(status="locked", gates=gates, receipt=None)
        evidence_hash = canonical_hash(tuple(item.model_dump(mode="json") for item in ordered))
        policy_hash = canonical_hash(self.policy.model_dump(mode="json"))
        receipt = ReadinessReceipt(
            receipt_id=canonical_hash((cohort.cohort_hash, evidence_hash, policy_hash, as_of)),
            cohort_hash=cohort.cohort_hash,
            evidence_hash=evidence_hash,
            policy_hash=policy_hash,
            gates=gates,
            issued_at=as_of,
            expires_at=as_of + timedelta(hours=self.policy.receipt_hours),
        )
        return ReadinessEvaluation(status="eligible", gates=gates, receipt=receipt)


__all__ = [
    "ReadinessEvaluation",
    "ReadinessEvaluator",
    "ReadinessGate",
    "ReadinessPolicy",
    "ReadinessReceipt",
]
