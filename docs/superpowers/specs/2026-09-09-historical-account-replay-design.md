# Historical account replay: fixed-rule diagnostic

## Purpose and authority

Replay historical candles one at a time to measure how the two retained Nowcaster rules manage money over time. This is a new, separately recorded **retrospective account-model evaluation**, not another parameter search or an independent holdout. The user has authorized autonomous implementation and upload, including choosing reasonable implementation details without further approval questions.

The prior 24-configuration search and its failures remain intact. All inspected historical data is development data. Positive replay results cannot override the failed historical screens or authorize alerts, account access or orders.

## Immutable boundaries

- Do not modify `.worktrees/live-paper-study`, its environment, the live collector, or anything in `ProspectiveStudies`.
- No installs, credentials, orders, power-setting changes or OS daemons. Use the existing Python environment read-only, with bytecode disabled.
- No strategy/parameter selection, fitting or tuning in this round. Use the two candidates retained in `data/research/holding-period-search-2026-09-08.json` unchanged.
- Original discovery SHA-256: `58c25ec6e836ecd7b17963a03ef6e9f3e43a03b78666c534506b874699a5f903`.
- Exact requested archive window: `2017-08-17T00:00:00Z` through exclusive `2026-09-08T00:00:00Z`; actual first observation is 04:00 UTC on 17 August. Two spot assets, BTCUSDT and ETHUSDT, hourly bars, 130 files and 79,270 valid rows per asset. Restore only the 260 checksum-pinned public archives; no substitute data on failure.
- Historical candle availability is modeled at candle close. Archives can be revised and are not point-in-time vintages. No historical quote/queue/latency/partial-fill fidelity claim.

## Chosen design

Use a new isolated historical replay engine. Reusing the prospective collector would violate its fixed 90-day contract and quote requirements; reusing only average trade returns would not evaluate a managed account. Reuse the configured strategy generators and verified archive parser, not a new indicator formula.

The engine consumes one finalized hourly bar at a time. Each bar has two logical phases: opening price becomes available for an already pending entry, then the completed high/low/close becomes available for exit resolution, valuation and the next decision. A decision never receives the next bar. Equal boundary timestamps are ordered by these phases: previous close/decision before next open. This is a next-bar-open approximation, not simulated 250ms market execution.

At each completed bar the strategy sees only the most recent 1,000 observed hourly bars, using existing `eligible_long_signals` and `gap_safe_atr`, matching the live calculation window. Existing gap segmentation resets indicator state. Freeze original reference close, ATR, eligibility, reason, stop/target distances, expiry and input-window fingerprint before later bars arrive. Do not precompute decisions on a full future frame. The two execution cost scenarios share this same decision stream.

### Account and execution model

Each asset/scenario starts with an independent 10,000 USDT account; accounts are never summed. Long-only, one position or pending entry per account. Risk budget is 25 USDT per entry; maximum entry cost is 25% of current cash. Lot increments are explicit modeling assumptions: BTC 0.00001 and ETH 0.0001, with minimum entry notional 5 USDT. No historical exchange-rule fidelity claim. No borrowing, funding, taxes or volume-based fill capacity is modeled.

Base per-side costs: fee10bps, half-spread2bps, slippage5bps. Stress doubles all three. Buy price = open*(1+spread)*(1+slippage); sell price = reference*(1-spread)*(1-slippage); fees charged on modeled fill notional. Quantity = lot-floor(min(cash*.25 / entry_unit_cost, 25 / loss_per_unit_at_modeled_stop)). Levels anchor to modeled entry with predecision ATR distances, and expiry remains original signal bar close + candidate maximum hours. The 68bps target floor is checked both on the predecision signal and modeled entry; later ineligible entry is rejected, not backfilled. Full fills are an explicit assumption; never use completed-bar volume to decide an opening fill.

After entry, completed-bar low/high resolve barriers. For long positions stop wins when both levels are touched; a gap-through stop uses the worse opening price. Expiry wins over a target in the expiry bar, after adverse stop checking. Record the exit bar interval and recognize its outcome at bar close; do not invent the intrabar trigger timestamp. A target-only opening gap uses the target price (no favorable price improvement). Record ambiguous-bar counts.

A missing interval cancels pending entry, taints and closes any open position at the first subsequently observed opening price, with modeled costs and reason `gap_liquidation`. This is a declared first-observation approximation, not a fill inside the missing interval. Preserve the gap and its P&L. Do not delete gap-crossing losses. At dataset end retain any open position and pending decision; report marked liquidation value separately from realized P&L, with no synthetic terminal trade. A newly appended suffix must not rewrite earlier decisions, entries, closed trades or valuations.

### Evidence and reports

Emit hash-chained events for observed opening/closing data, decisions (including abstentions/rejections), entries, exits, cancellations, gaps and valuations. An entry/open event hash must exclude that bar's not-yet-known high/low/close/volume. Decision input fingerprints bind only the already revealed context (bar hashes plus window bounds), alongside candidate definition and original decision values. Raw pinned archives plus the event log allow reconstruction. Hashes establish self-consistency, not independent attestation.

Reports show base/stress scenarios per asset: cash, marked equity, realized and marked P&L, completed trades/wins/losses, explicit costs, observed-close maximum drawdown, gap-tainted/ambiguous exits, retained open/pending exposure, and calendar-year and month equity changes. Partial years are labeled. No confidence score, probability of future profit, promotion or fresh-holdout label. Drawdown is on observed hourly closes, not every tick. A method/results Markdown report plus machine-readable evidence is the primary artifact (not a website or notebook).

Period changes use interval-ending timestamps: a bar closing exactly at midnight belongs to the hour/month/year just ended. Carry the last mark into the next period's starting balance; never reset account cash at a year boundary. Separate calendar-window completeness from missing-bar coverage. This doubled-cost scenario is not the prospective study's flat extra34bps stress deduction; the two methods must not be mislabeled as identical.

Before evaluation, create a new campaign record and protocol manifest in a separate `HistoricalReplays` collection, binding hypothesis, prior discovery and 24-trial selection history, candidates, source/runtime identity, archive identities and execution assumptions. Every attempt, error and partial event log survives. Refuse to reuse or overwrite an output directory. Do not alter the prospective campaign registry. Validate source identity again after evaluation. JSON results and Markdown are published only after complete success; failures retain a clearly failed status, not a partial success summary.

Archive restoration may use up to four concurrent HTTP downloads for the nine-megabyte pinned collection; replay calculation remains sequential by default. An explicit `--workers 2` may run the two independent assets in separate processes, with separate event files and stable result ordering; never split a single account's chronology. Cap calculation workers at two and reserve at least two logical processors (fall back to one when hardware cannot support two). Record requested/effective workers in the protocol. Cache paths and protocol/output paths are separate. After complete archive preflight, the evaluation loader forbids network access and therefore cannot silently substitute new data midway through a run.

### Acceptance

Hand-calculated entry/stop/target/expiry/gap/accounting tests; no future-price sizing; no opening fill from future volume; delayed/duplicate/revised bars rejected; future append/perturbation leaves prior evidence unchanged; real configured strategy prefix equivalence; no terminal forced sale; base/stress costs and changed cash affect sizing correctly; malformed/checksum-changed/missing archives fail closed; interrupted/refused repeated runs remain retained; source and output protections hold. Run the exact historical evaluation after review, then document its actual results, whatever their sign. Keep the live study running unchanged.

Sources: [Binance public archives and revision notices](https://github.com/binance/binance-public-data), [data leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).
