# Intraday strategy methodology

## What this research can and cannot say

Nowcaster asks whether a fixed rule produced reproducible historical evidence after a declared validation and execution protocol. A paper or a backtest can motivate further testing; neither proves that this implementation will profit. A missing dataset is `unavailable`, not a zero-return success. All outputs are research/paper-trading aids, not orders or investment advice.

The network-free CI fixture tests causality, accounting, serialization, and determinism. Its generated prices are not live or historical market evidence. Provider-backed results are published only for scopes whose full requested history passes the quality gates.

## Point-in-time clock and non-repainting rule

For a decision at UTC time `D`:

1. A source revision may be read only when it is finalized, its bar has closed, and `available_at <= D`.
2. The first observable finalized revision forms the causal history. Later revisions remain in the ledger but cannot repaint an earlier decision.
3. The strategy records `data_through`, `decision_timestamp`, and a content-derived decision hash.
4. A signal from bar `t` is eligible only on a later actionable bar after the declared latency. The current CI policy uses 250 ms latency, so a close-time decision still fills no earlier than the next bar.
5. Prefix invariance compares signals on a history prefix with the same rows in an extended history. A mismatch fails the causal audit.

This is stronger than merely applying `shift(1)`: source availability, revisions, decision provenance, and execution provenance are checked separately.

## Validation and learning isolation

Chronology is selected before filtering. The last 20% is sealed as the final test. Development uses expanding chronological folds; training labels must be observable by the fold cutoff, with purge/embargo rules applied. Preprocessing, calibration, thresholding, strategy weights, adaptive updates, rule search, and promotion decisions cannot read the final test.

The learning benchmark searches a bounded interpretable grammar inside development data. Every attempted candidate receives a stable hash and a ledger row, including errors and rejections. Inspecting a learned rule's forward outer block consumes that evidence once; it cannot be reused for promotion.

Promotion requires usable observations and trades, positive/stable development evidence, drawdown and doubled-cost survival, Deflated Sharpe evidence, and a passed causal audit. The [Deflated Sharpe Ratio paper](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) addresses selection bias and non-normal returns; [Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) motivates keeping selection separate from final assessment. These tests reduce risk; they do not eliminate it.

## Execution and portfolio assumptions

The committed CI research policy is deliberately explicit:

| Assumption | CI value | Meaning |
|---|---:|---|
| Taker fee | 10 bps | Charged on traded notional |
| Half-spread | 2 bps | Adverse move from reference to executable quote |
| Slippage | 5 bps | Additional adverse fill movement |
| Latency | 250 ms | Added before the next bar can act |
| Participation | 5% of bar volume | Prevents unlimited fills |
| Funding | 0 bps/bar | Binance spot has no perpetual-futures funding in this profile |
| Short borrow | unavailable | New spot shorts are rejected; a funding/borrow value is never silently invented |

Prices round to tick size and quantities to lot size. Zero-volume/halted bars do not fill. When a stop and target are both touched inside one OHLC bar, the adverse stop is assumed first. Fees, commission, spread, slippage, funding, and borrow remain distinct ledger fields. Real fees, spreads, latency, market impact, borrow, outages, taxes, and capacity can all be worse.

## Ensemble policy

Only strategies with eligible evidence can receive positive weight. Scores are shrunk 50% toward equal weight, all weights are nonnegative, each strategy is capped at 25%, and each configured family is capped at 50%. Failed and unavailable strategies may remain visible with zero weight for auditability, but the compact component list excludes them. Online updates use only outcomes whose `outcome_available_at` has passed and preserve the sealed/offline evidence boundary.

## Contextual allocation and asset selection

This is an additional research layer, not a replacement for the sealed live ensemble above. Its identity includes source dataset, protocol, provider/feed, venue/product, asset class/profile, symbol, interval, direction and mode. Database schema 14 stores immutable eligibility, outcomes, posteriors, estimates, covariance, allocations, portfolio decisions, drift and search attempts. It preserves existing ledgers while widening exchange sequence storage to 64 bits. Snapshot schema 5 remains backward compatible through optional fields.

