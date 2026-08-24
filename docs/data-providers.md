# Intraday data providers

## Provider identity is part of the evidence

Bars are never treated as interchangeable merely because they share a ticker. Dataset identity includes provider, feed, provider symbol, interval, requested UTC range, exchange calendar version, payload hashes, and coverage gaps. Raw credentials and bulk/licensed bars stay outside Git. Git contains only exact compact manifests, summaries, and the deterministic CI fixture descriptor.

## Binance spot

The configured app labels map as follows:

| App label | Official request symbol | Venue/feed | Quote disclosure |
|---|---|---|---|
| `BTC-USD` | `BTCUSDT` | Binance spot | USDT quote; not composite USD |
| `ETH-USD` | `ETHUSDT` | Binance spot | USDT quote; not composite USD |

“All available Binance history” means every finalized bar officially returned for each enabled compatible symbol/interval, beginning at that pair's earliest official bar and ending before one fixed UTC cutoff recorded in the manifest. The intervals derived from enabled crypto-compatible strategies are `5m`, `15m`, `1h`, and `4h`. A provider gap remains a gap; it is not filled with generated, daily, or third-party data.

The implementation uses Binance's official [Spot API documentation](https://github.com/binance/binance-spot-api-docs) and is compatible with the checksum posture of Binance's official [public data archive tooling](https://github.com/binance/binance-public-data). REST pages retry bounded transient failures, are written to an external cache atomically, and have adjacent SHA-256 files. Sorted ingestion is deterministic. Re-running verifies each cached payload before reuse.

Run the exhaustive provider profile with an external cache and an explicit cutoff:

```bash
.venv/bin/python -m src.cli strategy research \
  --profile live \
  --cache-dir /absolute/path/outside/the/repository \
  --cutoff 2026-08-24T00:00:00Z \
  --output-dir build/full-history-research
```

The default live command has no chunk cap. `--max-chunks-per-scope` is diagnostic only: using it guarantees an incomplete/unavailable result and records the unattempted range. Never present a diagnostic probe as a full-history backtest.

## Alpaca equities

The adapter uses Alpaca's official [Historical Stock Data API](https://docs.alpaca.markets/docs/historical-stock-data-1) and keeps feed identity (`iex` or `sip`) in the dataset key. Feeds are never spliced. The Task 9 environment had no usable key/secret pair, so equity intraday history is unavailable and no stock strategy result was inferred.

Beginner setup:

1. Create an Alpaca account and understand the market-data feed/entitlement you will use.
2. Store credentials only in your shell, password manager, or an untracked local `.env`.
3. Set a key alias and its secret; the preferred names are `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`. The adapter also accepts `ALPACA_API_KEY` with `ALPACA_API_SECRET`.
4. Run a small scoped ingest, inspect provider/feed and gaps, then expand the range. Do not commit `.env`, cache files, database files, logs, or command output containing values.

Example with placeholders only:

```bash
export APCA_API_KEY_ID='<local-key-id>'
export APCA_API_SECRET_KEY='<local-secret>'
.venv/bin/python -m src.cli strategy ingest --provider alpaca --feed iex --symbol SPY \
  --interval 5m --strategy-id opening_range_breakout \
  --start 2026-08-03T13:30:00Z --end 2026-08-04T20:00:00Z
```

Credential probing records only booleans (`key_present`, `secret_present`) and an availability reason. It never serializes raw values.

## CSV import and deterministic CI fixture

CSV is a strict local adapter, not a fallback provider. Its rows require explicit UTC timestamp, OHLCV, finalized flag, availability time, and revision. Feed defaults to `local` and remains in provenance.

The CI fixture is generated from the committed descriptor at `data/demo/intraday/research-fixture.json`. It has 110 finalized bars for each exercised scalar scope at a fixed `2026-08-20T00:00:00Z` cutoff. It requires no network or credentials. Its summary says `deterministic generated fixture` so it cannot be confused with live Binance data.

## Data-quality contract and analytical risk

Every published profile records:

- intended record grain and the provider/feed identity;
- natural-key uniqueness and duplicate count;
- OHLC consistency, nonnegative volume, UTC normalization, finalization, and cutoff freshness;
- exact requested/observed coverage and gap counts;
- revision ledger counts and payload checksums;
- return/volume distributions and robust outlier counts;
- `available_at`/decision and decision/execution time-travel checks;
- prefix-invariance audit counts;
- rows per symbol/interval versus strategy warm-up requirements;
- code, configuration, dataset, snapshot, and cache-manifest hashes.

Duplicates can double-count evidence; invalid OHLCV can create impossible fills; gaps can distort indicators and folds; late/revised timestamps can leak future information; stale cutoffs can make a signal look current; outliers can dominate Sharpe; and small samples can make promotion statistics meaningless. Any failed causal/coverage check makes the affected provider scope unavailable.
