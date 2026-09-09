# Following a paper trade from decision to result

The collector already closes positions automatically when the frozen stop, target or time limit triggers and an eligible quote supplies enough displayed size. A trade may take several fills to close. The audit is a separate **read-only observer**: it explains that lifecycle without forcing a sale, changing the strategy, rewriting old decisions or taking ownership of the collector's database.

## Why the first BTC trade was still open

At the 9 September 2026 06:56 UTC inspection, the retained trade had:

- Decision: 01:00:00.043153 UTC; entry fill: 01:00:01.019650 UTC.
- Original time limit: **13:00 UTC on 9 September (15:00 Paris)**.
- Frozen stop-trigger bid: 78,124.4577538147462 USDT/BTC.
- Frozen target-trigger bid: 79,848.43169427788070 USDT/BTC.
- No latched exit trigger and no completed round-trip yet.

It was not overdue. Decisions at 02:00, 03:00 and 04:00 were rejected because the account already held a position. Their proposed time limits did **not** replace the original trade's 13:00 deadline.

The BTC rule compares the price with 20 hours earlier and scales that move by trailing hourly volatility; positive momentum can produce a long signal, subject to the frozen target-distance floor. ETH's rule looks for a prior Bollinger/Keltner squeeze followed by a completed-hour breakout. These are fixed diagnostic rules, not a live model assigning a verified probability of profit. The frozen ledger records reference price and ATR but not the full calculation window, so today's downloaded candles must not be passed off as the exact inputs seen then.

A later read-only query found 317 retained minute valuations from 01:01 to 06:58 UTC, with sampled bids between 78,548.46 and 79,317.64. Those samples were inside the frozen exit levels. They do not cover every tick or the missing-feed intervals, so they cannot prove that the market never crossed an exit level. That limitation remains attached to the trade.

An expiry is an instruction to attempt an exit on an eligible observed quote, not a promise of a fill at exactly that second. Stale data, insufficient displayed size and minimum trade size can delay completion. The auditor distinguishes waiting for the original plan from waiting for an eligible exit quote. The latter deserves inspection, not an invented retrospective fill.

## Run an audit without changing the study

Run the auditor from the updated application checkout, **not** by upgrading the frozen collector:

```sh
.venv/bin/python scripts/audit_prospective_trades.py \
  --directory '/Users/james/Library/Application Support/Nowcaster/ProspectiveStudies/study-001-20260908' \
  --output-dir '/Users/james/Library/Application Support/Nowcaster/ProspectiveStudyAudits/study-001-20260908'
```

Omit `--output-dir` for JSON on standard output. `--now` accepts an explicit UTC timestamp for reproducible inspection; it does not change the experiment clock or reconstruct a historical snapshot from a newer ledger. Reports are derived artifacts outside the original study. Existing reports and the source database are retained.

The existing study monitor runs an audit for a new completed trade or a changed exit problem, retaining the original signal IDs. It should not restart a healthy collector, repeat the completed historical search, or repeatedly announce an unchanged open position. The Mac must remain awake and connected; no power settings or operating-system daemons are changed.

## What the audit checks

The audit reads one consistent SQLite snapshot in read-only mode. It checks journal sequence and content hashes, the state/head link and manifest binding before interpreting results. These checks detect inconsistency, not malicious edits followed by recomputing every hash.

For each actual trade it matches the original decision to its entry and all exits. It checks quantities, frozen levels and expiry, entry timing, and recorded versus independently reconstructed after-cost results. Entry spread and size must match the frozen limits, including the cash available after earlier trades and the market's quantity increments. End-of-study accounting must include every candidate account. Fees and modeled slippage are disclosed without being subtracted twice. Partial exits, open positions, rejected decisions and post-study-window liquidation remain distinct. Losing and gap-tainted trades are kept; neither can disappear from the scorecard.

The first live audit at **07:12:39 UTC on 9 September** checked 1,628 journal events and their links to the saved state and indexes. It found the same open BTC trade, three rejected overlapping decisions, no completed trade and no overdue exit. Both latest quotes were less than half a second old at inspection. The BTC trade's data-gap warning remained attached. This verifies retained-record consistency at that snapshot; it does not fill missing observations or create a realized profit.

For a completed long trade:

```text
net result = sum(exit quantity × exit fill price − exit fee)
             − (entry quantity × entry fill price + entry fee)
stressed result = net result − original entry notional × 0.0034
```

Fill prices already include the frozen slippage model. Open-position mark-to-market is not a realized result. A completed profitable paper trade is one observation, not a probability that the next trade will win.

## What “no repainting” means here

An original decision, entry plan and completed trade result must not change just because later observations arrive. The auditor checks those retained records and refuses inconsistent accounting or changed plans. It does not recompute earlier decisions using today's revised candles.

The frozen ledger does **not** retain every quote or every indicator input used at decision time. Consequently, this audit cannot certify every unrecorded market-path crossing or fully reproduce historical indicator calculations. Separate synthetic prefix-invariance and data-maturity tests exercise the strategy code, but passing finite tests is not a mathematical proof for every possible dataset.

The 9 September scoped causality investigation ran 26 targeted regressions: future-data append checks across configured strategies, a historical-revision case, the configured holding-period rules, and complete/fresh live-hour acceptance and rejection cases. They passed. The historical archive is not a contemporaneous revision history: algorithmic no-repaint behavior on supplied candles must not be described as proof that those candles exactly match every value published in the past.

The current research calibration measures positive strategy returns, not target-before-stop probability. It remains ineligible for qualified target-before-stop alerts. The auditor adds no confidence score and grants no promotion. Future research must retain this study and use a separately recorded hypothesis and selection round; it cannot tune this ongoing evaluation until its balance looks better.

Historical registration also requires a correctly formed `signal_prefix_hash` for every tested trial. This is a fingerprint of raw signal outputs at one 75%-of-bars cutoff—not a fingerprint of all inputs, all eligible trades, or an independently verified no-repaint certificate. Requiring the field prevents incomplete records from being registered; it does not replace an actual before/after prefix comparison. Existing registered evidence is not rewritten or recalculated.

This separation follows the principle that evaluation data must not influence model fitting or selection; see [scikit-learn's data-leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage). Binance also documents ticker and trade streams as different data products; the study's ticker feed is not a complete execution tape ([official stream reference](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/ws-streams/~)).

## Reproduce the initial timing check

Use SQLite read-only mode and a read transaction against the retained `ledger.sqlite`:

```sql
BEGIN;
SELECT seq, at, payload FROM journal
WHERE kind IN ('decision', 'fills', 'closed_trade') ORDER BY seq;
SELECT json_extract(payload, '$.accounts') FROM state WHERE singleton = 1;
ROLLBACK;
```

Compare the accepted decision and entry's original expiry with the current position. Do not treat a rejected decision's proposed expiry as an edit to the held trade.
