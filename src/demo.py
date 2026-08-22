from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import pandas as pd

from src.backtest.event_study import run_event_study
from src.config.settings import Settings
from src.consensus.proxy import historical_expectation_proxy
from src.consensus.variant import build_variant_signals
from src.database.engine import Database
from src.features.builder import FeatureBuilder, feature_rows
from src.ingestion.earnings import filing_event_proxy_rows, load_earnings_calendar
from src.ingestion.http import CachedHttpClient
from src.ingestion.prices import YahooChartPriceProvider, parse_yahoo_chart, price_rows
from src.ingestion.sec import SecClient, financial_to_row, normalize_company_facts
from src.ingestion.wikipedia import WikipediaProvider, parse_pageviews, wikipedia_rows
from src.models.base import ModelSpec
from src.models.matrix import build_model_matrix
from src.models.validation import expanding_window_forecasts
from src.pipeline import Pipeline, PipelineSummary
from src.utils.provenance import canonical_hash, git_commit

DEMO_STAGES = (
    "ingest_fundamentals",
    "ingest_prices",
    "ingest_alternative",
    "build_features",
    "train",
    "variant",
    "backtest",
)
DEMO_TICKERS = ("SBUX", "MCD", "COST")


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value is pd.NaT:
        return None
    return value


def _records(frame: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    return [{column: _clean(row.get(column)) for column in columns} for row in frame.to_dict(orient="records")]


class DemoStages:
    """Build research outputs from bundled, real public-data snapshots without network calls."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = Database.from_url(settings.database_url)
        self.database.initialize()
        self.demo_root = settings.project_root / "data" / "demo"
        selected_tickers = set(DEMO_TICKERS) if settings.mode == "demo" else None
        self.companies = {
            company.ticker: company
            for company in settings.universe.companies
            if company.enabled and (selected_tickers is None or company.ticker in selected_tickers)
        }
        if settings.mode == "demo" and set(self.companies) != set(DEMO_TICKERS):
            raise ValueError("Demo configuration must contain SBUX, MCD, and COST")

    def _empty(self, table: str) -> bool:
        return self.database.scalar(f"select count(*) from {table}") == 0

    def ingest_fundamentals(self) -> dict[str, int]:
        created_at = datetime.now(UTC)
        company_count = 0
        if self._empty("companies"):
            company_rows = [
                {
                    "company_id": company.ticker,
                    "ticker": company.ticker,
                    "cik": company.cik,
                    "name": company.name,
                    "sector": company.sector,
                    "sector_etf": company.sector_etf,
                    "fiscal_year_end_month": company.fiscal_year_end_month,
                    "active": True,
                    "created_at": created_at,
                }
                for company in self.companies.values()
            ]
            company_count = self.database.insert("companies", company_rows)
        financial_count = 0
        if self._empty("financials_quarterly"):
            rows: list[dict[str, Any]] = []
            for ticker, company in self.companies.items():
                path = self.demo_root / "sec" / f"{ticker}_companyfacts.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                rows.extend(
                    financial_to_row(item, source="sec_public_snapshot")
                    for item in normalize_company_facts(payload, company)
                )
            financial_count = self.database.insert("financials_quarterly", rows)
        event_count = 0
        if self._empty("earnings_calendar"):
            event_count = self.database.insert(
                "earnings_calendar",
                load_earnings_calendar(self.demo_root / "earnings_calendar.csv"),
            )
        return {
            "companies": company_count,
            "financials_quarterly": financial_count,
            "earnings_calendar": event_count,
        }

    def ingest_prices(self) -> dict[str, int]:
        if not self._empty("market_prices_daily"):
            return {"market_prices_daily": 0}
        rows: list[dict[str, object]] = []
        symbols = {*self.companies, "SPY", *(company.sector_etf for company in self.companies.values())}
        for symbol in sorted(symbols):
            path = self.demo_root / "prices" / f"{symbol}.json"
            frame = parse_yahoo_chart(path.read_text(encoding="utf-8"))
            rows.extend(price_rows(frame, source="yahoo_chart_public_snapshot", source_version="snapshot-2026-08-22"))
        return {"market_prices_daily": self.database.insert("market_prices_daily", rows)}

    def ingest_alternative(self) -> dict[str, int]:
        if not self._empty("alternative_data_daily"):
            return {"alternative_data_daily": 0}
        rows: list[dict[str, object]] = []
        lag = self.settings.features.availability_lags.get("wikipedia", 1)
        for ticker in self.companies:
            path = self.demo_root / "alternative" / f"{ticker}_wikipedia.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            observations = parse_pageviews(payload, company_id=ticker, availability_lag_days=lag)
            rows.extend(wikipedia_rows(observations, source="wikimedia_public_snapshot"))
        return {"alternative_data_daily": self.database.insert("alternative_data_daily", rows)}

    def build_features(self) -> dict[str, int]:
        if not self._empty("features_quarterly"):
            return {"features_quarterly": 0}
        financials = self.database.frame("select * from financials_quarterly")
        earnings = self.database.frame("select * from earnings_calendar")
        alternative = self.database.frame("select * from alternative_data_daily")
        features = FeatureBuilder(financials, earnings, alternative).build(
            horizons=list(self.settings.model.forecast_horizons)
        )
        rows = feature_rows(features, source="point_in_time_feature_engine")
        return {"features_quarterly": self.database.insert("features_quarterly", rows)}

    def train(self) -> dict[str, int]:
        if not self._empty("forecasts"):
            return {"model_runs": 0, "forecasts": 0}
        features = self.database.frame("select * from features_quarterly")
        financials = self.database.frame("select * from financials_quarterly")
        matrix = build_model_matrix(features, financials)
        specs = [
            ModelSpec("seasonal_naive", "fundamentals_only"),
            ModelSpec("historical_growth", "fundamentals_only"),
            ModelSpec("ridge", "fundamentals_only", {"alpha": 1.0}),
            ModelSpec("ridge", "fundamentals_alt", {"alpha": 1.0}),
        ]
        predictions, runs = expanding_window_forecasts(
            matrix,
            specs,
            minimum_training_quarters=self.settings.model.minimum_training_quarters * len(self.companies),
            seed=self.settings.model.random_seed,
        )
        created_at = datetime.now(UTC)
        commit = git_commit(self.settings.project_root)
        run_rows = [
            {
                "run_id": run.run_id,
                "run_timestamp": created_at,
                "git_commit": commit,
                "random_seed": self.settings.model.random_seed,
                "model_name": run.model_name,
                "feature_set": run.feature_columns,
                "training_start": run.training_start,
                "training_end": run.training_end,
                "test_start": run.test_start,
                "test_end": run.test_end,
                "hyperparameters": _clean(run.parameters),
                "observations": run.observations,
                "metrics": _clean(run.metrics),
                "status": "complete",
                "created_at": created_at,
            }
            for run in runs
        ]
        merge_keys = ["company_id", "fiscal_quarter", "forecast_cutoff_date", "horizon_days"]
        predictions = predictions.merge(
            matrix[merge_keys + ["revenue_year_ago"]],
            on=merge_keys,
            how="left",
            validate="many_to_one",
        )
        predictions = predictions[
            np.isfinite(predictions["forecast_revenue"]) & (predictions["forecast_revenue"] > 0)
        ].copy()
        predictions["target_forecast"] = np.log(predictions["forecast_revenue"] / predictions["revenue_year_ago"])
        predictions["forecast_id"] = predictions.apply(
            lambda row: canonical_hash(
                [row.run_id, row.company_id, row.fiscal_quarter, row.horizon_days, row.model_name, row.ablation]
            )[:24],
            axis=1,
        )
        predictions["explanation"] = predictions.apply(
            lambda row: {
                "research_confidence_not_profit_probability": row.confidence_score,
                "event_date_basis": "sec_filing_date_proxy",
            },
            axis=1,
        )
        predictions["status"] = "out_of_sample"
        predictions["source"] = "expanding_window_research"
        predictions["source_version"] = "demo-v1"
        predictions["created_at"] = created_at
        forecast_columns = [
            "forecast_id",
            "run_id",
            "company_id",
            "fiscal_quarter",
            "forecast_cutoff_date",
            "horizon_days",
            "model_name",
            "ablation",
            "target_forecast",
            "forecast_revenue",
            "actual_revenue",
            "interval_low",
            "interval_high",
            "confidence_score",
            "explanation",
            "status",
            "source",
            "source_version",
            "created_at",
        ]
        forecast_rows = _records(predictions, forecast_columns)
        return {
            "model_runs": self.database.insert("model_runs", run_rows),
            "forecasts": self.database.insert("forecasts", forecast_rows),
        }

    def variant(self) -> dict[str, int]:
        if not self._empty("variant_signals"):
            return {"consensus_estimates": 0, "variant_signals": 0}
        forecasts = self.database.frame("select * from forecasts")
        financials = self.database.frame("select * from financials_quarterly")
        expectations: dict[str, dict[str, Any]] = {}
        for row in forecasts.drop_duplicates(["company_id", "fiscal_quarter", "forecast_cutoff_date"]).itertuples(
            index=False
        ):
            proxy = historical_expectation_proxy(
                financials,
                company_id=row.company_id,
                fiscal_quarter=row.fiscal_quarter,
                cutoff=pd.Timestamp(row.forecast_cutoff_date).date(),
            )
            if proxy is None:
                continue
            key = canonical_hash([proxy.company_id, proxy.fiscal_quarter, proxy.as_of_date, proxy.mode])[:24]
            expectations[key] = {
                "estimate_id": key,
                "company_id": proxy.company_id,
                "fiscal_quarter": proxy.fiscal_quarter,
                "as_of_date": proxy.as_of_date,
                "consensus_revenue": proxy.revenue,
                "consensus_eps": None,
                "number_of_analysts": None,
                "mode": proxy.mode,
                "source": "expectation_proxy",
                "source_version": "seasonal-naive-v1",
                "created_at": datetime.now(UTC),
            }
        expectation_rows = list(expectations.values())
        expectation_count = self.database.insert("consensus_estimates", expectation_rows)
        expectation_frame = pd.DataFrame(expectation_rows)
        signals = build_variant_signals(forecasts, expectation_frame)
        signals["source"] = "variant_signal_engine"
        signals["source_version"] = "point-in-time-v1"
        signals["created_at"] = datetime.now(UTC)
        columns = [
            "signal_id",
            "forecast_id",
            "estimate_id",
            "company_id",
            "fiscal_quarter",
            "forecast_cutoff_date",
            "horizon_days",
            "variant",
            "variant_zscore",
            "variant_bucket",
            "confidence_score",
            "confidence_components",
            "expectation_mode",
            "source",
            "source_version",
            "created_at",
        ]
        return {
            "consensus_estimates": expectation_count,
            "variant_signals": self.database.insert("variant_signals", _records(signals, columns)),
        }

    def backtest(self) -> dict[str, int]:
        if not self._empty("backtest_results"):
            return {"backtest_results": 0}
        signals = self.database.frame("select * from variant_signals")
        earnings = self.database.frame(
            "select company_id, fiscal_quarter, earnings_date as event_date from earnings_calendar"
        )
        signals = signals.merge(earnings, on=["company_id", "fiscal_quarter"], validate="many_to_one")
        prices = self.database.frame("select * from market_prices_daily")
        benchmarks = {"market": "SPY", **{ticker: company.sector_etf for ticker, company in self.companies.items()}}
        study = run_event_study(
            signals,
            prices,
            self.settings.model.backtest.event_windows,
            benchmarks,
            bootstrap_samples=500,
            seed=self.settings.model.random_seed,
        )
        results = study.event_returns.copy()
        results["result_id"] = results.apply(
            lambda row: canonical_hash([row.signal_id, row.window_start, row.window_end])[:24], axis=1
        )
        results["portfolio_weight"] = None
        results["transaction_cost"] = (
            2 * (self.settings.model.backtest.transaction_cost_bps + self.settings.model.backtest.slippage_bps) / 10_000
        )
        results["source"] = "earnings_event_study"
        results["source_version"] = "event-study-v1"
        results["created_at"] = datetime.now(UTC)
        columns = [
            "result_id",
            "signal_id",
            "company_id",
            "event_date",
            "window_start",
            "window_end",
            "raw_return",
            "benchmark_return",
            "sector_return",
            "abnormal_return",
            "sector_adjusted_return",
            "portfolio_weight",
            "transaction_cost",
            "liquidity_status",
            "source",
            "source_version",
            "created_at",
        ]
        return {"backtest_results": self.database.insert("backtest_results", _records(results, columns))}


def demo_pipeline(settings: Settings) -> Pipeline:
    stages = DemoStages(settings)
    handlers = {stage: getattr(stages, stage) for stage in DEMO_STAGES}
    return Pipeline(settings, stage_handlers=handlers, stage_order=DEMO_STAGES)


class LiveStages(DemoStages):
    """Network-backed providers for users who explicitly opt into live retrieval."""

    def ingest_fundamentals(self) -> dict[str, int]:
        if not self.settings.sec_user_agent:
            raise ValueError("SEC_USER_AGENT with contact information is required in live mode")
        created_at = datetime.now(UTC)
        company_count = 0
        if self._empty("companies"):
            company_count = self.database.insert(
                "companies",
                [
                    {
                        "company_id": company.ticker,
                        "ticker": company.ticker,
                        "cik": company.cik,
                        "name": company.name,
                        "sector": company.sector,
                        "sector_etf": company.sector_etf,
                        "fiscal_year_end_month": company.fiscal_year_end_month,
                        "active": True,
                        "created_at": created_at,
                    }
                    for company in self.companies.values()
                ],
            )
        financial_count = event_count = 0
        if self._empty("financials_quarterly"):
            with CachedHttpClient(self.settings.project_root / "data" / "cache" / "sec") as http:
                client = SecClient(http, self.settings.sec_user_agent)
                rows = [
                    financial_to_row(item, source="sec_edgar_live")
                    for company in self.companies.values()
                    for item in normalize_company_facts(client.company_facts(company.cik), company)
                ]
            financial_count = self.database.insert("financials_quarterly", rows)
            if self._empty("earnings_calendar"):
                event_count = self.database.insert("earnings_calendar", filing_event_proxy_rows(rows))
        return {
            "companies": company_count,
            "financials_quarterly": financial_count,
            "earnings_calendar": event_count,
        }

    def ingest_prices(self) -> dict[str, int]:
        if not self._empty("market_prices_daily"):
            return {"market_prices_daily": 0}
        provider = YahooChartPriceProvider(self.settings.project_root / "data" / "cache" / "prices")
        symbols = {*self.companies, "SPY", *(company.sector_etf for company in self.companies.values())}
        rows = [
            item
            for symbol in sorted(symbols)
            for item in price_rows(
                provider.fetch(symbol, date(2009, 1, 1), date.today()),
                source="yahoo_chart_live_unofficial",
                source_version="retrieved-live",
            )
        ]
        return {"market_prices_daily": self.database.insert("market_prices_daily", rows)}

    def ingest_alternative(self) -> dict[str, int]:
        if not self._empty("alternative_data_daily"):
            return {"alternative_data_daily": 0}
        user_agent = self.settings.sec_user_agent
        if not user_agent:
            raise ValueError("An identifying SEC_USER_AGENT is also used for Wikimedia requests in live mode")
        with CachedHttpClient(self.settings.project_root / "data" / "cache" / "alternative") as http:
            provider = WikipediaProvider(
                http,
                user_agent=user_agent,
                availability_lag_days=self.settings.features.availability_lags.get("wikipedia", 1),
            )
            rows = [
                item
                for company in self.companies.values()
                for item in wikipedia_rows(
                    provider.fetch(company, date(2015, 7, 1), date.today()),
                    source="wikimedia_analytics_live",
                )
            ]
        return {"alternative_data_daily": self.database.insert("alternative_data_daily", rows)}


def live_pipeline(settings: Settings) -> Pipeline:
    if settings.mode != "live":
        settings = settings.model_copy(update={"mode": "live"})
    stages = LiveStages(settings)
    handlers = {stage: getattr(stages, stage) for stage in DEMO_STAGES}
    return Pipeline(settings, stage_handlers=handlers, stage_order=DEMO_STAGES)


def run_demo(settings: Settings, *, force: bool = False) -> PipelineSummary:
    if settings.mode != "demo":
        settings = settings.model_copy(update={"mode": "demo"})
    return demo_pipeline(settings).run(DEMO_STAGES, mode="demo", force=force)
