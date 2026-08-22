from __future__ import annotations

import numpy as np

from src.models.calibration import RollingProbabilityCalibrator


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
