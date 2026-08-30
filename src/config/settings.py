from __future__ import annotations

import math
import os
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from src.contextual.types import AssetProfileName, ContextLevel, StrategyDirection
from src.strategies.types import StrategyFamily, StrategySpec
from src.trading.risk import RiskPolicy

ImmutableFamilyWeightCaps = Annotated[
    Mapping[StrategyFamily, float],
    PlainSerializer(
        lambda value: {family.value: cap for family, cap in value.items()},
        return_type=dict[str, float],
        when_used="always",
    ),
]

ImmutableProfilePolicies = Annotated[
    Mapping[AssetProfileName, "ProfilePolicy"],
    PlainSerializer(
        lambda value: {profile.value: policy for profile, policy in value.items()},
        return_type=dict[str, "ProfilePolicy"],
        when_used="always",
    ),
]

ImmutableHierarchyStrengths = Annotated[
    Mapping[ContextLevel, float],
    PlainSerializer(
        lambda value: {level.value: strength for level, strength in value.items()},
        return_type=dict[str, float],
        when_used="always",
    ),
]


class CompanyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    cik: str
    name: str
    sector: str = "Unknown"
    sector_etf: str = "SPY"
    fiscal_year_end_month: int = Field(default=12, ge=1, le=12)
    wikipedia_article: str | None = None
    search_terms: tuple[str, ...] = ()
    enabled: bool = True

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("cik")
    @classmethod
    def normalize_cik(cls, value: str) -> str:
        digits = "".join(character for character in str(value) if character.isdigit())
        if not digits:
            raise ValueError("CIK must contain digits")
        return digits.zfill(10)


class UniverseConfig(BaseModel):
    companies: tuple[CompanyConfig, ...]

    @model_validator(mode="after")
    def unique_tickers(self) -> UniverseConfig:
        tickers = [company.ticker for company in self.companies]
        duplicates = sorted({ticker for ticker in tickers if tickers.count(ticker) > 1})
        if duplicates:
            raise ValueError(f"Duplicate ticker: {', '.join(duplicates)}")
        return self


class BacktestConfig(BaseModel):
    event_windows: tuple[tuple[int, int], ...] = ((-1, 1), (0, 1), (0, 3), (0, 5))
    transaction_cost_bps: float = Field(default=10, ge=0)
    slippage_bps: float = Field(default=5, ge=0)


