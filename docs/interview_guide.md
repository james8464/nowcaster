# Interview guide

## 60-second summary

I built a reproducible public-data earnings nowcasting platform around a hard investment-research question: whether attention data improves quarterly revenue forecasts and whether the resulting expectation divergence relates to event returns. The differentiator is the point-in-time discipline—every feature carries an availability date, models use expanding windows, estimate revisions are selected as of cutoff, and leakage invariants are tested. The demo processes 155 real SEC company-quarters and 12,210 Wikimedia observations. The honest result is mixed: the full Ridge model beat seasonal naive, but attention worsened MAE versus fundamentals-only Ridge, and the event spread was economically tiny.

## Walkthrough

1. Start on Overview: sample size, mode badge, and forecast error.
2. Show Data Quality: public source labels, freshness, revised-macro exclusion, filing-date proxy.
3. Show Model Performance: seasonal baseline versus matched Ridge ablations.
4. Show Forecast Monitor: explain expectation proxy and cutoff-safe variant z-score.
5. Show Event Study: discuss identical-date adjustment, repeated signals, and effective sample size.
6. Open the generated research note and measured resume bullets.

## Decisions worth discussing

- DuckDB provides a low-friction analytical database while SQLAlchemy keeps the schema portable.
- Long-form feature storage makes per-feature availability auditable before pivoting to a model matrix.
- Expanding windows reflect how the model would have learned through time; random splitting would leak regimes.
- A same-model ablation is required to attribute incremental value to attention.
- Latest-revised macro snapshots are excluded rather than pretending they were historically observable.
- Demo consensus and event dates are labelled proxies instead of being presented as institutional data.
- Simple baselines remain first-class because a sophisticated model must earn its complexity.

## Strongest result

The engineering and evaluation framework is the strongest result: deterministic snapshots, normalized financials, cutoff-aware features, reproducible folds, persisted provenance, and an end-to-end recruiter demo requiring no keys.

## Weakest result

The alternative signal itself is weak in this small demo. Attention worsened matched Ridge MAE by 8.4%, and the [0,+3] top-minus-bottom abnormal-return spread was -0.04%. That is useful evidence against overclaiming, not a failure to hide.

## What institutional data would improve

- Timestamped consensus histories with revisions, dispersion, breadth, and analyst counts.
- Precise earnings timestamps and after-hours/intraday market data.
- Licensed adjusted prices, corporate actions, borrow, liquidity, and realistic fill models.
- Transaction, foot-traffic, app, web-search, and card-spend signals with stable coverage.
- Larger sector-balanced universe and true PIT security master.
- Nested model selection, clustered inference, embargoed folds, and a preregistered test plan.

## Common questions

**Why not predict long/short directly?** Revenue forecasting, expectation surprise, and return response have different labels and error processes. Collapsing them makes leakage and attribution harder to diagnose.

**Does 28.3% lower MAE prove the alternative data works?** No. That compares the full Ridge model with seasonal naive. The matched ablation shows attention worsened MAE by 8.4% versus fundamentals-only Ridge.

**Is the confidence score a win probability?** No. It reflects residual dispersion and coverage. Calibration to profit probability would require a separate, sufficiently large return model and still would not guarantee profit.

**Why include a negative result?** Investment research is about falsification and risk-adjusted evidence. Reporting the negative ablation prevents the project from becoming a misleading ML tutorial.

**What does GS Quant do here?** The parent source tree supplies an optional local return cross-check. This standalone project is not a Goldman Sachs product and is not endorsed by Goldman Sachs.
