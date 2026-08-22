from __future__ import annotations

from pathlib import Path

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


def recruiter_statistics(database: Database) -> dict[str, int | float | None]:
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


def generate_resume_bullets(database: Database, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metrics = recruiter_statistics(database)
    if not metrics["companies"] or not metrics["historical_forecasts"]:
        text = (
            "# Resume bullets\n\n"
            "Not generated: the database contains insufficient measured research outputs. "
            "Run the complete pipeline before using quantitative claims.\n"
        )
        output_path.write_text(text, encoding="utf-8")
        return output_path
    improvement = metrics["alternative_incremental_mae_improvement"]
    if improvement is None:
        accuracy_bullet = (
            "- Evaluated expanding-window revenue forecasts against seasonal baselines; no matched MAE comparison "
            "was available."
        )
    elif improvement >= 0:
        accuracy_bullet = (
            f"- Reduced matched out-of-sample revenue forecast MAE by {improvement:.1%} versus a fundamentals-only "
            "Ridge model by adding point-in-time attention signals."
        )
    else:
        accuracy_bullet = (
            f"- Measured an {abs(improvement):.1%} deterioration in matched out-of-sample MAE after adding attention "
            "signals to a fundamentals-only Ridge model, documenting the negative result and model-risk controls."
        )
    spread = metrics["event_spread"]
    spread_text = f"; measured top-minus-bottom abnormal-return spread of {spread:.2%}" if spread is not None else ""
    text = "\n".join(
        [
            "# Resume bullet alternatives",
            "",
            (
                f"- Built a Python/DuckDB point-in-time research pipeline across {metrics['companies']} companies and "
                f"{metrics['company_quarters']} company-quarters, processing {metrics['alternative_observations']:,} "
                "daily public alternative-data observations."
            ),
            "",
            accuracy_bullet,
            "",
            (
                f"- Produced {metrics['historical_forecasts']:,} expanding-window forecasts and backtested "
                f"{metrics['backtest_observations']:,} earnings-event observations{spread_text}, with transaction-cost "
                "and statistical-robustness caveats."
            ),
            "",
        ]
    )
    output_path.write_text(text, encoding="utf-8")
    return output_path
