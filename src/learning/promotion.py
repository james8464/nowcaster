from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from src.learning.search import RuleCandidate
from src.strategies.validation import PromotionDecision


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is not UTC:
        raise ValueError(f"{name} must be an explicit UTC datetime")
    return value


@dataclass(frozen=True, slots=True)
class ForwardEvidence:
    candidate_hash: str
    candidate_version: str
    period_start: datetime
    period_end: datetime
    evaluated_at: datetime
    causal_audit_passed: bool
    causal_audited_at: datetime
    validation: PromotionDecision
    outer_block_inspected: bool
    outer_block_consumed: bool

    def __post_init__(self) -> None:
        if not self.candidate_hash.strip() or not self.candidate_version.strip():
            raise ValueError("forward evidence candidate identity must not be empty")
        for name in ("period_start", "period_end", "evaluated_at", "causal_audited_at"):
            _utc(getattr(self, name), f"forward evidence {name}")
        if not self.period_start < self.period_end <= self.causal_audited_at <= self.evaluated_at:
            raise ValueError("forward evidence chronology is malformed")
        for name in ("causal_audit_passed", "outer_block_inspected", "outer_block_consumed"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("forward evidence flags must be booleans")
        if not isinstance(self.validation, PromotionDecision):
            raise ValueError("forward evidence requires the normal validation decision")


def promote_candidate(candidate: RuleCandidate, evidence: ForwardEvidence) -> PromotionDecision:
    """Evaluate immutable forward evidence without mutating an active or shadow rule."""

    reasons: list[str] = []
    if evidence.candidate_hash != candidate.candidate_hash or evidence.candidate_version != candidate.version:
        reasons.append("forward evidence does not match the immutable candidate version")
    if candidate.state not in {"shadow", "paper"}:
        reasons.append("active or retired rules cannot be mutated by learning mode")
    if candidate.evidence_through is None:
        reasons.append("candidate development evidence boundary is unavailable")
    elif evidence.period_start <= candidate.evidence_through:
        reasons.append("promotion requires a genuinely new forward period")
    if not evidence.outer_block_inspected:
        reasons.append("forward outer block has not been inspected")
    if evidence.outer_block_consumed:
        reasons.append("forward outer block has already been consumed")
    if not evidence.causal_audit_passed:
        reasons.append("causal audit failed")
    if not evidence.validation.promoted:
        reasons.extend(evidence.validation.reasons or ("normal evidence gates failed",))
    return PromotionDecision(not reasons, tuple(dict.fromkeys(reasons)))


__all__ = ["ForwardEvidence", "PromotionDecision", "promote_candidate"]
