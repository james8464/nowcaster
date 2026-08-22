# Methodology

## Research questions

1. Fundamental forecast: does alternative data improve quarterly revenue forecasts?
2. Variant perception: how far is a model forecast from the expectation observable before the event?
3. Return association: is the pre-event variant associated with later abnormal returns?

Success at one link does not imply success at another.

## Point-in-time clock

For event date `E` and horizon `h`, the forecast cutoff is `C = E - h`. A feature row is admissible only when:

```text
maximum_input_available_date <= forecast_cutoff_date
```

SEC facts become available on filing date. Wikimedia observations use an explicit one-day lag. Historical estimate selection uses the latest revision with `as_of_date <= C`. Macro features accept ALFRED-style vintages only; latest-revised series are rejected. Automated mutation tests alter post-cutoff inputs and require historical features to remain unchanged.

## Fundamentals and features

Revenue tags follow a per-period precedence: `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`, then `Revenues`. YTD facts are differenced into standalone quarters where necessary. Features include lagged revenue, quarter seasonality, QoQ/YoY log growth, prior-year same-quarter revenue, and 28-day attention level, maximum, momentum, abnormal z-score, and YoY growth where history permits.

Missing inputs remain missing. The system does not backfill future values. Fold-local median imputation and scaling are learned only on training observations.

## Forecast models

- Seasonal naive: prior-year same-quarter revenue.
- Historical growth: applies the last observable YoY growth to prior-year revenue.
- OLS, Ridge, Elastic Net: interpretable linear pipelines with company one-hot encoding.
- Histogram gradient boosting: nonlinear benchmark gated by sample size.

Evaluation uses expanding cutoff dates. Every fold satisfies `training_end < test_start`. No random split is used. Metrics include MAE, RMSE, MAPE, bias, and directional accuracy. Intervals are residual-based research intervals; confidence combines empirical residual dispersion and coverage and is not calibrated as a probability of profit.

## Ablation logic

The primary incremental-data comparison is:

```text
Ridge(fundamentals + attention) vs Ridge(fundamentals only)
```

Comparing a full Ridge model with seasonal naive measures total modelling improvement, not the incremental contribution of attention. In the verified demo the full model beat seasonal naive, while adding attention to Ridge worsened matched MAE. The report displays both results.

## Expectations and variant

Real consensus can be imported with:

```text
ticker,fiscal_quarter,as_of_date,consensus_revenue,consensus_eps,number_of_analysts
```

When unavailable, demo mode uses prior-year same-quarter revenue as an `expectation_proxy`. It is never labelled actual consensus.

```text
Revenue variant = (forecast revenue - expectation revenue) / expectation revenue
```

Variants are z-scored within forecast cutoff and horizon and bucketed as strongly positive, positive, neutral, negative, or strongly negative.

## Event study and portfolio research

The price anchor is the first trading date on or after the event proxy. Company and benchmark returns use identical start/end trading dates. Reported fields include raw, SPY-adjusted, and sector-ETF-adjusted returns for `[-1,+1]`, `[0,+1]`, `[0,+3]`, and `[0,+5]`.

Bucket summaries report mean, median, hit rate, standard deviation, t-statistic, sample size, and seeded bootstrap intervals. The cross-sectional regression uses Newey-West covariance but remains exploratory given repeated model signals, overlapping events, small cross-sections, selection, and multiple testing.

The optional portfolio research takes equal-weight positive and negative legs, caps positions, removes duplicate company-events, filters explicit liquidity failures, and subtracts round-trip transaction cost and slippage. It omits borrow availability, intraday fills, taxes, capacity, latency, and financing; it is not an executable strategy.

## Known limitations

- Three-company demo universe and public-data survivorship.
- SEC filing dates rather than precise earnings timestamps.
- Expectation proxy rather than historical sell-side consensus.
- Wikimedia pageviews are a noisy attention measure with coverage only from July 2015.
- Unofficial Yahoo chart endpoint and daily rather than intraday prices.
- Revenue tag and fiscal-calendar complexity.
- Repeated model variants for a single event reduce effective sample size.
- Hyperparameter selection and exploratory slicing can overstate evidence.

No result should be interpreted as a promise of profitability or investment advice.
