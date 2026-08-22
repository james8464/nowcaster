# Intraday Strategy Research and Learning Design

**Status:** Approved 2026-08-22

## Purpose

Upgrade Nowcaster from a daily crypto research model into a native macOS strategy-research application that can ingest intraday stock and cryptocurrency bars, evaluate a diverse library of causal strategies, combine only the strategies with credible out-of-sample evidence, and discover new interpretable rules without contaminating the final test period.

The product is a research and paper-trading aid. It must never promise profit, imply that a high confidence number is certainty, or describe a historical backtest as live trading evidence.

## Non-negotiable causal contract

1. A decision at timestamp `t` may use only finalized observations with `available_at <= t`.
2. Indicators use trailing windows only. Centered windows, backward fill, future constituents, final-sample quantiles, and ex-post regime labels are forbidden.
3. A completed bar may create an order only for the next actionable bar after decision latency. Same-close execution is forbidden.
4. Incomplete provider bars are rejected. Corrections are append-only revisions; the normalized store resolves the revision visible at the simulated decision time.
5. When OHLC bars cannot reveal whether an entry, stop, and target occurred first, the simulator uses adverse ordering or a finer interval.
6. Every strategy, ensemble, and learning run must pass prefix invariance: appending or mutating future bars cannot change any earlier feature, signal, weight, order, or fill.
7. Stateful transforms are fitted inside each chronological training fold and updated only after their labels are observable.
8. Development, walk-forward, and final-test modes are explicit. The sealed final test is never used for parameters, calibration, strategy promotion, ensemble weights, or learner feedback.

## Data architecture

Add a provider-neutral `MarketBar` contract with UTC timestamps, interval, OHLCV, optional VWAP/trade count, provider, venue/feed, `available_at`, revision, finalized state, and payload hash.

- Crypto baseline: official Binance public klines/archive, paginated to each symbol's earliest available bar and checksum-manifested locally.
- Equities: authenticated Alpaca IEX/SIP adapter plus strict CSV import. Feed identity is part of the dataset key; IEX and SIP data are never spliced into one backtest.
- Existing daily Yahoo snapshots remain for the earnings product and legacy comparison, not for intraday-readiness claims.
- Raw observations are immutable. Normalized queries are as-of queries keyed by provider, feed, instrument, interval, open timestamp, and revision availability.
- Historical data is cached outside the repository. Git stores manifests, compact reproducible research summaries, and demo fixtures rather than redistributing licensed raw bars.

## Strategy registry

Each registered strategy declares a stable ID/version, asset compatibility, supported intervals, warm-up bars, family, parameter schema, causal signal function, and source/evidence note. All signals use `-1`, `0`, `+1`, with zero representing abstention.

Initial registry:

| Family | Strategies |
|---|---|
| Trend | EMA crossover with ADX filter; MACD histogram trend; Donchian breakout; ATR/Supertrend; VWAP trend continuation |
| Mean reversion | RSI reversal; Connors RSI; Bollinger-band reversion; VWAP z-score reversion; stochastic reversal; extreme-return reversal |
| Volatility/volume | Bollinger squeeze/Keltner breakout; volume-spike breakout; volatility-scaled trend |
| Session | Equity opening-range breakout with relative volume; ETF last-half-hour momentum; Bitcoin active-session momentum |
| Relative value | Rolling cointegration/pairs spread; liquid-crypto cross-sectional momentum when a sufficient universe is present |

Evidence quality is surfaced in metadata. Weak or venue-sensitive strategies remain research-only until they pass the same promotion gates as every other strategy.

## Backtest and execution engine

One event-driven path powers historical, paper, and future live modes. It supports market, stop, protective-stop, target, timed-exit, and session-flatten instructions.

Execution assumptions include:

- next-bar open or next observable bid/ask;
- maker/taker fees, commissions, spread, slippage, latency, and participation cap;
- crypto funding and equity borrow cost/availability where supplied;
- tick/lot rounding, sessions, halts, splits, dividends, and symbol changes;
- conservative same-bar collision ordering;
- cash, gross/net exposure, per-asset and per-strategy caps, turnover, and volatility targeting.

Every strategy is evaluated on all locally available compatible history. Reports distinguish unavailable data from failed strategies; missing equity credentials cannot be presented as a completed consolidated-market test.

## Validation and promotion

Chronological nested walk-forward validation surrounds every fixed strategy, parameter search, and learned rule. Labels overlapping validation are purged and an embargo covers the forecast horizon and known publication delay.

The final segment is chosen from the complete chronology before signal filtering. In `frozen` mode the entire pipeline is fitted through the development cutoff and never updated inside final test. In `walk_forward_learning` mode updates occur only after each outcome is observable and are reported separately from the frozen final result.

