from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from src.models.calibration import (
    RollingProbabilityCalibrator,
    calibration_report,
    fit_out_of_fold_calibration,
    selective_threshold,
)


def test_calibrator_refuses_small_samples_and_preserves_bounded_probabilities():
    calibrator = RollingProbabilityCalibrator(minimum_observations=100).fit(
        np.linspace(0.1, 0.9, 20),
        np.array([0, 1] * 10),
    )

    assert calibrator.status == "insufficient"
    assert np.allclose(calibrator.predict(np.array([-1.0, 0.5, 2.0])), [0.0, 0.5, 1.0])


def test_calibrator_fits_only_when_sample_and_both_classes_are_available():
    probabilities = np.linspace(0.01, 0.99, 120)
    outcomes = (probabilities > 0.55).astype(int)

    calibrator = RollingProbabilityCalibrator(minimum_observations=100).fit(probabilities, outcomes)

    calibrated = calibrator.predict(np.array([0.2, 0.8]))
    assert calibrator.status == "calibrated"
    assert calibrated[0] <= calibrated[1]
    assert np.all((calibrated >= 0) & (calibrated <= 1))


def test_calibration_report_uses_hand_checked_brier_and_effective_sample() -> None:
    report = calibration_report(np.array([0.25, 0.75]), np.array([0, 1]))

    assert report.brier_score == pytest.approx(0.0625)
    assert report.sample_size == 2
    assert 0 < report.effective_sample_size <= 2
    assert 0 <= report.confidence_low <= report.base_rate <= report.confidence_high <= 1


def test_calibration_report_clips_log_loss_and_rejects_invalid_labels() -> None:
    report = calibration_report(np.array([0.0, 1.0]), np.array([0, 1]), slice_identity="AAPL:5m")

    assert np.isfinite(report.log_loss)
    assert report.expected_calibration_error == pytest.approx(0.0)
    assert report.slice_identity == "AAPL:5m"
    with pytest.raises(ValueError, match="binary"):
        calibration_report(np.array([0.2, 0.8]), np.array([0, 2]))


def test_out_of_fold_calibration_selects_sample_appropriate_method() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    probabilities = np.linspace(0.05, 0.95, 120)
    outcomes = (probabilities + 0.08 * np.sin(np.arange(120)) > 0.55).astype(int)
    timestamps = tuple(started + timedelta(minutes=index) for index in range(120))

    sigmoid = fit_out_of_fold_calibration(probabilities, outcomes, timestamps)
    isotonic = fit_out_of_fold_calibration(
        np.tile(probabilities, 9),
        np.tile(outcomes, 9),
        tuple(started + timedelta(minutes=index) for index in range(1_080)),
    )

    assert sigmoid.status == "calibrated"
    assert sigmoid.method == "oof_sigmoid_v2"
    assert isotonic.method == "oof_isotonic_v2"
    assert np.all(
        (sigmoid.predict(np.array([-1.0, 0.5, 2.0])) >= 0) & (sigmoid.predict(np.array([-1.0, 0.5, 2.0])) <= 1)
    )


def test_out_of_fold_calibration_requires_ordered_explicit_utc_timestamps() -> None:
    with pytest.raises(ValueError, match="explicit UTC"):
        fit_out_of_fold_calibration(
            np.linspace(0.1, 0.9, 100),
            np.array([0, 1] * 50),
            tuple(datetime(2026, 1, 1) + timedelta(minutes=index) for index in range(100)),
        )


def test_selective_threshold_requires_a_positive_lower_net_edge() -> None:
    probabilities = np.array([0.51] * 60 + [0.82] * 60)
    net_returns = np.array([-0.002, 0.001] * 30 + [0.006, 0.004] * 30)

    selected = selective_threshold(
        probabilities,
        net_returns,
        minimum_coverage=0.25,
        minimum_observations=30,
    )
    rejected = selective_threshold(
        probabilities,
        np.full(120, -0.001),
        minimum_coverage=0.25,
        minimum_observations=30,
    )

    assert selected.status == "selected"
    assert selected.threshold > 0.5
    assert selected.lower_net_edge > 0
    assert rejected.status == "abstain"
