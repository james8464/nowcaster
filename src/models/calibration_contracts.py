"""Versioned outcome contracts; structural compatibility is not proof of event provenance.

Keep this module independent of fitting libraries: the live monitor imports it.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrategyReturnCalibrationContract(BaseModel):
    """Current producer: chronological net strategy returns, research-only."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal["strategy_return_v1"] = "strategy_return_v1"
    outcome_source: Literal["strategy_equity_curve"] = "strategy_equity_curve"


class TargetEventCalibrationContract(BaseModel):
    """Reserved event contract; no production event-calibration producer exists yet.

    The event-definition commitment covers target, protective stop, decision-relative
    horizon, costs, and same-bar ordering. The outcome commitment covers the resolved
    target/stop event rows used for calibration. These hashes bind declared provenance;
    validating their shape does not reconstruct or verify the underlying event paths.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal["target_stop_event_v1"]
    outcome_source: Literal["resolved_target_stop_events"]
    event_definition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_rows_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
