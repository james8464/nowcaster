# Prospective live-data paper study

## Purpose and boundary

Improve the evidence, not the appearance of profitability. Historical screening found no qualified strategy. Add a bounded lower-turnover search and a separate prospective paper experiment. Neither can unlock real-money trading or qualified alerts. There are no broker order calls, credentials, account changes, or user notifications from experimental signals.

All previously inspected history is development data, including the old audit's holdout. Never reuse it as an untouched test. Public Binance BTCUSDT and ETHUSDT spot, long/cash only, are this round's universe. Equity, derivatives, leverage and short execution remain out of scope.

## Development search

Predeclare exactly 24 configurations: two assets; squeeze_breakout, macd_trend and volatility_scaled_trend (use actual registry IDs); six/twelve-hour expiry; one/two ATR stop; target 1.5 times stop distance. Use finalized hourly candles, causal existing signals/ATR, next-continuous-bar entry, stop-first ambiguous candles and expiry-before-target. Require target distance at least 68 bps when generating an eligible signal. Model 34 bps round-trip costs and 68 bps stress costs. Divide the available verified history into four chronological development folds; no fold is called independent validation.

Rank by worst-fold mean stressed return, then full-history stressed mean, then deterministic identity. Record every configuration, trade count, exclusions and losses. Select at most one candidate per asset for **experimental observation**, even if all are negative; record whether it survived the screen (at least 30 scorable trades and positive stressed mean in every fold). A failed screen is permanently visible and cannot earn a positive study verdict. Do not expand the search after seeing its results.

## Immutable live experiment

Register a manifest before prospective observation. Freeze schema, source hash (including study scripts), discovery report hash, ordered candidate definitions, timestamps, execution assumptions, independent account sizing and verdict thresholds. End date is start plus 90 days. Record a monotonically numbered campaign in a separate append-only registry; never erase failed studies or restart evidence from zero after a loss. A changed source/configuration requires a new study and an explicit retained link to its predecessor.

Each candidate has its own $10,000 simulated account, maximum 25% notional exposure, maximum 0.25% initial account risk per entry, one position at a time, no borrowing, and never negative cash. These accounts are independent comparisons, not a combined portfolio. Execute hypothetical buys at observed ask plus 5 bps slippage, sells at observed bid minus 5 bps, and charge 10 bps per side. These fees are model assumptions, not the user's fee tier. Require at least 250 ms after the decision, quote age no more than two seconds, spread no more than 10 bps for entry, valid lot/minimum-notional metadata and enough displayed size. No fill is inferred from a candle, seed, repaired history, stale quote or missing observation. Partial exits may use successive fresh quotes; a stop/expiry trigger latches until fully closed. Pending entries expire after 30 seconds. Stress deducts an additional modeled round-trip friction charge; label the method precisely.

Freeze entry, SL, TP and expiry before entering. Use fresh bid quotes to close positions; do not backdate or rewrite decisions. Gaps over 30 seconds cancel pending entries and taint open positions, preserving their eventual P&L. Resume by recording gaps, not inventing intervening fills. Store durable state transactionally and append an integrity-hashed journal containing manifests, decisions, fills, minute valuations and gaps. Identical repeats are idempotent; conflicting event identities, changed manifests, backward clocks and competing writers fail closed. Retain filled and unfilled signals. Warm-up candles only provide context and never earn prospective evidence.

## Assessment

Publish an atomic, beginner-readable summary at least every minute: paper-only flag, study/candidate identities, fixed window, quote/decision/fill counts, net/stressed return, costs, open exposure, drawdown, cash and buy-and-hold comparisons, coverage/outages, historical screen result and limitations. Never sum independent candidate accounts. Pending/open positions and stale valuations cannot be treated as successful closed trades.

No positive verdict before the fixed end date, 100 closed trades per candidate, 99% observable minute coverage, no tainted trades, positive total net/stressed P&L and maximum drawdown below 10%. A dependence-aware daily-return block bootstrap may supply a conservative screening lower bound, adjusted for all campaigns/candidates; describe it as approximate, not a calibrated probability of profit. Too few observations or inadequate bootstrap resolution means insufficient evidence. Even a passing verdict means only `positive_paper_evidence`, never guaranteed profit or real-money readiness. Keep an ordinary dated report if no candidate passes.

## Operation

Run on public live feeds without keys. A resumable local command owns the ledger; a Codex heartbeat checks progress and resumes collection after interruption. The Mac must be awake and online. Do not install OS daemons or change power settings. Long-running collection is separate from app alert qualification. Show how to inspect its summary using the existing app's local workspace/research workflow; a native summary view is optional if it can be added without widening the evidence boundary.

## Evidence sources

- [Alpaca paper trading limitations](https://docs.alpaca.markets/us/docs/paper-trading): simulated fills are not real execution evidence.
- [Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf): searching many rules creates selection bias.
- [Binance public data](https://github.com/binance/binance-public-data): historical archive provenance.
- [Binance fees](https://www.binance.com/en/fee/trading): account fees vary; this study freezes explicit assumptions.