- **Executability:** profile-specific history, session, freshness, volatility, coverage and liquidity gates precede selection. An authenticated full order-book snapshot must agree with the quote, and exchange lot/notional rules must admit the configured hypothetical probe (default 1,000 quote-currency units). Both book sides must support the probe. Depth deltas are not full books; missing evidence blocks eligibility. Rules follow the exchange's [published filters](https://developers.binance.com/en/docs/products/spot/filters).
- **Regimes:** causal trailing features and chronological fits produce normalized probabilities across four fixed market states. Uncertainty is retained instead of selecting a retrospectively convenient state.
- **Specialization:** global → asset class → profile → asset → asset/regime estimates use effective samples and partial pooling. Sparse cells revert toward their parent. Long and short evidence stays separate.
- **Dependence:** synchronized returns feed a regularized covariance estimate, with constrained nonnegative weights, family caps, cash and turnover penalties. The covariance estimator is [Ledoit–Wolf shrinkage](https://scikit-learn.org/stable/modules/generated/sklearn.covariance.LedoitWolf.html). Portfolio correlation, exposure and conservative sizing constraints can exclude a second otherwise-eligible asset.
- **Causality:** full published outcome indexes are authenticated before slicing a historical prefix. Normal assessment cannot use a cohort published after its decision. Prior weights must predate that decision and their SQL timestamps must match the immutable payload. Same-time outcomes/drift are allowed only when actually available; same-time self-generated weight refreshes are not prior evidence.
- **Reproducibility:** contextual outcome protocol v2 binds runtime source content and research configuration into both outcome and cohort-cache identities. Moving a database, editing documentation or changing a Git receipt does not change the research. Actual code, strategy and cost-policy changes invalidate its cache. Operational trading identities still retain their database scope.
- **Learning:** every attempted policy is reserved in the global ledger before scoring. Chronological folds exclude the reserved tail and unresolved labels. Concurrent assets are aggregated for temporal sample statistics. A shared cash account reserves capital until an actual execution resolves, prevents overlapping positions per asset and charges extra allocation costs. Only observed holding horizons are searchable. The score observes closing valuations, not intratrade maximum loss, and cannot authorize promotion by itself.
- **Live boundary:** contextual evidence is an additional veto on an unchanged, qualified sealed ensemble. Exact members, versions, source cohort, source outcome index, mode and protocol are required. The monitor reloads contextual evidence at every decision, checks its bounded expiry and preserves confirmed drift quarantine across ordinary research refreshes.

The contextual replay is a fixed-policy chronological refit on previously published development outcomes. It evaluates every available decision after a 40-timestamp warm-up; a minimum of 60 timestamps is required. Its last 20% is reported separately as a **retrospective holdout**, not an independent sealed final test. This distinction prevents reused development evidence from being presented as new validation. The [Deflated Sharpe research](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) explains why the number of attempted strategies matters; no amount of search alone proves an edge.

## Implemented catalog

No idea is listed here unless it has a static, tested implementation in `src/strategies/library.py`.

| Family | Strategy | Enabled intervals | Foundation and availability |
|---|---|---|---|
| Trend | `ema_adx_trend` | 5m, 15m, 1h | Technical heuristic; EMA direction gated by trailing ADX |
| Trend | `macd_histogram_trend` | 5m, 15m, 1h | Technical heuristic; trailing MACD histogram sign |
| Trend | `donchian_breakout` | 15m, 1h | Technical heuristic; prior-range breakout |
| Trend | `supertrend` | 15m, 1h | Technical heuristic; trailing ATR bands |
| Trend | `vwap_trend_continuation` | 5m, 15m | Session-VWAP continuation |
| Mean reversion | `rsi_reversal` | 5m, 15m, 1h | Trailing Wilder-RSI extreme reversal |
| Mean reversion | `connors_rsi` | 5m, 15m | Price/streak RSI and percentile-rank composite |
| Mean reversion | `bollinger_reversion` | 5m, 15m, 1h | Trailing population-band extreme reversal |
| Mean reversion | `vwap_zscore_reversion` | 5m, 15m | Trailing price-to-session-VWAP z-score |
| Mean reversion | `stochastic_reversal` | 5m, 15m | Trailing stochastic extreme reversal |
| Mean reversion | `extreme_return_reversal` | 5m, 15m, 1h | Trailing return-shock z-score |
| Volatility/volume | `bollinger_keltner_squeeze` | 5m, 15m, 1h | Trailing volatility-contraction breakout |
| Volatility/volume | `volume_spike_breakout` | 5m, 15m | Prior-range break with trailing relative volume |
| Volatility/volume | `volatility_scaled_trend` | 15m, 1h | Time-series trend normalized by trailing volatility; motivated by the original [time-series momentum study](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2089463) |
| Session | `opening_range_breakout` | 5m | Equity-session rule; unavailable for the configured crypto assets |
| Session | `etf_last_half_hour_momentum` | 5m | ETF/equity-session rule; unavailable for the configured crypto assets |
| Session | `bitcoin_active_session_momentum` | 15m, 1h | Fixed UTC active-window Bitcoin heuristic; not run on Ether |
| Relative value | `rolling_cointegration_pairs` | 1h | Rolling pair residual; motivated by the original [NBER pairs-trading paper](https://www.nber.org/papers/w7032); unavailable until authenticated point-in-time paired context is supplied |
| Relative value | `crypto_cross_sectional_momentum` | 1h, 4h | Requires five point-in-time liquid assets; unavailable with the configured two-asset universe |

The cited studies motivate hypotheses and validation. They do not authenticate this code, dataset, cost model, or expected profitability.

## Paper-trading workflow

1. Complete provider history and pass every data-quality check.
2. Freeze code, configuration, dataset hash, cost policy, and cutoff.
3. Run development and the sealed final test once; publish failures and unavailable scopes.
4. Export snapshot v5 and inspect warnings, rejected gates, gaps, and zero-weight components.
5. Run a forward paper account without changing the rule. Record latency, rejected orders, fees, spread, impact, funding/borrow, outages, and data revisions.
6. Compare forward behavior with the historical assumptions before considering any new research cycle.

Day trading can produce rapid and substantial losses; read the SEC's [Investor.gov day-trading risk explanation](https://www.investor.gov/introduction-investing/investing-basics/glossary/day-trading).
