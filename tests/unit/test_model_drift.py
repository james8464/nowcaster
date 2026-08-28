from __future__ import annotations

import math

import pytest

from src.models.drift import AdaptiveMeanDrift, DriftPolicy, StreamingDriftMonitor, assess_drift


def test_adaptive_monitor_ignores_stationary_values() -> None:
    monitor = AdaptiveMeanDrift(minimum_window=20, confidence=0.99, confirmation_updates=2)

    reports = [monitor.update(0.0) for _ in range(100)]

    assert not any(item.confirmed for item in reports)
    assert reports[-1].status == "stable"


def test_adaptive_monitor_detects_and_latches_a_persistent_mean_shift() -> None:
    monitor = AdaptiveMeanDrift(minimum_window=20, confidence=0.99, confirmation_updates=2)

    reports = [monitor.update(value) for value in ([0.0] * 40 + [2.0] * 40)]

    assert any(item.confirmed for item in reports[-20:])
    assert reports[-1].confirmed
    assert reports[-1].status == "confirmed"


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_adaptive_monitor_rejects_nonfinite_input(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        AdaptiveMeanDrift(minimum_window=5).update(value)


def test_static_drift_report_is_hashed_and_missing_metrics_fail_closed() -> None:
    policy = DriftPolicy(
        required_metrics=("feature_distribution", "prediction_distribution"),
        minimum_window=20,
        minimum_ready_metrics=2,
        confidence=0.99,
    )
    reference = {
        "feature_distribution": [0.0] * 20,
        "prediction_distribution": [0.5] * 20,
    }
    recent = {
        "feature_distribution": [2.0] * 20,
        "prediction_distribution": [0.9] * 20,
    }

    report = assess_drift(reference, recent, policy=policy)
    replay = assess_drift(reference, recent, policy=policy)
    missing = assess_drift(reference, {"feature_distribution": [2.0] * 20}, policy=policy)

    assert report.status == "confirmed"
    assert report.evidence_hash == replay.evidence_hash
    assert report.policy_hash == policy.policy_hash
    assert missing.status == "unavailable"
    assert missing.missing_metrics == ("prediction_distribution",)


def test_streaming_monitor_requires_multiple_ready_metrics_before_reporting_stable() -> None:
    policy = DriftPolicy(
        required_metrics=("feature_distribution", "prediction_distribution", "net_edge"),
        minimum_window=5,
        minimum_ready_metrics=2,
        confirmation_updates=2,
    )
    monitor = StreamingDriftMonitor(policy)

    early = monitor.update({"feature_distribution": 0.0, "prediction_distribution": 0.5})
    for _ in range(11):
        ready = monitor.update({"feature_distribution": 0.0, "prediction_distribution": 0.5})

    assert early.status == "unavailable"
    assert ready.status == "stable"
    assert ready.missing_metrics == ("net_edge",)
