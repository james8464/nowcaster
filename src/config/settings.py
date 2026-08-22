from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
        return self


class InstrumentsConfig(BaseModel):
    instruments: tuple[InstrumentConfig, ...] = ()

    @model_validator(mode="after")
    def unique_symbols(self) -> InstrumentsConfig:
        symbols = [instrument.symbol for instrument in self.instruments]
        if len(symbols) != len(set(symbols)):
            raise ValueError("instrument symbols must be unique")
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
        )

    def config_hash_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json", exclude={"project_root", "sec_user_agent", "fred_api_key", "alpha_vantage_api_key"}
        )