class ModelConfig(BaseModel):
    forecast_horizons: tuple[int, ...] = (30, 14, 7, 1)
    target: str = "revenue_yoy_log_growth"
    minimum_training_quarters: int = Field(default=8, ge=4)
    random_seed: int = 42
    models: dict[str, dict[str, Any]] = {}
    backtest: BacktestConfig = BacktestConfig()

    @field_validator("forecast_horizons")
    @classmethod
    def positive_horizons(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or any(horizon <= 0 for horizon in value):
            raise ValueError("forecast horizons must be positive")
        return value


class FeatureConfig(BaseModel):
    availability_lags: dict[str, int] = {"wikipedia": 1, "prices": 1}
    attention_windows: tuple[int, ...] = (28, 91)
    minimum_history_observations: int = 20
    macro_series: dict[str, str] = {}


class InstrumentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str
    asset_class: Literal["equity", "crypto"]
    currency: str = "USD"
    venue: str = "composite"
    provider: str = "composite"
    feed: str = "daily"
    product: Literal["composite", "equity", "spot", "margin", "perpetual"] = "composite"
    profile: AssetProfileName | None = None
    shortable: bool = False
    short_mechanism: Literal["none", "borrow", "derivative"] = "none"
    funding_applicable: bool = False
    borrow_applicable: bool = False
    trading_calendar: str = "unknown"
    historical_proxy_symbol: str | None = None
    historical_proxy_provider: str | None = None
    horizons: tuple[int, ...] = (5,)
    primary_horizon: int = 5
    minimum_training_days: int = Field(default=365, ge=120)
    fee_bps: float = Field(default=10, ge=0)
    slippage_bps: float = Field(default=5, ge=0)
    enabled: bool = True

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def primary_is_configured(self) -> InstrumentConfig:
        if not self.horizons or any(horizon <= 0 for horizon in self.horizons):
            raise ValueError("instrument horizons must be positive")
        if self.primary_horizon not in self.horizons:
            raise ValueError("primary_horizon must be included in horizons")
        if any(not value.strip() for value in (self.provider, self.feed, self.venue, self.trading_calendar)):
            raise ValueError("instrument source and calendar identities cannot be blank")
        if self.shortable != (self.short_mechanism != "none"):
            raise ValueError("shortable instruments require an explicit short mechanism")
        if self.borrow_applicable != (self.short_mechanism == "borrow"):
            raise ValueError("borrow applicability must match the short mechanism")
        if self.funding_applicable and self.short_mechanism != "derivative":
            raise ValueError("funding is applicable only to derivative instruments")
        if self.product == "spot" and self.funding_applicable:
            raise ValueError("spot instruments cannot have perpetual funding")
        if (self.historical_proxy_symbol is None) != (self.historical_proxy_provider is None):
            raise ValueError("historical proxy symbol and provider must be declared together")
        return self


class InstrumentsConfig(BaseModel):
    instruments: tuple[InstrumentConfig, ...] = ()

    @model_validator(mode="after")
    def unique_symbols(self) -> InstrumentsConfig:
        symbols = [instrument.symbol for instrument in self.instruments]
        if len(symbols) != len(set(symbols)):
            raise ValueError("instrument symbols must be unique")
        return self


class StrategiesConfig(BaseModel):
    """Configuration only; signal callables are an explicit code-level registry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_weight_cap: float = Field(default=0.25, gt=0, le=1)
    family_weight_caps: ImmutableFamilyWeightCaps = Field(default_factory=lambda: MappingProxyType({}))
    strategies: tuple[StrategySpec, ...] = ()

    @field_validator("family_weight_caps")
    @classmethod
    def valid_family_caps(cls, value: Mapping[StrategyFamily, float]) -> Mapping[StrategyFamily, float]:
        if any(cap <= 0 or cap > 1 for cap in value.values()):
            raise ValueError("family weight caps must be in (0, 1]")
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def consistent_caps_and_ids(self) -> StrategiesConfig:
        if any(self.strategy_weight_cap > cap for cap in self.family_weight_caps.values()):
            raise ValueError("strategy weight cap must not exceed a family weight cap")
        missing_families = {spec.family for spec in self.strategies if spec.enabled} - set(self.family_weight_caps)
        if missing_families:
            values = ", ".join(sorted(family.value for family in missing_families))
            raise ValueError(f"enabled strategy family requires a weight cap: {values}")
        strategy_ids = [spec.strategy_id for spec in self.strategies]
        if len(strategy_ids) != len(set(strategy_ids)):
            raise ValueError("strategy IDs must be unique")
        return self

    @property
    def enabled(self) -> tuple[StrategySpec, ...]:
        return tuple(spec for spec in self.strategies if spec.enabled)


class ProfilePolicy(BaseModel):
    """Structural and point-in-time execution limits for one asset profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_classes: tuple[Literal["equity", "crypto"], ...]
    products: tuple[Literal["equity", "spot", "margin", "perpetual"], ...]
    trading_calendars: tuple[str, ...]
    allowed_directions: tuple[StrategyDirection, ...]
    allowed_families: tuple[StrategyFamily, ...]
    session_strategy_ids: tuple[str, ...] = ()
    minimum_cross_sectional_peers: int = Field(default=2, ge=2)
    minimum_history_bars: int = Field(gt=0)
    minimum_coverage: float = Field(ge=0, le=1)
    maximum_data_age_seconds: int = Field(gt=0)
    minimum_median_notional_volume: float = Field(gt=0)
    maximum_spread_bps: float = Field(gt=0)
    minimum_depth_notional: float = Field(gt=0)
    maximum_price_impact_bps: float = Field(gt=0)
    maximum_participation_rate: float = Field(gt=0, le=1)
    minimum_realized_volatility: float = Field(ge=0)
    maximum_realized_volatility: float = Field(gt=0)
    require_observed_spread: bool = True
    require_observed_depth: bool = True
    require_observed_impact: bool = True

    @field_validator(
        "asset_classes",
        "products",
        "trading_calendars",
        "allowed_directions",
        "allowed_families",
    )
    @classmethod
    def nonempty_unique_values(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if not value:
            raise ValueError("profile policy collections cannot be empty")
        if len(value) != len(set(value)):
            raise ValueError("profile policy collections must contain unique values")
        if any(isinstance(item, str) and not item.strip() for item in value):
            raise ValueError("profile policy values cannot be blank")
        return value

    @field_validator("session_strategy_ids")
    @classmethod
    def unique_session_strategies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("session strategy IDs must be nonblank and unique")
        return normalized

    @field_validator(
        "minimum_coverage",
        "minimum_median_notional_volume",
        "maximum_spread_bps",
        "minimum_depth_notional",
        "maximum_price_impact_bps",
        "maximum_participation_rate",
        "minimum_realized_volatility",
        "maximum_realized_volatility",
    )
    @classmethod
    def finite_thresholds(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("profile policy thresholds must be finite")
        return value

    @model_validator(mode="after")
    def coherent_volatility_range(self) -> ProfilePolicy:
        if self.minimum_realized_volatility >= self.maximum_realized_volatility:
            raise ValueError("minimum realized volatility must be below maximum realized volatility")
        return self


class AllocationPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_effective_strategies: int = Field(default=4, ge=2)
    maximum_strategy_weight: float = Field(default=0.25, gt=0, le=1)
    minimum_covariance_overlap: int = Field(default=100, ge=20)
    risk_penalty: float = Field(default=4.0, gt=0)
    turnover_penalty: float = Field(default=0.5, ge=0)
    prior_penalty: float = Field(default=0.5, ge=0)
    family_weight_caps: ImmutableFamilyWeightCaps

    @field_validator(
        "maximum_strategy_weight",
        "risk_penalty",
        "turnover_penalty",
        "prior_penalty",
    )
    @classmethod
    def finite_allocation_values(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("allocation values must be finite")
        return value

    @field_validator("family_weight_caps")
    @classmethod
    def immutable_family_caps(cls, value: Mapping[StrategyFamily, float]) -> Mapping[StrategyFamily, float]:
        if not value or any(not math.isfinite(cap) or cap <= 0 or cap > 1 for cap in value.values()):
            raise ValueError("family weight caps must be finite and in (0, 1]")
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def enforce_effective_breadth(self) -> AllocationPolicyConfig:
        reciprocal_breadth = 1.0 / self.minimum_effective_strategies
        if self.maximum_strategy_weight > reciprocal_breadth + 1e-12:
            raise ValueError("maximum strategy weight exceeds reciprocal effective breadth")
        if any(self.maximum_strategy_weight > cap for cap in self.family_weight_caps.values()):
            raise ValueError("maximum strategy weight must not exceed family caps")
        return self


class PortfolioSelectionPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_candidates: int = Field(default=20, ge=1, le=200)
    maximum_opportunities: int = Field(default=5, ge=1, le=20)
    maximum_gross_exposure: float = Field(default=0.50, gt=0, le=1)
    maximum_net_exposure: float = Field(default=0.30, gt=0, le=1)
    maximum_asset_weight: float = Field(default=0.10, gt=0, le=1)
    maximum_asset_class_weight: float = Field(default=0.30, gt=0, le=1)
    maximum_sector_weight: float = Field(default=0.20, gt=0, le=1)
    maximum_correlation: float = Field(default=0.75, ge=0, lt=1)
    minimum_research_weight: float = Field(default=0.0025, gt=0, le=1)
    kelly_fraction: float = Field(default=0.10, gt=0, le=0.25)
    volatility_target: float = Field(default=0.10, gt=0, le=1)

    @field_validator(
        "maximum_gross_exposure",
        "maximum_net_exposure",
        "maximum_asset_weight",
        "maximum_asset_class_weight",
        "maximum_sector_weight",
        "maximum_correlation",
        "minimum_research_weight",
        "kelly_fraction",
        "volatility_target",
    )
    @classmethod
    def finite_portfolio_values(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("portfolio limits must be finite")
        return value

    @model_validator(mode="after")
    def coherent_portfolio_limits(self) -> PortfolioSelectionPolicyConfig:
        if self.maximum_net_exposure > self.maximum_gross_exposure:
            raise ValueError("maximum net exposure cannot exceed maximum gross exposure")
        if self.maximum_asset_weight > self.maximum_gross_exposure:
            raise ValueError("maximum asset weight cannot exceed maximum gross exposure")
        if self.maximum_opportunities > self.maximum_candidates:
            raise ValueError("maximum opportunities cannot exceed maximum candidates")
        return self


class AssetSelectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_candidate_universe: int = Field(default=200, ge=1, le=1_000)
    profiles: ImmutableProfilePolicies
    hierarchy_prior_strengths: ImmutableHierarchyStrengths
    allocation: AllocationPolicyConfig
    portfolio: PortfolioSelectionPolicyConfig

    @field_validator("profiles")
    @classmethod
    def complete_immutable_profiles(
        cls, value: Mapping[AssetProfileName, ProfilePolicy]
    ) -> Mapping[AssetProfileName, ProfilePolicy]:
        missing = set(AssetProfileName) - set(value)
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"missing asset profile policies: {names}")
        return MappingProxyType(dict(value))

    @field_validator("hierarchy_prior_strengths")
    @classmethod
    def complete_immutable_hierarchy(
        cls, value: Mapping[ContextLevel, float]
    ) -> Mapping[ContextLevel, float]:
        missing = set(ContextLevel) - set(value)
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"missing hierarchy prior strengths: {names}")
        if any(not math.isfinite(item) or item <= 0 for item in value.values()):
            raise ValueError("hierarchy prior strengths must be finite and positive")
        return MappingProxyType(dict(value))


class TradingConfig(BaseModel):
    """Fail-closed broker session configuration; live mode is introduced only by the gated pilot plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    paper_enabled: bool = True
    live_enabled: bool = False
    paper_base_url: Literal["https://paper-api.alpaca.markets"] = "https://paper-api.alpaca.markets"
    paper_stream_url: Literal["wss://paper-api.alpaca.markets/stream"] = "wss://paper-api.alpaca.markets/stream"
    reconciliation_interval_seconds: int = Field(default=30, ge=5, le=300)
    market_data_stale_after_seconds: int = Field(default=30, ge=1, le=300)
    risk: RiskPolicy = RiskPolicy()

    @model_validator(mode="after")
    def live_remains_locked(self) -> TradingConfig:
        if self.live_enabled:
            raise ValueError("live trading is unavailable until the gated live-pilot plan is complete")
        return self


class DeepResearchConfig(BaseModel):
    """Conservative local-search defaults; UI and CLI may only tighten bounded values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    default_cycle_budget: int = Field(default=100, ge=4, le=100_000)
    maximum_workers: int = Field(default=256, ge=1, le=256)
    reserved_processors: int = Field(default=2, ge=2, le=16)
    minimum_calibration_observations: int = Field(default=100, ge=100)
    minimum_effective_calibration_observations: int = Field(default=100, ge=100)
    minimum_isotonic_calibration_observations: int = Field(default=1_000, ge=1_000)
    maximum_brier_score: float = Field(default=0.25, ge=0, le=0.25)
    maximum_calibration_error: float = Field(default=0.10, ge=0, le=0.10)
    minimum_walk_forward_train_observations: int = Field(default=500, ge=500)
    minimum_walk_forward_validation_observations: int = Field(default=100, ge=100)
    minimum_promotion_observations: int = Field(default=1_000, ge=1_000)
    minimum_effective_promotion_observations: int = Field(default=300, ge=300)
    minimum_promotion_trades: int = Field(default=300, ge=300)
    minimum_rolling_holdouts: int = Field(default=3, ge=3)
    minimum_promotion_bootstrap_probability: float = Field(default=0.99, ge=0.99, le=1)
    minimum_promotion_dsr_probability: float = Field(default=0.99, ge=0.99, le=1)
    maximum_promotion_pbo_probability: float = Field(default=0.10, ge=0, le=0.10)
    crypto_fee_bps: float = Field(default=10.0, ge=0)
    equity_fee_bps: float = Field(default=0.0, ge=0)
    half_spread_bps: float = Field(default=2.0, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0)

    @model_validator(mode="after")
    def evidence_thresholds_are_coherent(self) -> DeepResearchConfig:
        if self.minimum_effective_calibration_observations > self.minimum_calibration_observations:
            raise ValueError("effective calibration minimum cannot exceed the raw calibration minimum")
        if self.minimum_isotonic_calibration_observations < self.minimum_calibration_observations:
            raise ValueError("isotonic calibration must require at least the general calibration minimum")
        if self.minimum_effective_promotion_observations > self.minimum_promotion_observations:
            raise ValueError("effective promotion observations cannot exceed raw promotion observations")
        return self


class Settings(BaseModel):
    project_root: Path
    mode: Literal["live", "demo", "test"] = "live"
    database_url: str
    log_level: str = "INFO"
    sec_user_agent: str | None = None
    fred_api_key: str | None = None
    alpha_vantage_api_key: str | None = None
    universe: UniverseConfig
    model: ModelConfig
    features: FeatureConfig
    instruments: InstrumentsConfig = InstrumentsConfig()
    strategies: StrategiesConfig = StrategiesConfig()
    asset_selection: AssetSelectionConfig | None = None
    trading: TradingConfig = TradingConfig()
    deep_research: DeepResearchConfig = DeepResearchConfig()

    @model_validator(mode="after")
    def contextual_profiles_match_instruments(self) -> Settings:
        if self.asset_selection is None:
            return self
        for instrument in self.instruments.instruments:
            if instrument.profile is None:
                continue
            policy = self.asset_selection.profiles[instrument.profile]
            if instrument.asset_class not in policy.asset_classes:
                raise ValueError(f"{instrument.symbol} profile does not support asset class")
            if instrument.product not in policy.products:
                raise ValueError(f"{instrument.symbol} profile does not support product")
            if instrument.trading_calendar not in policy.trading_calendars:
                raise ValueError(f"{instrument.symbol} profile does not support trading calendar")
        return self

    @classmethod
    def load(cls, project_root: Path | None = None, *, mode: str | None = None) -> Settings:
        root = (project_root or Path.cwd()).resolve()
        load_dotenv(root / ".env", override=False)

        def read_yaml(name: str, *, required: bool = True) -> dict[str, Any]:
            path = root / "config" / name
            if not path.exists() and not required:
                return {}
            with path.open(encoding="utf-8") as handle:
                return yaml.safe_load(handle) or {}

        selected_mode = mode or os.getenv("NOWCASTER_MODE", "live")
        asset_selection_payload = read_yaml("asset_selection.yaml", required=False)
        default_database = root / "data" / "nowcaster.duckdb"
        return cls(
            project_root=root,
            mode=selected_mode,
            database_url=os.getenv("NOWCASTER_DATABASE_URL", f"duckdb:///{default_database}"),
            log_level=os.getenv("NOWCASTER_LOG_LEVEL", "INFO"),
            sec_user_agent=os.getenv("SEC_USER_AGENT") or None,
            fred_api_key=os.getenv("FRED_API_KEY") or None,
            alpha_vantage_api_key=os.getenv("ALPHA_VANTAGE_API_KEY") or None,
            universe=UniverseConfig.model_validate(read_yaml("universe.yaml")),
            model=ModelConfig.model_validate(read_yaml("model.yaml")),
            features=FeatureConfig.model_validate(read_yaml("features.yaml")),
            instruments=InstrumentsConfig.model_validate(read_yaml("instruments.yaml", required=False)),
            strategies=StrategiesConfig.model_validate(read_yaml("strategies.yaml", required=False)),
            asset_selection=(
                AssetSelectionConfig.model_validate(asset_selection_payload) if asset_selection_payload else None
            ),
            trading=TradingConfig.model_validate(read_yaml("trading.yaml", required=False)),
            deep_research=DeepResearchConfig.model_validate(read_yaml("deep_research.yaml", required=False)),
        )

    def config_hash_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json", exclude={"project_root", "sec_user_agent", "fred_api_key", "alpha_vantage_api_key"}
        )
