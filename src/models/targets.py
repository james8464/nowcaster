from __future__ import annotations

import math


def encode_revenue_growth(actual_revenue: float, revenue_year_ago: float) -> float:
    if actual_revenue <= 0 or revenue_year_ago <= 0:
        raise ValueError("Revenue values must be positive")
    return math.log(actual_revenue / revenue_year_ago)


def decode_revenue_growth(growth: float, revenue_year_ago: float) -> float:
    if revenue_year_ago <= 0:
        raise ValueError("Year-ago revenue must be positive")
    bounded_growth = max(float(growth), -700.0)
    return revenue_year_ago * math.exp(bounded_growth)
