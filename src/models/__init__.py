from typing import TYPE_CHECKING, Any

from src.models.base import ModelSpec
from src.models.drift import AdaptiveMeanDrift, DriftMetric, DriftPolicy, DriftReport, assess_drift
from src.models.trade_outcomes import BarrierPolicy, TradeOutcome, label_trade_outcomes

if TYPE_CHECKING:
    from src.models.validation import expanding_window_forecasts


def __getattr__(name: str) -> Any:
    if name == "expanding_window_forecasts":
        from src.models.validation import expanding_window_forecasts

        globals()[name] = expanding_window_forecasts
        return expanding_window_forecasts
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AdaptiveMeanDrift",
    "BarrierPolicy",
    "DriftMetric",
    "DriftPolicy",
    "DriftReport",
    "ModelSpec",
    "TradeOutcome",
    "assess_drift",
    "expanding_window_forecasts",
    "label_trade_outcomes",
]
