from __future__ import annotations

from src.database.engine import Database


def _forecast_improvement(database: Database) -> float | None:
    forecasts = database.frame(
        """
        select company_id, fiscal_quarter, horizon_days, model_name, ablation,
               forecast_revenue, actual_revenue
        from forecasts where actual_revenue is not null and status = 'out_of_sample'
        """
    )
    if forecasts.empty:
        return None
    keys = ["company_id", "fiscal_quarter", "horizon_days"]
    baseline = forecasts[forecasts["model_name"] == "seasonal_naive"][keys + ["forecast_revenue", "actual_revenue"]]
    alternative = forecasts[(forecasts["model_name"] == "ridge") & (forecasts["ablation"] == "fundamentals_alt")][
        keys + ["forecast_revenue"]
    ]
    matched = baseline.merge(alternative, on=keys, suffixes=("_baseline", "_alternative"), validate="one_to_one")
    if matched.empty:
        return None
    baseline_mae = (matched["forecast_revenue_baseline"] - matched["actual_revenue"]).abs().mean()
    alternative_mae = (matched["forecast_revenue_alternative"] - matched["actual_revenue"]).abs().mean()
    if not baseline_mae:
        return None
    return float((baseline_mae - alternative_mae) / baseline_mae)


def _alternative_incremental_improvement(database: Database) -> float | None:
    forecasts = database.frame(
        """
        select company_id, fiscal_quarter, horizon_days, model_name, ablation,
               forecast_revenue, actual_revenue
        from forecasts where actual_revenue is not null and status = 'out_of_sample'
        """
    )
    keys = ["company_id", "fiscal_quarter", "horizon_days"]
    alternative = forecasts[(forecasts["model_name"] == "ridge") & (forecasts["ablation"] == "fundamentals_alt")][
        keys + ["forecast_revenue", "actual_revenue"]
    ]
    fundamentals = forecasts[(forecasts["model_name"] == "ridge") & (forecasts["ablation"] == "fundamentals_only")][
        keys + ["forecast_revenue"]
    ]
    matched = alternative.merge(fundamentals, on=keys, suffixes=("_alternative", "_fundamentals"))
    if matched.empty:
        return None
    alternative_mae = (matched["forecast_revenue_alternative"] - matched["actual_revenue"]).abs().mean()
    fundamentals_mae = (matched["forecast_revenue_fundamentals"] - matched["actual_revenue"]).abs().mean()
    if not fundamentals_mae:
        return None
    return float((fundamentals_mae - alternative_mae) / fundamentals_mae)


def _event_spread(database: Database) -> float | None:
    frame = database.frame(
        """
        select v.variant_zscore, b.abnormal_return
        from backtest_results b join variant_signals v on b.signal_id = v.signal_id
        where b.window_start = 0 and b.window_end = 3 and b.abnormal_return is not null
        """
    )
    if frame.empty:
        return None
    top = frame.loc[frame["variant_zscore"] >= 0.5, "abnormal_return"]
    bottom = frame.loc[frame["variant_zscore"] <= -0.5, "abnormal_return"]
    if top.empty or bottom.empty:
        return None
    return float(top.mean() - bottom.mean())


def research_statistics(database: Database) -> dict[str, int | float | None]:
    return {
        "companies": int(database.scalar("select count(distinct company_id) from financials_quarterly") or 0),
        "company_quarters": int(database.scalar("select count(*) from financials_quarterly") or 0),
        "alternative_observations": int(database.scalar("select count(*) from alternative_data_daily") or 0),
        "financial_filings": int(database.scalar("select count(*) from financials_quarterly") or 0),
        "feature_names": int(database.scalar("select count(distinct feature_name) from features_quarterly") or 0),
        "feature_rows": int(database.scalar("select count(*) from features_quarterly") or 0),
        "historical_forecasts": int(
            database.scalar("select count(*) from forecasts where status = 'out_of_sample'") or 0
        ),
        "historical_events": int(database.scalar("select count(*) from earnings_calendar") or 0),
        "backtest_observations": int(database.scalar("select count(*) from backtest_results") or 0),
        "forecast_mae_improvement": _forecast_improvement(database),
        "alternative_incremental_mae_improvement": _alternative_incremental_improvement(database),
        "event_spread": _event_spread(database),
    }
