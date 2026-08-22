from __future__ import annotations

import pytest

from src.models.ensemble import inverse_error_weights


def test_inverse_error_weights_favor_lower_prior_fold_error():
    weights = inverse_error_weights({"ridge": 2.0, "elastic_net": 1.0})

    assert weights == pytest.approx({"ridge": 1 / 3, "elastic_net": 2 / 3})


def test_inverse_error_weights_reject_nonfinite_or_nonpositive_errors():
    with pytest.raises(ValueError, match="finite and positive"):
        inverse_error_weights({"ridge": 0.0})
