# Historical account replay — 9 September 2026

## What this test asks

If Nowcaster had received the selected historical prices one hour at a time, how would its two retained Bitcoin and Ether rules have managed a simulated account? This test follows complete paper trades and the remaining balance through time. It does not simply add up the percentage changes from individual signals.

The replay is a separate research tool in the project, not a new button in the native app and not a connection to a brokerage account. It places no orders. The existing live paper study continues under its original rules and dates.

## Completed account results

**Both retained rules lost money after modeled costs.** The amended replay completed on 9 September 2026 at 11:27 UTC, covering 17 August 2017 through 7 September 2026. Each row below is a separate account starting with 10,000 simulated USDT—not a combined portfolio.

| Rule and costs | Ending balance (USDT) | Net return | Closed trades | Maximum observed-close drawdown |
|---|---:|---:|---:|---:|
| Bitcoin, ordinary costs | 384.64 | −96.15% | 5,715 | 96.37% |
| Bitcoin, doubled costs | 20.01 | −99.80% | 4,710 | 99.80% |
| Ether, ordinary costs | 4,867.29 | −51.33% | 1,190 | 52.42% |
| Ether, doubled costs | 1,868.69 | −81.31% | 1,197 | 81.45% |

All four accounts ended with no open position or pending entry, so these are completed-trade outcomes rather than gains on an unsold holding. The losing trades and damaged balances remain in the evidence. These results are evidence against using these particular rules under this execution model; they do not justify real-money signals, a confidence upgrade or selecting Ether simply because it lost less.

### How the results changed over time

Each year's return uses the balance carried from the previous year. The account is never reset to 10,000 USDT.

| Calendar period | Bitcoin ordinary | Bitcoin doubled costs | Ether ordinary | Ether doubled costs |
|---|---:|---:|---:|---:|
| 2017, partial | +4.93% | −0.95% | +0.38% | −2.58% |
| 2018 | −17.48% | −36.21% | −6.01% | −11.87% |
| 2019 | −21.09% | −53.49% | −9.19% | −20.52% |
| 2020 | −19.76% | −58.23% | −6.09% | −15.24% |
| 2021 | −31.79% | −62.70% | +0.55% | −7.38% |
| 2022 | −46.29% | −68.15% | −13.28% | −23.86% |
| 2023 | −29.98% | −59.75% | −8.96% | −17.40% |
| 2024 | −33.34% | −60.66% | −8.58% | −17.83% |
| 2025 | −44.27% | −13.32% | −11.70% | −21.19% |
| 2026, partial | −26.39% | 0.00% | −5.58% | −14.35% |

The first period begins on 17 August 2017 and the last ends after 7 September 2026; neither is a full calendar year. Missing-hour coverage is recorded separately. Of 110 monthly periods, ordinary-cost Bitcoin had 13 profitable and 97 losing months; Ether had 25 profitable and 85 losing months. Doubled-cost Bitcoin had 4 profitable, 90 losing and 16 flat months; Ether had 7 profitable and 103 losing months. These are summaries of the same already-inspected history, not 110 independent validation trials.

Bitcoin's doubled-cost account was flat in 2026 because its balance had fallen to 20.01 USDT and its modeled entry sizes were too small: all 2,906 attempted entries that year were rejected for falling below the minimum trade value. Its last completed trade exited at a loss on 5 October 2025. The flat period is not evidence of improved trading.

### What went wrong in these simulated accounts

For the exact trades that were executed, price movement before modeled trading friction contributed positive amounts. Fees, spread and slippage were larger:

| Account | Reference-price movement (USDT) | Total modeled trading costs (USDT) | Net completed-trade result (USDT) |
|---|---:|---:|---:|
| Bitcoin, ordinary | +4,405.18 | 14,020.54 | −9,615.36 |
| Bitcoin, doubled | +2,736.26 | 12,716.25 | −9,979.99 |
| Ether, ordinary | +1,417.38 | 6,550.09 | −5,132.71 |
| Ether, doubled | +873.92 | 9,005.23 | −8,131.31 |

