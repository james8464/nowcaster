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
            "Features are reconstructed at a seven-day pre-event cutoff and must satisfy input availability date less "
            "than or equal to cutoff. Models use expanding windows with fold-local preprocessing; no random split is "
            "used. The demo compares seasonal/history baselines with Ridge models using fundamentals-only and "
            "fundamentals-plus-attention ablations.",
        ),
        (
            "Forecast Accuracy",
            f"{_result_sentence(improvement)} Results cover {metrics['historical_forecasts']:,} out-of-sample "
            "forecasts.",
        ),
        (
            "Incremental Value of Alternative Data",
            f"{incremental_sentence} This ablation is the relevant test of incremental attention-data value and its "
            "negative result is not suppressed. Coverage begins only when Wikimedia observations become available; "
            "early folds cannot use those features.",
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