Metrics include net return, annualized return/volatility, Sharpe and Sortino ratios, maximum drawdown, Calmar ratio, hit rate, profit factor, turnover, exposure, trade count, Brier score, calibration error, and cost sensitivity. Robustness includes block-bootstrap confidence, Deflated Sharpe Ratio, Probability of Backtest Overfitting, parameter stability, regime/year/side attribution, and doubled-cost survival. The trial count is the actual append-only candidate ledger count.

Promotion requires sufficient trades and history, positive median walk-forward net edge, acceptable drawdown, stable performance across folds, non-negative doubled-cost result, acceptable overfitting diagnostics, and no causal-audit failure. Failure results in zero weight, not silent inversion.

## Ensemble

Strategy votes are volatility-normalized. Development-only evidence scores combine clipped out-of-sample Sharpe, downside risk, calibration, stability, cost survival, sample size, and multiple-testing penalties. Scores produce nonnegative shrinkage weights with maximum strategy and family caps. Equal weights are the prior; optimization must earn deviations from that prior.

For online adaptation, cost-aware specialist Fixed Share/AdaHedge updates a strategy's weight only after its return horizon resolves. Regime specialists use lagged trend, volatility, and liquidity state recorded at the original timestamp. The final signal is long, short, or abstain; it requires minimum breadth, vote margin, probability calibration, and expected net edge above cost and uncertainty buffers.

## Learning mode

Learning Mode searches an interpretable typed grammar of lagged indicators, comparisons, crossovers, and bounded Boolean combinations. It uses deterministic seeded evolutionary structure search and bounded parameter search inside inner chronological folds.

- Candidate complexity is capped by node count/depth and penalized by minimum-description-length cost.
- Duplicate semantics are removed.
- Fitness is median validation net Sharpe minus drawdown, turnover, instability, and complexity penalties.
- Every candidate and parameter query, successful or failed, is appended to the trial ledger.
- Outer test blocks are evaluated once. A result inspected or used for a decision is no longer untouched.
- Discovered rules enter a shadow/paper state. Promotion requires a new forward period and all normal evidence gates.
- Production discovery cannot continuously mutate an active rule; it produces a new versioned candidate.

## Persistence and snapshot contract

Add timestamped tables for bars, strategy definitions/runs, component signals, ensemble weights, executions, backtest metrics/curves, learning trials, discovered rules, and causal audits. Natural keys include dataset hash, strategy version, interval, symbol, run mode, and decision timestamp.

Snapshot schema v2 adds strategy summaries, current component contributions, weight history, learning runs, discovered rules, dataset coverage, and no-repaint audit results. Swift supports schema v2 and presents a clear incompatibility message for unsupported snapshots.

## Native macOS experience

Add a Strategy Lab to the existing SwiftUI navigation and design language rather than introducing a separate visual system.

- Overview: current ensemble posture, probability/margin, estimated costs, breadth, readiness, and last finalized bar.
- Strategy table: strategy, family, state, weight, walk-forward/final Sharpe, drawdown, trades, cost survival, and evidence gate.
- Detail inspector: plain-language logic, component vote, parameters, history coverage, equity/drawdown charts, assumptions, warnings, and citations.
- Learning workspace: queued/running/completed experiments, generation/candidate progress, best rule in plain English, complexity, frozen boundary, promotion state, and causal-audit badge.
- Controls create typed engine requests; they never rewrite YAML silently.
- Native tables, split views, Charts, SF Symbols, semantic colors, keyboard navigation, VoiceOver labels, reduced motion, adaptable window sizing, and system spacing follow macOS HIG conventions.

## Verification and delivery

Required gates:

- Python unit/integration suite and Ruff;
- registry-wide prefix-invariance, closed-bar, revision-vintage, frozen-holdout, delayed-weight, and same-bar collision tests;
- deterministic backtest rerun with identical dataset/config/code hashes;
- Swift tests and release build;
- snapshot v2 Python-to-Swift fixture sync check;
- native screenshot review for Strategy Lab and affected existing screens;
- beginner-friendly README with setup, data limitations, strategy explanations, learning-mode explanation, and explicit risk disclosure;
- Git history contains no raw credentials or licensed bulk datasets;
- verified push to `https://github.com/james8464/nowcaster.git` after all gates pass.

## Research foundations

- Sullivan, Timmermann & White, “Data-Snooping, Technical Trading Rule Performance, and the Bootstrap.”
- Allen & Karjalainen, “Using Genetic Algorithms to Find Technical Trading Rules.”
- Gao et al., “Market Intraday Momentum.”
- Moskowitz, Ooi & Pedersen, “Time Series Momentum.”
- Gatev, Goetzmann & Rouwenhorst, “Pairs Trading.”
- Bailey et al., “The Probability of Backtest Overfitting” and “The Deflated Sharpe Ratio.”
- Hansen, “A Test for Superior Predictive Ability.”
- Freund & Schapire, Hedge; Herbster & Warmuth, Fixed Share; van Erven et al., AdaHedge.

