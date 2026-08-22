from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from src.database.engine import Database


@dataclass(frozen=True)
class OverviewView:
    data_mode: str
    company_count: int
    company_quarter_count: int
    alternative_observation_count: int
    historical_forecast_count: int
    event_return_count: int
    quality_issue_count: int
    latest_refresh: str | None


@dataclass(frozen=True)
class CompanyResearchView:
    fundamentals: pd.DataFrame
    forecasts: pd.DataFrame
    attention: pd.DataFrame


def default_database_url() -> str:
    configured = os.getenv("NOWCASTER_DATABASE_URL")
    if configured:
        return configured
    root = Path(__file__).resolve().parents[1]
    return f"duckdb:///{root / 'data' / 'nowcaster.duckdb'}"


@st.cache_data(show_spinner=False, ttl=60)
def load_overview(database_url: str) -> OverviewView:
    database = Database.from_url(database_url)
    mode = database.scalar("select mode from pipeline_runs where status = 'success' order by ended_at desc limit 1")
    latest = database.scalar("select cast(max(ended_at) as varchar) from pipeline_runs where status = 'success'")
    labels = {"demo": "demo_real_snapshot", "live": "live_provider", "test": "test_fixture"}
    return OverviewView(
        data_mode=labels.get(str(mode), "uninitialized"),
        company_count=int(database.scalar("select count(*) from companies") or 0),
        company_quarter_count=int(database.scalar("select count(*) from financials_quarterly") or 0),
        alternative_observation_count=int(database.scalar("select count(*) from alternative_data_daily") or 0),
        historical_forecast_count=int(database.scalar("select count(*) from forecasts") or 0),
        event_return_count=int(database.scalar("select count(*) from backtest_results") or 0),
        quality_issue_count=int(database.scalar("select count(*) from data_quality_issues") or 0),
        latest_refresh=str(latest) if latest is not None else None,
    )


FORECAST_MONITOR_COLUMNS = [
    "company_id",
    "fiscal_quarter",
    "earnings_date",
    "forecast_cutoff_date",
    "horizon_days",
    "model_name",
    "ablation",
    "forecast_revenue",
    "actual_revenue",
    "expectation_revenue",
    "expectation_mode",
    "variant",
    "absolute_variant",
    "variant_zscore",
    "variant_bucket",
    "confidence_score",
]


@st.cache_data(show_spinner=False, ttl=60)
def load_forecast_monitor(database_url: str, horizon: int) -> pd.DataFrame:
    database = Database.from_url(database_url)
    frame = database.frame(
        """
        select v.company_id, v.fiscal_quarter, e.earnings_date, v.forecast_cutoff_date,
               v.horizon_days, f.model_name, f.ablation, f.forecast_revenue, f.actual_revenue,
               c.consensus_revenue as expectation_revenue, v.expectation_mode, v.variant,
               abs(v.variant) as absolute_variant, v.variant_zscore, v.variant_bucket,
               v.confidence_score
        from variant_signals v
        join forecasts f on v.forecast_id = f.forecast_id
        join consensus_estimates c on v.estimate_id = c.estimate_id
        join earnings_calendar e on v.company_id = e.company_id and v.fiscal_quarter = e.fiscal_quarter
        where v.horizon_days = :horizon
        """,
        {"horizon": horizon},
    )
    if frame.empty:
        return pd.DataFrame(columns=FORECAST_MONITOR_COLUMNS)
    return frame[FORECAST_MONITOR_COLUMNS].sort_values("absolute_variant", ascending=False).reset_index(drop=True)


def forecast_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["model_name", "ablation", "horizon_days", "n", "mae", "rmse", "mape", "directional_accuracy"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, float | int | str]] = []
    for key, group in frame.groupby(["model_name", "ablation", "horizon_days"], dropna=False):
        errors = group["forecast_revenue"] - group["actual_revenue"]
        denominator = group["actual_revenue"].replace(0, np.nan).abs()
        if {"forecast_acceleration", "actual_acceleration"} <= set(group):
            directional = float((group["forecast_acceleration"] == group["actual_acceleration"]).mean())
        else:
            directional = math.nan
        rows.append(
            {
                "model_name": key[0],
                "ablation": key[1],
                "horizon_days": int(key[2]),
                "n": len(group),
                "mae": float(errors.abs().mean()),
                "rmse": float(np.sqrt(np.square(errors).mean())),
                "mape": float((errors.abs() / denominator).mean()),
                "directional_accuracy": directional,
            }
        )
    return pd.DataFrame(rows, columns=columns)


@st.cache_data(show_spinner=False, ttl=60)
def load_model_performance(database_url: str) -> pd.DataFrame:
    database = Database.from_url(database_url)
    frame = database.frame(
        """
        select f.model_name, f.ablation, f.horizon_days, f.forecast_revenue, f.actual_revenue,
               sign(f.forecast_revenue - q.feature_value) as forecast_acceleration,
               sign(f.actual_revenue - q.feature_value) as actual_acceleration
        from forecasts f
        left join features_quarterly q
          on f.company_id = q.company_id and f.fiscal_quarter = q.fiscal_quarter
         and f.horizon_days = q.horizon_days and q.feature_name = 'revenue_level_lag1'
        where f.actual_revenue is not null
        """
    )
    return forecast_metrics(frame)


@st.cache_data(show_spinner=False, ttl=60)
def load_company_research(database_url: str, company_id: str) -> CompanyResearchView:
    database = Database.from_url(database_url)
    fundamentals = database.frame(
        """
        select fiscal_quarter, period_end, revenue, operating_income, net_income, diluted_eps,
               available_date, quality_status
        from financials_quarterly where company_id = :company order by period_end
        """,
        {"company": company_id},
    )
    forecasts = database.frame(
        """
        select fiscal_quarter, forecast_cutoff_date, model_name, ablation, forecast_revenue,
               actual_revenue, interval_low, interval_high, confidence_score
        from forecasts where company_id = :company order by forecast_cutoff_date
        """,
        {"company": company_id},
    )
    attention = database.frame(
        """
        select observation_date, signal, value, available_date
        from alternative_data_daily where company_id = :company order by observation_date
        """,
        {"company": company_id},
    )
    return CompanyResearchView(fundamentals, forecasts, attention)


@st.cache_data(show_spinner=False, ttl=60)
def load_event_study(database_url: str) -> pd.DataFrame:
    database = Database.from_url(database_url)
    return database.frame(
        """
        select b.company_id, b.event_date, b.window_start, b.window_end, b.raw_return,
               b.abnormal_return, b.sector_adjusted_return, v.variant, v.variant_zscore,
               v.variant_bucket, v.expectation_mode
        from backtest_results b join variant_signals v on b.signal_id = v.signal_id
        order by b.event_date
        """
    )


@st.cache_data(show_spinner=False, ttl=60)
def load_data_quality(database_url: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    database = Database.from_url(database_url)
    issues = database.frame(
        """
        select stage, severity, rule, entity_key, message, detected_at
        from data_quality_issues order by detected_at desc
        """
    )
    coverage = database.frame(
        """
        select 'financials_quarterly' as dataset, source, count(*) as rows,
               cast(max(created_at) as varchar) as latest_refresh from financials_quarterly group by source
        union all
        select 'market_prices_daily', source, count(*), cast(max(created_at) as varchar)
        from market_prices_daily group by source
        union all
        select 'alternative_data_daily', source, count(*), cast(max(created_at) as varchar)
        from alternative_data_daily group by source
        union all
        select 'features_quarterly', source, count(*), cast(max(created_at) as varchar)
        from features_quarterly group by source
        """
    )
    return issues, coverage
