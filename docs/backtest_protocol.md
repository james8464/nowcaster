# Backtest protocol

## Purpose

The protocol is designed to disprove fragile research before presenting it as actionable evidence. A positive return or Sharpe ratio alone cannot make a system decision-ready.

## Dataset clock

Every input has an `available_date`. Equity features must satisfy `maximum_input_available_date <= forecast_cutoff_date`; crypto predictors are shifted by one bar. Labels become trainable only after their return or reported result is fully observable. Frozen public snapshots include source URLs, retrieval times, notes, and SHA-256 checksums.

## Validation design

1. Sort observations chronologically.
2. Reserve the final 20% once as an isolated final test.
3. Run expanding walk-forward development folds on the preceding 80%.
4. Purge training labels whose outcome overlaps a validation boundary and apply the configured embargo.
5. Fit imputation, scaling, calibration, and models inside each training fold only.
6. Freeze the selected rules before evaluating the final segment.

No random train/test split is used.

## Execution model

- Signal-to-position lag: one bar.
- Crypto holding period: five days, with non-overlapping positions per instrument.
- Capital: normalized to one unit per instrument; overlapping signals cannot multiply available capital.
- Costs: explicit transaction cost and slippage on turnover; short borrow cost where applicable.
- Risk: volatility targeting with capped gross exposure.
- Annualization: derived from actual calendar duration, not assumed row counts.

## Reported evidence

Development, final-test, and full-period values are never blended in the UI. Metrics include cumulative and annualized return, volatility, Sharpe, Sortino, Calmar, maximum drawdown, hit rate, profit factor, turnover, average exposure, trade count, and holding period. Curves expose net equity, drawdown, rolling Sharpe, exposure, and turnover.

Robustness includes block-bootstrap intervals, probability of a positive mean, deflated Sharpe after a declared trial count, HAC or clustered inference as appropriate, Benjamini-Hochberg multiple-testing correction, subperiod stability, regime slices, and parameter/cost sensitivity.

## Readiness gates

Status is the most conservative failed gate:

- **Decision-ready research**: minimum sample, development threshold, positive final evidence, bootstrap and deflated-Sharpe thresholds, stability, and cost sensitivity all pass.
- **Research only**: potentially informative, but at least one promotion gate fails.
- **Not ready**: insufficient or materially unstable evidence.

The bundled snapshot currently promotes no strategy to decision-ready. BTC-USD is research-only and ETH-USD is not ready. This is expected behavior, not an error.

## Deep Research reliability protocol

Deep Research freezes source/feed, dataset hash, chronology, search space, seed, costs, folds, and final boundary before candidate generation. Workers receive development data only and execute signals on the next actionable bar. Every generated, duplicate, invalid, failed, interrupted, and successful attempt remains in the trial ledger so unsuccessful searches cannot disappear from the statistical denominator.

A research challenger passes only if it has at least 300 closed trades; positive median walk-forward return and Sharpe after costs; positive doubled-cost return; Deflated Sharpe and block-bootstrap positive-edge probabilities of at least 99%; probability of backtest overfitting no more than 10%; parameter stability of at least 80%; drawdown no more than 10%; profit concentration below 50%; a positive sealed final result; complete causal, provenance, coverage, and execution audits; and a material improvement over the existing research champion. These are rejection filters, not promises of future performance.

Continuous mode is a sequence of finite generations. Work dispatch is bounded to the chosen CPU count, numeric libraries use one thread per process, completed batches are checkpointed in ordinal order, a crash is retried once, and repeated failure is recorded. Pause stops new batches after active work drains; Stop preserves completed evidence. Resume requires the exact dataset, code, search-space, cost-policy, and protocol identity.

## Threats that remain

Public-data survivorship, regime change, unofficial price data, capacity, taxes, latency, unavailable borrow, exchange failures, spreads during stress, selection bias, and further researcher degrees of freedom can all make live results worse. Backtests are not investment advice or evidence of guaranteed profitability.
