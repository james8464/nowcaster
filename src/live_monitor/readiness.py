from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update

from src.database.engine import Database
from src.database.schema import TABLES
from src.models.drift import DEFAULT_DRIFT_POLICY_HASH


def invalidate_readiness_for_drift(
    database: Database,
    *,
    cohort_hash: str,
    drift_evidence_hash: str,
    drift_policy_hash: str,
    invalidated_at: datetime,
) -> bool:
    """Atomically invalidate an active readiness receipt after confirmed material drift."""
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


__all__ = ["invalidate_readiness_for_drift"]
