# Longer holding periods: no profitable rule found

**Result:** every one of the 24 tested variations lost on average after the modeled trading costs. None passed the historical screen. These rules are not approved trading opportunities, and this experiment does not unlock the app's trading alerts or real-money controls.

This round tested whether allowing a trade more time and room to move would improve the earlier results. It used three existing indicators—Bollinger/Keltner squeeze, MACD trend and volatility-scaled trend—on BTC/USDT and ETH/USDT. Each rule was tested with six- and twelve-hour time limits, one- and two-ATR stops, and a target 1.5 times its stop distance. ATR measures recent price movement; a larger ATR means the price has been moving around more.

## What the data covers

| Check | BTC/USDT | ETH/USDT |
|---|---:|---:|
| Hourly candles | 79,270 | 79,270 |
| Verified archive files | 130 | 130 |
| Gaps within the observed span | 32 | 32 |
| Missing hours within that span | 142 | 142 |
| Impossible archive boundary rows excluded | 15 | 15 |
| Duplicate timestamps / missing OHLCV fields | 0 / 0 | 0 / 0 |

The actual observed span is 17 August 2017 at 04:00 UTC through 8 September 2026 at 00:00 UTC. Files were checked against Binance's published SHA-256 checksums. The checksum list's identity is `b4760f75a23b09990d1ec4a839a97c7c18b9553cf16d7d414cd5781d8d3c74d1`.

This is the available hourly archive for these two instruments—not every historical market, tick, order book or asset. All inspected history, including the earlier audit's old holdout, is now development data. It cannot provide a fresh independent test. Archives can also be corrected after publication, so their history does not reproduce exactly what a trader saw at the time.

## How results are counted

Signals use closed candles and past information only. Indicators restart after gaps. A signal's minimum target distance is checked using its own closing price and past ATR, never the next opening price. Hypothetical entry occurs on the next continuous candle. A candle touching both stop and target counts as a stop; expiry takes priority over a target on the last permitted candle. Trades crossing missing data or the end of available observation are excluded and counted in the diagnostics, not guessed.

The base cost is 34 basis points per round trip: **0.34%** in total. It represents two 10-basis-point fees, two 2-basis-point half-spreads and two 5-basis-point slippage allowances. The stress case uses **0.68%**. These are explicit modeling assumptions, not a claim about a particular account's fee tier. Cash sizing and quote-based simulated execution are tested separately in the prospective study.

Four chronological development folds check whether a rule's average return survives different periods. A pass requires at least 30 completed trades and a positive stressed average in **every** fold. Selection uses the worst fold first, then the full-history stressed average, then a stable identity. All 24 variations and their failures remain in the machine-readable report; selecting a diagnostic rule does not make it qualified.

## Rules retained for diagnostic observation

| Independent account | Rule | Stop / target | Maximum hold | Full-history trades | Average after base costs | Worst-fold stressed average | Historical screen |
|---|---|---|---:|---:|---:|---:|---|
| BTC/USDT | Volatility-scaled trend | 2 / 3 ATR | 12 hours | 5,664 | −0.2598% | −0.6528% | Failed |
| ETH/USDT | Bollinger/Keltner squeeze | 1 / 1.5 ATR | 12 hours | 1,193 | −0.2411% | −0.6551% | Failed |

These are selected by the predeclared worst-period ranking, not by the most flattering full-history average. Even the best full-history average among all 24 variations—ETH squeeze with a two-ATR stop and six-hour expiry—was negative, at approximately **−0.1985% per trade** after base costs. None is a demonstrated profitable strategy. Do not add results across overlapping rules or multiply an average trade return into a claimed portfolio return.

## What happens next

The separate [live paper study](live-paper-study.md) can collect prospective observations and hypothetical fills without placing orders. It records fees, spread, slippage, missed fills, interruptions and open exposure. These two failed-screen rules are **diagnostic only**: even a later lucky positive balance cannot override their failed historical screen. An improved rule would require a separately recorded research round and genuinely new prospective data.

The study's fixed 90-day window is a measurement protocol, not a promised date when the app will become profitable. Reliable live execution would need additional broker-specific evidence; simulated public quotes cannot establish it. The app remains unsuitable for relying on as a source of trading profits.

## Reproduce and inspect

```sh
PYTHONPATH=. .venv/bin/python scripts/search_holding_periods.py \
  --root . \
  --output-dir build/holding-period-search-reproduction \
  --start 2017-08-17T00:00:00Z \
  --end-exclusive 2026-09-08T00:00:00Z
```

The [retained full discovery](../data/research/holding-period-search-2026-09-08.json) contains every trial, selection, fold, exclusion count, archive checksum and generating source/runtime fingerprint. A second complete run reproduced every trial, selection and archive manifest unchanged. Its generating historical-source identity is `353e6721adbf3322afdd699f4321cd024380ac3f1de5460eed3a5550a9c0028c`. Registration verifies this full contract and refuses incomplete, altered or mismatched evidence. These local integrity checks are not independent external attestation of profitability.

Sources: [Binance public data documentation](https://github.com/binance/binance-public-data), [Binance fee schedule](https://www.binance.com/en/fee/trading), [paper-trading limitations](https://docs.alpaca.markets/us/docs/paper-trading), [Bailey et al. on backtest overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