This is an accounting breakdown of the recorded quantities and entry/exit reference prices, **not a separate profitable zero-cost backtest**. Costs also affect sizing, fills and which entries remain affordable; doubled costs do not simply subtract a fixed amount from the ordinary account. Reusing capital across many trades allows cumulative costs to exceed the initial balance. The ordinary accounts won only 2,237 of 5,715 Bitcoin trades and 468 of 1,190 Ether trades after costs.

The practical research finding is that these rules' price-movement gains did not cover their execution costs, and losses persisted across many years. Raising a displayed confidence score, ignoring fees or choosing only a good year would not fix that. Any changed rule, asset filter or cost assumption needs its own recorded research round and later untouched evidence. Nothing in this result changes the frozen live study or qualifies these rules for real-money use.

## Retained failed attempt and amendment

The first actual attempt failed safely on 9 September at 09:54 UTC when it reached archived bars that did not start on the UTC hourly clock. Its source and partial trading records are retained; it has no completed performance result. The completed second attempt has a separately recorded amendment and links back to that failure.

The archive parser had accepted 42 one-hour bars per asset with shifted timestamps around a February 2018 data gap. The replay engine correctly refused them under its stricter clock rule. The correction does not round their timestamps or relax the engine. It preserves those rows in a quarantine record and treats the interval as missing execution data. This choice is based on timestamp quality, not on whether those trades would win or lose.

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

## Verification and retained results

The [machine-readable summary](../data/research/historical-account-replay-2026-09-09.json) retains exact balances, all 110 monthly and 10 yearly periods for each account, selection and exclusion details, execution assumptions and the validation receipt. The tables above round money and percentages to two decimal places.

A separate standard-library checker reconstructed both event chains: 504,387 Bitcoin records and 404,599 Ether records. It reconciled original decision/input-window fingerprints, every entry and exit cash flow, fees, spread, slippage, completed trades, all observed-close valuations, drawdowns, calendar changes and gap counts. An independently implemented result-level check also reproduced the headline and calendar figures. Decimal calculation-order differences were economically negligible and below the checker's 0.000000000000001-USDT tolerance. Both the original checker and an equivalent version caching immutable bar hashes passed; the latter also verified the terminal result hash. The numeric tables above were checked directly against the full raw result.

This is accounting and evidence verification, not an independent market-performance sample, third-party attestation or recomputation of every strategy formula. Automated tests additionally exercise future-price perturbations, appended data, revised-bar rejection, next-opening entry isolation and conservative gap/exit handling. Those checks support the stated causal design; they do not certify that historical archives can never be revised.

The evaluated source identity is `81b465ccff8792833f2923b13aa56c1a149e6ddd35a2b6fbfc8f682d519f14da`. The full `result.json` byte SHA-256 is `82931f53004aebc98f1e29bf79d4af700708a04b2b2817c838e25444eab94132`; the data-quality artifact SHA-256 is `3f6a859302a0a68e91b52f3b0baf4bead806e13339ca6c32aeef2d196c8810e3`. The summary retains the remaining protocol and journal identities.

Full event journals, trades, equity curves, pinned archives and the failed first attempt remain on the study Mac. The completed attempt is in `/Users/james/Library/Application Support/Nowcaster/HistoricalReplays/round-002-20260909`; the separate verification receipt is in `/Users/james/Library/Application Support/Nowcaster/HistoricalReplayAudits/round-002-20260909/validation.json`. These large local evidence files are not embedded in the compact GitHub summary. The original live study and both frozen historical source checkouts are retained unchanged.

Software verification on the evaluated source passed: 1,272 Python tests, 85 native tests, deterministic fixture parity/repeatability, code-format and secret checks, a fresh macOS app build, signature/integrity checks and packaged recorded-feed playback. The previously documented foreground-window visual-smoke limitation remains; software tests are not evidence of trading profitability.

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
