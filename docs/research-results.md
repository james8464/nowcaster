# Reproducible intraday research results

## Interpretation first

These artifacts demonstrate a research process. They do not promise profit, and backtests are not live evidence. The deterministic CI profile is software-test data. The provider probe is incomplete and therefore unavailable for strategy inference.

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
| Code | `a0eefafbde08445ba5ceb9432f41a8a018c79707c49a293add3eb5bdb0b5cb09` |
| CI configuration | `bdd5db762ac98ad464c552409781a71a944928083061cdc6979240e5bec324cc` |
| CI aggregate dataset | `f3b7a8131155c59abe7b7c6e03b66af5252e0304f8649f12aa5fa32978b1e34c` |
| CI semantic snapshot | `e9f0e33867b833d6d769272e7ba9ce1a9bf511133d8294323fa7942a672af18d` |
| CI summary file | `33d351ed195ee5f901d60be5543fbc266553812c5d0924653ba1e6d099e35b92` |
| CI snapshot file | `f73ebad40e812d2de82a41f530ba910f2e844fe526e5b6c4c952ca8cf4cbfaf8` |

## Official Binance attempt on 24 August 2026 UTC

Task 9 performed a real diagnostic request against the official Binance spot REST API with cutoff `2026-08-24T00:00:00Z`, using an external SHA-256 cache. The diagnostic cap was one 30-day chunk per scalar-compatible symbol/interval. This was intentionally not an exhaustive research run.

| Provider pair | Interval | Attempted provider coverage | Rows stored | Missing expected bars | Result |
|---|---:|---|---:|---:|---|
| BTCUSDT | 5m | 2017-08-17 04:00Z to 2017-09-16 04:00Z | 8,557 | 83 | Unavailable |
| BTCUSDT | 15m | same range | 2,853 | 27 | Unavailable |
| BTCUSDT | 1h | same range | 714 | 6 | Unavailable |
| ETHUSDT | 5m | same range | 8,557 | 83 | Unavailable |
| ETHUSDT | 15m | same range | 2,853 | 27 | Unavailable |
| ETHUSDT | 1h | same range | 714 | 6 | Unavailable |
| BTCUSDT | 4h | same range | 180 | 0 | Data chunk complete; strategy unavailable |
| ETHUSDT | 4h | same range | 180 | 0 | Data chunk complete; strategy unavailable |

The probe stored 24,608 bars in its external/local working database, observed no duplicate logical bars, no invalid OHLCV rows, and no revisions. It found exchange-history gaps in the 5m, 15m, and 1h chunks. The 4h chunks were complete, but the only enabled 4h rule needs a five-asset point-in-time universe, so no strategy result was manufactured from two assets. Every interval stopped before its remaining range because of the declared diagnostic cap. Therefore the probe ran no provider-backed strategy evaluation or learning benchmark. Missing data was not replaced with CI data.

`BTC-USD`/`ETH-USD` in app configuration were mapped truthfully to Binance `BTCUSDT`/`ETHUSDT`. Results would describe that venue's USDT-quoted spot market, not composite USD.

The exact attempt and cache-page checksums are kept in the compact live manifest generated during Task 9. The 3.9 MB bulk raw page cache and 12 MB working DuckDBs remained outside Git.

The live aggregate dataset hash is `a72666c903bc5456b73ab97c376fb807968d1ac4f5471bc12c9994c56d18da24`; configuration hash is `2775d97ed7934398ce2086d2c5f71e760263d2121c635328470c30565cfeada0`; semantic snapshot hash is `a3945aea2f3937b527bc09cdfccdc121137e28848bd8b2e1c80f1584a2790c76`; external cache-manifest hash is `a9402b24e12f1c0507c459bc3f21f3135910fa691edb376532fe9ea5721e95b7` across 36 raw/checksum-paired JSON payloads; and the two fresh-database live summary files both had SHA-256 `4b3652cde32f0dd3367ec96033041a507102b67f0c8de5f5c34beafd2faf36ec`.

## Alpaca status

`APCA_API_KEY_ID`/`ALPACA_API_KEY` and a matching secret were absent. The published probe contains only `key_present=false`, `secret_present=false`, and setup guidance. Equity intraday history and equity-session strategy results are unavailable.

## Remaining uncertainty

Even a complete result could fail live because of regime change, selection bias, capacity, queue position, stressed spreads, changing fees, borrow recalls, exchange/API outages, data revisions, taxes, or implementation drift. Deflated Sharpe and overfitting diagnostics help audit a search; they do not convert historical association into a guaranteed edge. A credible next step is a frozen forward paper-trading period with measured provider latency and realized costs.
