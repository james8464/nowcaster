"""Frozen registration contract for independent, public-feed paper accounts."""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, model_validator

from src.live_monitor.types import LiveMonitorModel
from src.strategies.types import canonical_hash


class StudyCandidate(LiveMonitorModel):
    candidate_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    strategy_id: Literal["bollinger_keltner_squeeze", "macd_histogram_trend", "volatility_scaled_trend"]
    strategy_definition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    stop_atr: Literal[1, 2]
    target_atr: Literal[1.5, 3.0]
    maximum_bars: Literal[6, 12]
    screen_passed: bool

    @model_validator(mode="after")
    def coherent_target(self):
        if self.target_atr != self.stop_atr * 1.5:
            raise ValueError("target must be 1.5 times stop distance")
        return self


class StudyManifest(LiveMonitorModel):
    schema_version: Literal[1] = 1
    study_number: int = Field(ge=1)
    registered_at: datetime
    starts_at: datetime
    ends_at: datetime
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    discovery_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    predecessor_study_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidates: tuple[StudyCandidate, ...] = Field(min_length=1, max_length=2)
    initial_cash: Literal[10000] = 10000
    maximum_exposure_fraction: Literal[0.25] = 0.25
    initial_risk_fraction: Literal[0.0025] = 0.0025
    slippage_bps: Literal[5] = 5
    fee_bps: Literal[10] = 10
    additional_stress_bps: Literal[34] = 34
    minimum_target_bps: Literal[68] = 68
    entry_latency_ms: Literal[250] = 250
    maximum_quote_age_seconds: Literal[2] = 2
    maximum_entry_spread_bps: Literal[10] = 10
    pending_lifetime_seconds: Literal[30] = 30
    maximum_gap_seconds: Literal[30] = 30
    minimum_closed_trades: Literal[100] = 100
    minimum_minute_coverage: Literal[0.99] = 0.99
    maximum_drawdown_fraction: Literal[0.1] = 0.1
    bootstrap_block_days: Literal[7] = 7
    bootstrap_minimum_days: Literal[28] = 28
    bootstrap_resamples: Literal[10000] = 10000
    bootstrap_minimum_tail_samples: Literal[20] = 20
    bootstrap_seed: Literal[20260908] = 20260908
    bootstrap_family_alpha: Literal[0.05] = 0.05
    execution_model: Literal["observed_ask_bid_displayed_size"] = "observed_ask_bid_displayed_size"
    level_anchor: Literal["simulated_entry_with_predecision_ATR_distances"] = (
        "simulated_entry_with_predecision_ATR_distances"
    )
    expiry_anchor: Literal["signal_bar_end"] = "signal_bar_end"
    stress_method: Literal["extra_34bps_of_entry_notional_per_closed_trade"] = (
        "extra_34bps_of_entry_notional_per_closed_trade"
    )
    daily_return_method: Literal["complete_UTC_days_marked_to_liquidation_value"] = (
        "complete_UTC_days_marked_to_liquidation_value"
    )
    promotion: Literal["never_automatic_real_money"] = "never_automatic_real_money"

    @model_validator(mode="after")
    def validate_registration(self):
        if self.registered_at > self.starts_at or self.ends_at != self.starts_at + timedelta(days=90):
            raise ValueError("registration must precede a fixed 90-day study")
        if len({c.symbol for c in self.candidates}) != len(self.candidates):
            raise ValueError("at most one independent candidate per symbol")
        if len({c.candidate_id for c in self.candidates}) != len(self.candidates):
            raise ValueError("duplicate candidate")
        if self.study_number > 1 and self.predecessor_study_id is None:
            raise ValueError("later campaigns require a retained predecessor")
        return self

    @property
    def study_id(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))
