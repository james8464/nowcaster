from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.database.engine import Database
from src.trading.arming import ArmingService, ArmRequest
from src.trading.readiness import ReadinessGate, ReadinessReceipt

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _receipt(expires=NOW + timedelta(hours=1)):
    return ReadinessReceipt(
        receipt_id="receipt",
        cohort_hash="c" * 64,
        evidence_hash="e" * 64,
        policy_hash="p" * 64,
        gates=(ReadinessGate(name="all", passed=True, detail="passed"),),
        issued_at=NOW - timedelta(hours=1),
        expires_at=expires,
    )


def _service(tmp_path, nonce="process-1"):
    database = Database.from_url(f"duckdb:///{tmp_path / 'arming.duckdb'}")
    database.initialize()
    return ArmingService(database, process_nonce=nonce, clock=lambda: NOW)


def test_arm_requires_exact_account_and_loss_phrase(tmp_path) -> None:
    service = _service(tmp_path)
    rejected = service.arm(
        ArmRequest(account_suffix="9999", phrase="ARM LIVE 9999 LOSS 25"),
        account_suffix="1234",
        receipt=_receipt(),
    )
    assert rejected is None


def test_arm_expires_at_earliest_of_30_minutes_or_receipt(tmp_path) -> None:
    service = _service(tmp_path)
    arm = service.arm(
        ArmRequest(account_suffix="1234", phrase="ARM LIVE 1234 LOSS 25"),
        account_suffix="1234",
        receipt=_receipt(NOW + timedelta(minutes=10)),
    )
    assert arm is not None and arm.expires_at == NOW + timedelta(minutes=10)
    assert service.current(account_suffix="1234", at=NOW + timedelta(minutes=9)) == arm
    assert service.current(account_suffix="1234", at=NOW + timedelta(minutes=10)) is None


def test_arm_does_not_survive_process_restart_and_disarm_is_immediate(tmp_path) -> None:
    first = _service(tmp_path, "process-1")
    arm = first.arm(
        ArmRequest(account_suffix="1234", phrase="ARM LIVE 1234 LOSS 25"),
        account_suffix="1234",
        receipt=_receipt(),
    )
    assert arm is not None
    restarted = ArmingService(first.database, process_nonce="process-2", clock=lambda: NOW)
    assert restarted.current(account_suffix="1234", at=NOW) is None
    first.disarm("operator")
    assert first.current(account_suffix="1234", at=NOW) is None
