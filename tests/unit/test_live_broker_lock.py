from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src.trading.alpaca import AlpacaCredentials
from src.trading.live import LiveBrokerFactory, LiveLockContext, LiveLockedError, LivePilotPolicy
from src.trading.readiness import ReadinessGate, ReadinessReceipt

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _receipt():
    gate = ReadinessGate(name="all", passed=True, detail="passed")
    return ReadinessReceipt(
        receipt_id="receipt",
        cohort_hash="c" * 64,
        evidence_hash="e" * 64,
        policy_hash="p" * 64,
        gates=(gate,),
        issued_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=1),
    )


def _context(**updates):
    values = dict(
        environment="live",
        credential_environment="live",
        account_suffix="1234",
        expected_account_suffix="1234",
        cohort_hash="c" * 64,
        readiness_receipt=_receipt(),
        engine_identity_verified=True,
        signature_posture="production",
        arm_expires_at=NOW + timedelta(minutes=10),
        reconciliation_mismatches=0,
        health_breakers=0,
        now=NOW,
    )
    values.update(updates)
    return LiveLockContext(**values)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"environment": "paper"}, "live_environment_required"),
        ({"credential_environment": "paper"}, "live_credentials_required"),
        ({"account_suffix": "9999"}, "account_mismatch"),
        ({"cohort_hash": "x" * 64}, "readiness_receipt_invalid"),
        ({"readiness_receipt": None}, "readiness_receipt_required"),
        ({"engine_identity_verified": False}, "engine_identity_required"),
        ({"signature_posture": "adhoc"}, "production_signature_required"),
        ({"arm_expires_at": NOW}, "manual_arm_required"),
        ({"reconciliation_mismatches": 1}, "reconciliation_unresolved"),
        ({"health_breakers": 1}, "health_breaker_active"),
    ],
)
def test_live_factory_rejects_each_missing_lock(mutation, reason) -> None:
    with pytest.raises(LiveLockedError, match=reason):
        LiveBrokerFactory().create(
            _context(**mutation),
            AlpacaCredentials(key_id="live-key", secret_key="live-secret"),
            client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
        )


def test_live_factory_constructs_fixed_live_adapter_only_when_every_lock_passes() -> None:
    client = LiveBrokerFactory().create(
        _context(),
        AlpacaCredentials(key_id="live-key", secret_key="live-secret"),
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
    )
    assert client.environment.value == "live"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_position_dollars", "100.01"),
        ("max_position_fraction", "0.0011"),
        ("max_gross_dollars", "500.01"),
        ("max_gross_fraction", "0.0051"),
        ("max_daily_loss_dollars", "25.01"),
        ("max_daily_loss_fraction", "0.00051"),
        ("arm_minutes", 31),
    ],
)
def test_live_policy_can_tighten_but_never_raise_hard_caps(field, value) -> None:
    with pytest.raises(ValueError):
        LivePilotPolicy(**{field: value})
