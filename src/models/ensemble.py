from __future__ import annotations

import math


def inverse_error_weights(errors: dict[str, float]) -> dict[str, float]:
    if not errors:
        raise ValueError("At least one prior-fold error is required")
    if any(not math.isfinite(error) or error <= 0 for error in errors.values()):
        raise ValueError("Prior-fold errors must be finite and positive")
    inverse = {name: 1.0 / error for name, error in errors.items()}
    total = sum(inverse.values())
    return {name: value / total for name, value in inverse.items()}
