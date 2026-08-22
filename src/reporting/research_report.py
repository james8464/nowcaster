from __future__ import annotations

from pathlib import Path

from src.database.engine import Database
from src.reporting.case_study import render_case_study, select_case_study
from src.reporting.recruiter import recruiter_statistics

REQUIRED_REPORT_SECTIONS = (
    "Executive Summary",
    "Research Question",
    "Dataset",
    "Methodology",
    "Forecast Accuracy",
    "Incremental Value of Alternative Data",
    "Variant-Perception Analysis",
    "Event-Study Results",
    "Crypto Walk-Forward Results",
    "Strategy Readiness",
    "Example Investment Case",
    "Risks and Limitations",
    "Conclusion",
)


def _result_sentence(improvement: float | None) -> str:
    if improvement is None:
        return "Insufficient evidence is available for a matched forecast-accuracy comparison."
    if improvement >= 0:
        return f"The alternative-signal Ridge specification reduced matched out-of-sample MAE by {improvement:.1%}."
    return (
        f"The alternative-signal Ridge specification increased matched out-of-sample MAE by {abs(improvement):.1%}; "
        "the alternative data did not improve this demo result."
    )


def generate_research_report(database: Database, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metrics = recruiter_statistics(database)
    improvement = metrics["forecast_mae_improvement"]
    incremental = metrics["alternative_incremental_mae_improvement"]
    event_spread = metrics["event_spread"]
    case_text = render_case_study(select_case_study(database))
    evidence_status = (
        "The database contains measured out-of-sample and event-study outputs."
        if metrics["historical_forecasts"]
        else "Insufficient evidence: run the full pipeline before drawing research conclusions."
    )
    spread_sentence = (
        f"The [0,+3] top-minus-bottom variant abnormal-return spread was {event_spread:.2%}."
        if event_spread is not None
        else "Insufficient evidence is available for a two-sided event spread."
    )
    incremental_sentence = (
        "Insufficient evidence is available for a matched fundamentals-only ablation."
        if incremental is None
        else (
            f"Adding attention signals reduced MAE by {incremental:.1%} versus fundamentals-only Ridge."
            if incremental >= 0
            else f"Adding attention signals increased MAE by {abs(incremental):.1%} versus fundamentals-only Ridge."
        )
    )
    crypto_runs = database.frame(
        """
        select strategy_name, readiness, development_metrics, final_test_metrics, readiness_reasons
        from backtest_runs where asset_class = 'crypto' order by strategy_name
        """
    )
    crypto_lines: list[str] = []
    readiness_lines: list[str] = []
    for row in crypto_runs.itertuples(index=False):
        development = row.development_metrics if isinstance(row.development_metrics, dict) else {}
        final_test = row.final_test_metrics if isinstance(row.final_test_metrics, dict) else {}
        development_sharpe = development.get("sharpe")
        final_sharpe = final_test.get("sharpe")
        crypto_lines.append(
            f"{row.strategy_name}: development Sharpe {_format_metric(development_sharpe)}, "
            f"isolated final-test Sharpe {_format_metric(final_sharpe)}, status {row.readiness}."
        )
        reasons = row.readiness_reasons if isinstance(row.readiness_reasons, list) else []
        reason_text = "; ".join(str(reason) for reason in reasons) or "all declared gates passed"
        readiness_lines.append(f"{row.strategy_name} is {row.readiness}; {reason_text}.")
    crypto_summary = " ".join(crypto_lines) or "No crypto walk-forward run is available."
    readiness_summary = " ".join(readiness_lines) or "No strategy readiness assessment is available."
    sections = [
        (
            "Executive Summary",
            f"{evidence_status} {_result_sentence(improvement)} {incremental_sentence} {spread_sentence}",
        ),
        (
            "Research Question",
            "Can publicly observable attention signals improve pre-earnings quarterly-revenue forecasts relative to "
            "historical baselines, and is model-to-expectation divergence associated with later event returns? "
            "Fundamental forecasting, expectation surprise, and return prediction are treated as distinct questions.",
        ),
        (
            "Dataset",
            f"The persisted sample contains {metrics['companies']} companies, {metrics['company_quarters']} "
            f"company-quarters, {metrics['financial_filings']} normalized SEC filing records, "
            f"{metrics['alternative_observations']:,} daily alternative-data observations, and "
            f"{metrics['historical_events']} historical event-date records. Demo event dates are explicitly labelled "
            "SEC filing-date proxies. Latest-revised macro snapshots are excluded from historical features.",
        ),
        (
            "Methodology",
            "Features are reconstructed independently at 1-, 7-, 14-, and 30-day pre-event cutoffs and must satisfy "
            "input availability date less than or equal to cutoff. Each horizon uses its own expanding-window models; "
            "a target can enter training only after its reported result is available. Preprocessing is fit within each "
            "fold and no random split is used. The demo compares seasonal/history baselines with Ridge models using "
            "fundamentals-only and fundamentals-plus-attention ablations.",
        ),
        (
            "Forecast Accuracy",
            f"{_result_sentence(improvement)} Results cover {metrics['historical_forecasts']:,} out-of-sample "
            "forecasts.",
        ),
        (
            "Incremental Value of Alternative Data",
            f"{incremental_sentence} This matched ablation is the relevant test of incremental attention-data value; "
            "the measured result is reported regardless of sign. Coverage begins only when Wikimedia observations "
            "become available, so earlier observations cannot use those features.",
        ),
        (
            "Variant-Perception Analysis",
            "Variant is (model revenue forecast minus expectation revenue) divided by expectation revenue. In demo "
            "mode the comparison is a transparent prior-year seasonal expectation proxy—not actual Wall Street "
            "consensus. Scores are standardized within cutoff and horizon cohorts.",
        ),
        (
            "Event-Study Results",
            f"{spread_sentence} The database contains {metrics['backtest_observations']:,} signal-window observations. "
            "Market and sector adjustments use identical trading dates. Bootstrap and Newey-West diagnostics are "
            "exploratory and do not eliminate event overlap, selection bias, or multiple testing.",
        ),
        (
            "Crypto Walk-Forward Results",
            f"{crypto_summary} These are one-bar-lagged, cost-adjusted out-of-sample simulations. "
            "The final 20% is reported separately and is never blended into development metrics.",
        ),
        (
            "Strategy Readiness",
            f"{readiness_summary} Readiness also checks sample size, block-bootstrap evidence, deflated Sharpe, "
            "subperiod stability, stressed costs, and drawdown. No label constitutes an assurance of future profit.",
        ),
        ("Example Investment Case", case_text),
        (
            "Risks and Limitations",
            "The universe is small; public attention data are noisy; SEC filing dates are imperfect earnings-time "
            "proxies; the expectation proxy is not sell-side consensus; Yahoo prices are from an unofficial endpoint; "
            "revisions, survivorship, borrow, liquidity, slippage, taxes, intraday execution, and capacity can alter "
            "results. A research confidence score is not a probability of profit.",
        ),
        (
            "Conclusion",
            "This platform is a reproducible research and interview artifact. It demonstrates point-in-time data "
            "engineering and honest out-of-sample evaluation; it does not establish a profitable trading strategy.",
        ),
    ]
    text = "# Alternative-Data Earnings Nowcaster — Research Note\n\n"
    text += "\n\n".join(f"## {index}. {title}\n\n{body}" for index, (title, body) in enumerate(sections, 1))
    text += "\n\n---\n\nThis report is for research and education only and is not investment advice.\n"
    output_path.write_text(text, encoding="utf-8")
    return output_path


def _format_metric(value: object) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "unavailable"
