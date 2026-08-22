from __future__ import annotations

from dataclasses import dataclass

from src.database.engine import Database


@dataclass(frozen=True)
class CaseStudy:
    company_id: str
    fiscal_quarter: str
    event_date: str
    model_name: str
    expectation_mode: str
    expectation_revenue: float
    forecast_revenue: float
    actual_revenue: float | None
    variant: float
    variant_zscore: float | None
    abnormal_return: float | None
    confidence_score: float | None


def select_case_study(database: Database) -> CaseStudy | None:
    frame = database.frame(
        """
        select v.company_id, v.fiscal_quarter, cast(e.earnings_date as varchar) as event_date,
               f.model_name, v.expectation_mode, c.consensus_revenue as expectation_revenue,
               f.forecast_revenue, f.actual_revenue, v.variant, v.variant_zscore,
               b.abnormal_return, v.confidence_score
        from variant_signals v
        join forecasts f on v.forecast_id = f.forecast_id
        join consensus_estimates c on v.estimate_id = c.estimate_id
        join earnings_calendar e on v.company_id = e.company_id and v.fiscal_quarter = e.fiscal_quarter
        left join backtest_results b on v.signal_id = b.signal_id and b.window_start = 0 and b.window_end = 3
        where f.model_name = 'ridge' and f.ablation = 'fundamentals_alt'
        order by abs(v.variant) desc, v.confidence_score desc nulls last
        limit 1
        """
    )
    if frame.empty:
        return None
    row = frame.iloc[0]
    return CaseStudy(
        company_id=str(row.company_id),
        fiscal_quarter=str(row.fiscal_quarter),
        event_date=str(row.event_date),
        model_name=str(row.model_name),
        expectation_mode=str(row.expectation_mode),
        expectation_revenue=float(row.expectation_revenue),
        forecast_revenue=float(row.forecast_revenue),
        actual_revenue=float(row.actual_revenue) if row.actual_revenue is not None else None,
        variant=float(row.variant),
        variant_zscore=float(row.variant_zscore) if row.variant_zscore is not None else None,
        abnormal_return=float(row.abnormal_return) if row.abnormal_return is not None else None,
        confidence_score=float(row.confidence_score) if row.confidence_score is not None else None,
    )


def render_case_study(case: CaseStudy | None) -> str:
    if case is None:
        return "Insufficient evidence to select a historical investment case."
    reported = f"{case.actual_revenue:,.0f}" if case.actual_revenue is not None else "unavailable"
    reaction = f"{case.abnormal_return:.2%}" if case.abnormal_return is not None else "unavailable"
    outcome = (
        "directionally consistent with the variant"
        if case.abnormal_return is not None and case.abnormal_return * case.variant > 0
        else "not directionally consistent with the variant"
    )
    return (
        f"**{case.company_id} {case.fiscal_quarter}** (event-date proxy: {case.event_date}) was selected because it "
        "had "
        f"one of the largest absolute model-to-expectation divergences. The {case.expectation_mode.replace('_', ' ')} "
        f"was {case.expectation_revenue:,.0f}; the {case.model_name} forecast was {case.forecast_revenue:,.0f} "
        f"({case.variant:.2%} variant), and reported revenue was {reported}. The [0,+3] market-adjusted reaction was "
        f"{reaction}, {outcome}. Attention features were eligible only if available before cutoff; this output does "
        "not "
        "claim causal attribution. The thesis could have been invalidated by proxy error, filing-date timing, omitted "
        "drivers, revisions, transaction costs, or a market reaction unrelated to revenue."
    )
