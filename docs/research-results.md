# Reproducible intraday research results

## Interpretation first

These artifacts demonstrate a research process. They do not promise profit, and backtests are not live evidence. The deterministic CI profile is software-test data. The exhaustive provider attempt remains unavailable for strategy inference because exact coverage gates found exchange-history gaps.

## Deterministic CI profile

The committed artifact is `data/research/ci/research-summary.json`, accompanied by a cautious Markdown report and a native-compatible snapshot v2. It uses one BTCUSDT scalar fixture per exercised interval, 110 finalized bars per scope, a fixed `2026-08-20T00:00:00Z` cutoff, and no network or credentials.

Expected outcome after regeneration:

- all 19 enabled strategies appear exactly once in the compact catalog;
- 15 scalar-compatible strategies complete at least one causal evaluation scope;
- four strategies are explicitly unavailable: the two equity-session rules, paired-context rule, and five-asset cross-sectional rule;
- failed/unavailable evidence receives no positive ensemble component weight;
- 36 strategy/symbol/interval audit records pass prefix invariance;
- the bounded learning benchmark writes exactly two candidate ledger rows;
- 330 logical bars have zero duplicates, zero invalid OHLCV rows, zero coverage gaps, and zero revisions;
- reruns in fresh databases produce byte-identical compact summaries.

No return or Sharpe from generated fixture bars is reported as financial evidence.

Reproducibility identifiers for the committed run:

| Identifier | SHA-256 / canonical hash |
|---|---|
| Code | `0f79745cf6f9393908820f758032864fe468ca1179e72e173a2daaa62092e6e8` |
| CI configuration | `bdd5db762ac98ad464c552409781a71a944928083061cdc6979240e5bec324cc` |
| CI aggregate dataset | `f3b7a8131155c59abe7b7c6e03b66af5252e0304f8649f12aa5fa32978b1e34c` |
| CI semantic snapshot | `e9f0e33867b833d6d769272e7ba9ce1a9bf511133d8294323fa7942a672af18d` |
| CI summary file | `8a54e46124c7ac4e1b1b70c244e5b90b2cd74f2610ea890971546d5b179dc488` |
| CI snapshot file | `f73ebad40e812d2de82a41f530ba910f2e844fe526e5b6c4c952ca8cf4cbfaf8` |

## Exhaustive official Binance attempt on 24 August 2026 UTC

Task 9 ran the unbounded official Binance spot REST profile from the earliest provider boundary through the fixed cutoff `2026-08-24T00:00:00Z`. It attempted all 880 deterministic 30-day chunks: 110 chunks for each of BTCUSDT/ETHUSDT at every configured `5m`, `15m`, `1h`, and `4h` interval. There was no diagnostic chunk cap. All 3,084 raw JSON pages were written outside Git with adjacent SHA-256 checksums; the final replay verified the cache without network access. Of the 880 requested chunks, 736 met exact coverage and 144 were explicitly unavailable because requested bars remained missing.

| Provider pair | Interval | Attempted UTC coverage | Chunks | Rows stored | Missing expected bars | Gap segments | Result |
|---|---:|---|---:|---:|---:|---:|---|
| BTCUSDT | 5m | 2017-08-17 04:00 to 2026-08-24 00:00 | 110 | 946,909 | 1,715 | 34 | Unavailable: coverage gaps |
| BTCUSDT | 15m | same range | 110 | 315,643 | 565 | 33 | Unavailable: coverage gaps |
| BTCUSDT | 1h | same range | 110 | 78,924 | 128 | 28 | Unavailable: coverage gaps |
| BTCUSDT | 4h | same range | 110 | 19,747 | 16 | 8 | Unavailable: coverage gaps and unmet five-asset context |
| ETHUSDT | 5m | same range | 110 | 946,909 | 1,715 | 34 | Unavailable: coverage gaps |
| ETHUSDT | 15m | same range | 110 | 315,643 | 565 | 33 | Unavailable: coverage gaps |
| ETHUSDT | 1h | same range | 110 | 78,924 | 128 | 28 | Unavailable: coverage gaps |
| ETHUSDT | 4h | same range | 110 | 19,747 | 16 | 8 | Unavailable: coverage gaps and unmet five-asset context |

The isolated working database stored 2,722,446 finalized bars. It found 4,848 missing expected bars across 206 distinct gap segments, zero duplicate logical bars, zero invalid OHLCV rows, and zero revisions; timestamps were UTC-normalized, finalized, and no later than the cutoff. The return diagnostic covered 2,722,438 observations and flagged 64,358 robust outliers for downstream caution. Because no symbol/interval passed exact full-coverage gates, all 19 strategies and the learning benchmark remained explicitly unavailable and received no ensemble weight. Missing data was never replaced with CI data. The 4h cross-sectional rule would remain unavailable even with complete scalar histories because two configured assets cannot satisfy its five-asset point-in-time universe.

The provider configuration now uses Binance `BTCUSDT`/`ETHUSDT` exactly. These results describe that venue's USDT-quoted spot market, not the separately disclosed composite-USD daily demo proxies.

The exact attempt and page checksums are kept in the compact live manifest generated during Task 9. The 463 MB raw page cache and 1.1 GB working DuckDB remained outside Git.

The final reproducibility identifiers are updated from the post-format cache-only replay below:

| Identifier | SHA-256 / canonical hash |
|---|---|
| Code | `0f79745cf6f9393908820f758032864fe468ca1179e72e173a2daaa62092e6e8` |
| Live configuration | `2775d97ed7934398ce2086d2c5f71e760263d2121c635328470c30565cfeada0` |
| Live aggregate dataset | `4fad454ba2f3695fe135d828dc741a862f482a5bacdcae54999f60604385c09e` |
| Semantic snapshot | `967c08eeccaa8e11028e3010acddb6a988e7d2dbda0c8fdd52e9c9904c12c60f` |
| External cache manifest (3,084 pages) | `30228b87de6e2687064fcf9ad63842c2cc89f0fca087f7b4cdc6ea5749e570ff` |
| Live summary file, final replay | `2221adaa827cce52d38bfffea1c736a2d1f8f79179e163773c682ba085822a84` |
| Live snapshot file, final replay | `f8631ae688396864a5da589f65b6d5ed5b545422211312664ebea6fc8a562a7f` |

## Alpaca status

`APCA_API_KEY_ID`/`ALPACA_API_KEY` and a matching secret were absent. The published probe contains only `key_present=false`, `secret_present=false`, and setup guidance. Equity intraday history and equity-session strategy results are unavailable.

## Remaining uncertainty

Even a complete result could fail live because of regime change, selection bias, capacity, queue position, stressed spreads, changing fees, borrow recalls, exchange/API outages, data revisions, taxes, or implementation drift. Deflated Sharpe and overfitting diagnostics help audit a search; they do not convert historical association into a guaranteed edge. A credible next step is a frozen forward paper-trading period with measured provider latency and realized costs.
