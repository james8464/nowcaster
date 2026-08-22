from __future__ import annotations

import pytest

from src.models.targets import decode_revenue_growth, encode_revenue_growth


def test_log_growth_round_trip_is_positive():
    growth = encode_revenue_growth(125.0, 100.0)

    assert decode_revenue_growth(growth, 100.0) == pytest.approx(125.0)
    assert decode_revenue_growth(-100.0, 100.0) > 0


@pytest.mark.parametrize("actual,year_ago", [(0, 100), (100, 0), (-1, 100), (100, -1)])
def test_log_growth_rejects_nonpositive_revenue(actual, year_ago):
    with pytest.raises(ValueError, match="positive"):
        encode_revenue_growth(actual, year_ago)
