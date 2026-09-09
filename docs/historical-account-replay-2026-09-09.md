# Historical account replay — 9 September 2026

## What this test asks

If Nowcaster had received the selected historical prices one hour at a time, how would its two retained Bitcoin and Ether rules have managed a simulated account? This test follows complete paper trades and the remaining balance through time. It does not simply add up the percentage changes from individual signals.

The replay is a separate research tool in the project, not a new button in the native app and not a connection to a brokerage account. It places no orders. The existing live paper study continues under its original rules and dates.

## Evaluation status

The first actual attempt failed safely on 9 September at 09:54 UTC when it reached archived bars that did not start on the UTC hourly clock. Its source and partial trading records are retained; it has no completed performance result. A separately recorded amended attempt is being prepared. Its results will appear here only after completion and independent reconciliation.

The archive parser had accepted 42 one-hour bars per asset with shifted timestamps during a February 2018 outage. The replay engine correctly refused them under its stricter clock rule. The correction does not round their timestamps or relax the engine. It preserves those rows in a quarantine record and treats the interval as missing execution data. This choice is based on timestamp quality, not on whether those trades would win or lose.

## What is fixed before seeing the results

- Two assets: Bitcoin (`BTCUSDT`) and Ether (`ETHUSDT`), on Binance spot. These are independent accounts, not a combined portfolio.
- Two unchanged rules from the [8 September study](holding-period-search-2026-09-08.md): volatility-scaled trend for Bitcoin and Bollinger/Keltner squeeze for Ether. Both are long-only: they can buy and later sell, not borrow an asset to sell it short.
- Each account starts with 10,000 simulated USDT. A normal entry risks at most 25 USDT at the modeled stop and spends at most a quarter of available cash. Gaps can make the actual modeled loss larger than that budget.
- The original data selection is fixed: 260 checksum-pinned public archives, containing 79,270 parser-valid hourly intervals per asset. The amended selection retains 79,228 clock-aligned bars per asset and quarantines 42 shifted intervals. Another 15 invalid-duration rows per asset were already excluded by the original parser; they remain in the raw archives. The requested window is 17 August 2017 through 7 September 2026 inclusive; the first available hour opens at 04:00 UTC on 17 August 2017.
- Base costs are a 0.10% fee, 0.02% half-spread and 0.05% slippage on each purchase or sale. A second account doubles all three costs. This is different from the live study's flat extra-cost calculation.

The earlier search examined 24 configurations, and all lost on average after its modeled costs. Those failures remain in the record. These two rules were retained for diagnosis, not approved for real trading. Replaying already-inspected history does not turn it into an independent test.

## How future prices are kept out of earlier decisions

An hourly bar contains the opening, highest, lowest and closing price for that hour. At the modeled opening, only the opening price is revealed. It may fill an entry that was decided at the previous close. The new hour's high, low, close and trading volume cannot be used to size that opening trade.

At the completed hour, the program resolves any existing stop, target or time limit, values the account, and calculates the next decision from at most the latest 1,000 already-observed bars. It records the original inputs, reason, price levels and expiry. Adding later prices must not rewrite that record. Tests compare earlier event records after future data is appended or changed.

This prevents look-ahead in this replay's calculation. It does not establish that today's downloadable archives are the exact versions a trader saw years ago. Binance can revise historical files; checksums pin the downloaded version, not a contemporaneous market-data tape. See [Binance's archive documentation](https://github.com/binance/binance-public-data).

## Deliberately cautious trade handling

A stop is the price intended to limit a loss; a target is the intended profit-taking price. Hourly bars cannot reveal which was reached first when both lie inside the same hour. This replay counts the stop first and records the ambiguity. A price opening below the stop gets the worse opening price. It does not grant a better target fill just because an hour opened above the target.

A missing hour is retained as a data gap. Pending entries are cancelled; a position spanning the gap is closed at the first subsequently observed opening, with costs and a gap warning. That is an explicit approximation, not a claim that a fill occurred inside the missing period. Losses are not removed.

Positions still open when the dataset ends remain open in the evidence. Their estimated sale value, after modeled exit costs, contributes to **marked equity**: cash plus what the remaining holding might realize under this model. It is reported separately from profit on completed trades. No final winning or losing sale is invented to tidy the results.

## Reading the report

For each asset and cost scenario, the output includes cash, marked equity, profit or loss on closed trades, all modeled costs, completed trade counts, wins and losses, gap-related exits, ambiguous exits, and remaining positions or pending entries. **Maximum drawdown** is the largest fall from an earlier account peak, measured at observed hourly closes; between-hour losses could be larger.

Monthly and yearly changes carry the actual balance forward rather than starting a new account each January. An hour closing exactly at midnight belongs to the month or year just ended. The partial 2017 and 2026 calendar years are labeled, and missing-hour coverage is reported separately from calendar completeness. USDT is the accounting unit, not a promise of an equivalent cash amount in a bank account.

## Evidence and limitations

Each attempt gets a new directory and an entry in a separate, hash-chained campaign record before outcomes are evaluated. Its protocol binds the source code, runtime, selected rules, original discovery file, exact archives and execution assumptions. The runner refuses to overwrite an earlier attempt. Failed and interrupted attempts retain their status and partial event files; only a completed run publishes successful results. The amended hypothesis explicitly records the first failure and the changed input-quality rule. It is not presented as an untouched continuation of the first attempt.

Before account calculations, a separate `data-quality.json` retains the original quarantined rows and fingerprints, and distinguishes raw input, excluded invalid-duration rows and executable bars. Its hash is bound into the run's status and result. The amended data have 188 missing hours per asset in the requested window: four leading hours and 184 internal hours across 31 gaps. Those exclusions and gaps must accompany any performance numbers.

After archive verification, evaluation runs without network access. Two optional workers can process the independent assets, but neither can split or reorder an asset's timeline. Every decision, entry, exit and valuation is journaled. These hashes help detect inconsistent evidence; they are not independent third-party attestation.

This test assumes full fills and specified costs. It does not recreate historical order books, queue position, latency, partial fills, changing exchange size rules, taxes or outages that are absent from the source. It evaluates these two rules on these two assets, not every strategy in the app. A favorable result would describe this simulation—not demonstrate an executable, reliably profitable live strategy or unlock alerts.

## Running a separately recorded reproduction

Use the project's installed Python environment, with a new output directory outside the source checkout, archive cache and live study. Replace these example paths with actual locations:

```sh
/path/to/nowcaster/.venv/bin/python -u -m scripts.run_historical_replay \
  --root /path/to/nowcaster \
  --cache-dir /path/to/HistoricalReplayArchives \
  --output-dir /path/to/HistoricalReplays/new-round \
  --allow-download --workers 2
```

`--allow-download` permits restoring only the exact original public archive files. Omit it for a wholly offline run with an already complete cache. A changed or missing required archive causes failure, not substitution. Once verification is finished, the evaluation loader forbids network requests. The default is one calculation worker; two workers can handle the independent assets when the computer has sufficient processors.

Read `status.json` first: it must say `succeeded` before interpreting `result.json` or `report.md` as a completed run. A failure can leave partial files, which are deliberately retained. The separate `campaigns.jsonl`, `protocol.json`, original `discovery.json`, `data-quality.json`, status journal and per-asset `events.jsonl` preserve the attempt's history. Running the same experiment again records another attempt; it does not create another independent piece of market evidence.
