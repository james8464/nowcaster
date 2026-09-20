# Research Round 2: paper-only crypto research

Research Round 2 is a separate, repeatable way to examine a small number of crypto ideas. It is designed to make failed, incomplete, and inconvenient evidence visible. It is **paper-only**: it does not connect to an account, place an order, send a notification, or create a qualified setup.

It is **not proof of profitability**. A historical simulation can be useful for rejecting weak ideas, but it cannot establish future income or tell a person what to buy or sell.

## What is included

The initial protocol is deliberately narrow: Binance public spot data for `BTCUSDT` and `ETHUSDT`, using finalized one-minute observations. Binance spot is long-or-abstain research in this round; the round cannot turn spot data into a short recommendation. A future venue with shorting would need a separate, newly registered protocol, source identity, cost model, and evidence record.

Every round has a source identity and a protocol hash. The source identity says which provider, feed, and revision supplied the observations. The protocol hash binds the symbols, strategies, costs, quality limits, and test schedule together. If any of those change, the work is a new round rather than a quiet edit to old results.

## How the evidence is protected

1. **Capture only finalized observations.** Each recorded bar carries provider, receipt, and availability times. A bar that was not available when a decision would have been made cannot support that decision.
2. **Record quality problems instead of repairing them.** Missing minutes, late data, stale prices, high spreads, provider errors, and conflicting duplicate records are retained as exclusions. The system does not fill a gap with later data. After a gap, it waits for a continuous 60-minute warm-up before considering observations again.
3. **Use fixed walk-forward windows.** A candidate first uses an earlier training window, then a later validation window, and finally one untouched sealed test window. The sealed window is used once and its receipt is retained; it cannot be reused to tune parameters.
4. **Keep every candidate.** Rejected and insufficient-data candidates stay in the report beside any experimental result. This prevents a favourable-looking result from being shown without the failed alternatives that were tested too.
5. **Model friction.** The replay declares fees, spread, slippage, latency, participation, cash exposure, and conservative stop/target handling before it evaluates a candidate. If executable evidence is unavailable, the result is no trade rather than a guessed fill.

Default quality checks require 99.5% expected one-minute coverage for each evaluated fold, observations no older than 15 seconds, observed spread at most 25 basis points, no unresolved provider error in the decision interval, and the continuous warm-up described above. These are safeguards, not a way to make a strategy profitable.

## Reading a report in the Mac app

The app can load a retained `research-round-2-summary.json` file in Strategy Lab. It shows one of three research states:

- **Insufficient data** means the evidence or quality coverage was not enough to evaluate the idea. Stand aside is the honest outcome.
- **Rejected** means the declared gates did not pass. It is not hidden or converted into a more favourable label.
- **Experimental paper-only** means the fixed gates passed for retained simulated evidence. It remains unqualified research, not a trade instruction, alert, or proof that a future result will be positive.

The summary identifies the protocol hash, reasons, and sealed metrics so that it can be checked later. The retained protocol manifest and registry identify the provider, feed, and source revision; the current bounded app summary intentionally does not repeat source identity. It accepts no account, broker, order, notification, alert, or position field. A malformed or action-shaped import is rejected by the native app.

## Provider boundary

The public Binance source is identified as public research data. It is not presented as a premium feed. The code contains a `PremiumProviderAdapter` interface solely to define what a separately configured provider would need to supply: a named identity and finalized, attributable observations. It contains no credentials, no automatic fallback, and no hidden network or account integration.

## Included fixture

`data/demo/intraday/research-fixture.json` contains legacy deterministic intraday-generator metadata only; it is not a Research Round 2 app report. Its clearly named metadata block is visibly `paper_only`, `unqualified`, and `unavailable` because it has no provider observations and no evaluated candidate. The separately bundled `research-round-2-summary.json` is a valid bounded app import fixture and records an insufficient-data, Stand aside state. Neither fixture contains a return, a signal, a target, or a claim about market performance.

## Practical limit

Research Round 2 can help someone inspect whether an idea was tested causally and whether its evidence is complete enough to study further. It cannot remove market risk, predict the next move, or make day trading reliable. If quality is incomplete, the correct output is abstention.
