from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import insert, select, update

from src.database.engine import Database
from src.database.schema import TABLES
from src.live_monitor.evidence import (
    LIVE_READINESS_POLICY,
    ActiveReadinessGate,
    ActiveReadinessReceipt,
    SealedCohort,
    derive_live_readiness_robustness,
    live_readiness_evidence_hash,
    live_readiness_policy_hash,
    selected_cohort_hash,
)
from src.models.drift import DEFAULT_DRIFT_POLICY_HASH
from src.strategies.types import canonical_hash
from src.trading.forward import ForwardCohortIdentity, ForwardDailyEvidence
from src.trading.readiness import ReadinessEvaluator, ReadinessPolicy


class LiveReadinessQualification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["eligible", "locked"]
    cohort_hash: str
    gates: tuple[ActiveReadinessGate, ...]
    receipt: ActiveReadinessReceipt | None = None


def _forward_evidence(database: Database, cohort_hash: str) -> tuple[ForwardDailyEvidence, ...]:
    frame = database.frame(
        "select evidence from forward_evidence_daily where cohort_hash = :cohort_hash "
        "order by period_start, period_end",
        {"cohort_hash": cohort_hash},
    )
    return tuple(
        ForwardDailyEvidence.model_validate(item)
        for item in (() if frame.empty else frame["evidence"])
        if isinstance(item, dict)
    )


def _identity(cohorts: tuple[SealedCohort, ...]) -> ForwardCohortIdentity:
    selection_hash = selected_cohort_hash(cohorts)
    providers = sorted({item.provider for item in cohorts})
    feeds = sorted({item.feed for item in cohorts})
    symbols = sorted({item.symbol for item in cohorts})
    intervals = sorted({item.interval for item in cohorts})
    return ForwardCohortIdentity(
        asset_class="crypto" if any(item.provider == "binance" for item in cohorts) else "equity",
        provider="|".join(providers),
        feed="|".join(feeds),
        symbol="|".join(symbols),
        interval="|".join(intervals),
        strategy_id="sealed_live_ensemble",
        strategy_version="1",
        parameters_hash=canonical_hash([item.model_dump(mode="json") for item in cohorts]),
        weights_hash=canonical_hash([[str(component.weight) for component in item.components] for item in cohorts]),
        dataset_hash=canonical_hash(sorted(item.dataset_hash for item in cohorts)),
        code_hash=canonical_hash(sorted(component.model_hash for item in cohorts for component in item.components)),
        config_hash=selection_hash,
        risk_policy_hash=live_readiness_policy_hash(cohorts),
        cost_policy_hash=canonical_hash([(item.cohort_id, str(item.cost_buffer_multiplier)) for item in cohorts]),
        selection_hash=selection_hash,
    )


def evaluate_and_persist_live_readiness(
    database: Database,
    cohorts: tuple[SealedCohort, ...],
    *,
    as_of: datetime | None = None,
) -> LiveReadinessQualification:
    """Issue a receipt only from sealed database evidence; no caller metrics are accepted."""
    if not cohorts:
        raise ValueError("qualified live readiness requires at least one sealed cohort")
    evaluated_at = as_of or datetime.now(UTC)
    if evaluated_at.tzinfo is not UTC:
        raise ValueError("live readiness evaluation requires explicit UTC")
    identity = _identity(cohorts)
    evidence = _forward_evidence(database, identity.cohort_hash)
    forward_rows = tuple(item.model_dump(mode="json") for item in evidence)
    robustness = derive_live_readiness_robustness(cohorts, forward_rows)
    policy = ReadinessPolicy(**LIVE_READINESS_POLICY)
    evaluation = ReadinessEvaluator(policy).evaluate(identity, evidence, robustness, as_of=evaluated_at)
    gates = tuple(ActiveReadinessGate.model_validate(item.model_dump()) for item in evaluation.gates)
    if evaluation.receipt is None:
        return LiveReadinessQualification(status="locked", cohort_hash=identity.cohort_hash, gates=gates)
    evidence_hash = live_readiness_evidence_hash(cohorts, forward_rows)
    policy_hash = live_readiness_policy_hash(cohorts)
    receipt = ActiveReadinessReceipt(
        receipt_id=canonical_hash((identity.cohort_hash, evidence_hash, policy_hash, evaluated_at)),
        cohort_hash=identity.cohort_hash,
        evidence_hash=evidence_hash,
        policy_hash=policy_hash,
        drift_policy_hash=DEFAULT_DRIFT_POLICY_HASH,
        gates=gates,
        issued_at=evaluated_at,
        expires_at=evaluation.receipt.expires_at,
    )
    table = TABLES["readiness_receipts"]
    with database.engine.begin() as connection:
        connection.execute(
            update(table)
            .where(table.c.cohort_hash == identity.cohort_hash, table.c.invalidated_at.is_(None))
            .values(status="invalidated", invalidated_at=evaluated_at, invalidation_reason="superseded")
        )
        existing = connection.execute(
            select(table.c.readiness_receipt_id).where(table.c.readiness_receipt_id == receipt.receipt_id)
        ).scalar_one_or_none()
        if existing is None:
            connection.execute(
                insert(table).values(
                    readiness_receipt_id=receipt.receipt_id,
                    cohort_hash=receipt.cohort_hash,
                    evidence_hash=receipt.evidence_hash,
                    policy_hash=receipt.policy_hash,
                    drift_policy_hash=receipt.drift_policy_hash,
                    gates=[item.model_dump(mode="json") for item in receipt.gates],
                    issued_at=receipt.issued_at,
                    expires_at=receipt.expires_at,
                    status="active",
                    invalidated_at=None,
                    invalidation_reason=None,
                    source="live_readiness_workflow",
                    source_version="3",
                    created_at=evaluated_at,
                )
            )
    return LiveReadinessQualification(status="eligible", cohort_hash=identity.cohort_hash, gates=gates, receipt=receipt)


def invalidate_readiness_for_drift(
    database: Database,
    *,
    cohort_hash: str,
    drift_evidence_hash: str,
    drift_policy_hash: str,
    invalidated_at: datetime,
) -> bool:
    """Atomically invalidate the active receipt after confirmed material drift."""
    for label, value in {
        "cohort": cohort_hash,
        "drift evidence": drift_evidence_hash,
        "drift policy": drift_policy_hash,
    }.items():
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError(f"{label} hash is invalid")
    if invalidated_at.tzinfo is not UTC:
        raise ValueError("drift invalidation requires explicit UTC")
    if drift_policy_hash != DEFAULT_DRIFT_POLICY_HASH:
        raise ValueError("drift policy does not match the active safety policy")
    table = TABLES["readiness_receipts"]
    with database.engine.begin() as connection:
        receipt_id = connection.execute(
            select(table.c.readiness_receipt_id).where(
                table.c.cohort_hash == cohort_hash,
                table.c.status == "active",
                table.c.invalidated_at.is_(None),
                table.c.drift_policy_hash == drift_policy_hash,
            )
        ).scalar_one_or_none()
        if receipt_id is None:
            return False
        connection.execute(
            update(table)
            .where(table.c.readiness_receipt_id == receipt_id)
            .values(
                status="invalidated",
                invalidated_at=invalidated_at,
                invalidation_reason=f"material_model_drift:{drift_evidence_hash}",
            )
        )
    return True


__all__ = [
    "LiveReadinessQualification",
    "evaluate_and_persist_live_readiness",
    "invalidate_readiness_for_drift",
]
