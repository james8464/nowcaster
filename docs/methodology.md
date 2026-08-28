# Methodology

## Research questions

Nowcaster evaluates two distinct systems:

1. **Equities:** can information observable before a company report improve a revenue forecast, differ from the then-observable expectation, and associate with later event returns?
2. **Crypto:** can shifted daily market features estimate a five-day directional distribution that survives purged walk-forward evaluation after costs?

Success at any one link does not imply a profitable strategy. The native app distinguishes model confidence, calibration, eligibility, and backtest readiness.

## Point-in-time clock

For equity event date `E` and horizon `h`, cutoff `C = E - h`. Every feature row must satisfy:

```text
maximum_input_available_date <= forecast_cutoff_date
```

SEC facts become available on filing date; Wikimedia features receive a publication lag; historical expectations use the latest revision at or before `C`. Labels join training only once the corresponding reported result is observable.

For crypto, all predictors are shifted one full daily bar and the target is future log return over the declared horizon. A signal generated at bar `t` cannot become a position until `t+1`.

Intraday models use a different target: whether an empirically selected target is touched before a protective stop within a fixed number of future finalized bars. If one bar contains both levels, the label uses adverse stop-before-target ordering. This target-before-stop definition is sealed with the model so a generic direction score cannot be relabelled as a trade probability.

## Equity research

Revenue tags follow a documented period-level precedence and year-to-date facts are differenced into standalone quarters where required. The target is revenue log growth, reconstructed to a strictly positive revenue forecast. Features include lagged revenue, seasonality, prior growth, and lagged public-attention aggregates.

Baselines include seasonal naive and historical growth. Linear and nonlinear candidates use fold-local preprocessing. Expanding windows are independent by forecast horizon. Prediction intervals and calibration are learned only from past residuals. The primary ablation compares matched fundamentals-plus-attention and fundamentals-only models.

The expectation source is explicit. The bundled demo uses prior-year seasonality labelled `expectation_proxy`; it is not Wall Street consensus. Variants are normalized forecast-minus-expectation differences. Event returns use identical company, market, and sector dates.

## Crypto research

The daily demo uses explicitly disclosed frozen BTC-USD and ETH-USD proxy histories. Live intraday research identifies Binance BTCUSDT and ETHUSDT USDT spot products exactly and never splices those venue bars with the daily proxy. Candidate features include lagged returns, momentum, rolling volatility, drawdown, volume behavior, trend, and regime context. The ensemble combines regularized logistic regression and histogram gradient boosting where the training sample supports it. Probability calibration and abstention thresholds are fit inside historical folds.

The system can emit long research, short research, or abstain. The output is a research posture with evidence and invalidation—not an instruction to trade.

## Calibration, selectivity, and drift

Calibration is fitted only to out-of-fold development predictions whose outcomes were already observable. The evidence records the raw sample, autocorrelation-adjusted effective sample size, Brier score, log loss, expected calibration error, probability interval, outcome definition, and slice identity. At least 100 effective observations are required; isotonic calibration requires at least 1,000 raw observations.

A threshold is selected inside development data to balance precision against selective coverage. The model may abstain on most bars if only a small subset has a positive lower cost-adjusted edge. That restraint is intentional: accuracy computed only on selected predictions must always be read beside coverage.

Live model drift monitors feature and prediction distributions, calibration residuals, realized costs, latency, and net edge. A warning blocks new alerts. Confirmed material model drift invalidates the readiness receipt; no automatic retraining can reuse the old forward record.

## Backtest design

The final 20% of chronology is isolated once. Development uses expanding purged walk-forward folds with embargo where applicable. Preprocessing, calibration, model fitting, and thresholds occur inside training data. Positions are lagged, non-overlapping per instrument, costed for turnover/slippage/borrow, volatility-targeted, and gross-exposure-capped. Calendar duration drives annualization.

Reported evidence includes development, final-test, and full-period metrics; equity/drawdown/risk/exposure/turnover curves; monthly results; block bootstrap; deflated Sharpe with trial adjustment; HAC or clustered inference; multiple-testing correction; stability; regimes; and sensitivities. Details and readiness gates are in [backtest_protocol.md](backtest_protocol.md).

## Bundled findings

- Equity: the full revenue model beat seasonal naive, but public-attention features did not add value versus the matched fundamentals-only model. The event spread was near zero and negative.
- BTC-USD daily proxy: positive historical result but only two-thirds of subperiods profitable, so status is research-only.
- ETH-USD daily proxy: sample, development Sharpe, bootstrap, deflated-Sharpe, and stability gates fail; status is not ready.

No bundled system is decision-ready.

## Known limitations

- Small equity universe, filing-date event proxies, and no bundled historical sell-side consensus.
- Public-data and asset survivorship.
- Unofficial daily price endpoint, with no exchange-level execution or intraday microstructure.
- Regime change, capacity, latency, taxes, borrow, outages, and stressed spreads remain incompletely modelled.
- Model/threshold search and exploratory slicing create researcher degrees of freedom despite statistical correction.

This is reproducible educational research and not investment advice. Historical results do not guarantee future profitability.
