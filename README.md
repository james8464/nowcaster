# Nowcaster for macOS

Nowcaster is a native Mac app for learning how a computer can study stocks and cryptocurrencies without pretending that it can predict the future.

It collects historical market and company information, asks models what that information might have suggested at the time, and then checks those ideas against what happened later. The app presents the result as a **research posture**—long, short, or abstain—along with the evidence, risks, and historical test results behind it.

Nowcaster is a research and education tool. It is not a brokerage, does not place trades, cannot guarantee profit, and is not investment advice.

![Nowcaster Today view](docs/images/macos/today-light.png)

## What problem does it solve?

Financial markets produce far more information than a person can comfortably compare by hand. A company publishes sales figures, its share price moves, public attention changes, and the broader market may be rising or falling at the same time.

Nowcaster brings those pieces into one place and helps answer four questions:

1. What information was actually available on a given date?
2. Did a model see a positive, negative, or unclear setup?
3. Would similar historical signals have survived realistic costs and delays?
4. Is the evidence strong and stable enough to study further?

The app deliberately shows **abstain**, **research only**, or **not ready** when the evidence is weak. Doing nothing is a valid result.

## A beginner's guide to the language

| Term | Plain-English meaning |
|---|---|
| **Stock** | A small ownership share in a company. |
| **Cryptocurrency** | A digitally traded asset such as Bitcoin or Ether. It is usually more volatile than a large-company stock. |
| **Long** | A view that an asset may rise. A normal purchase is a long position. |
| **Short** | A view that an asset may fall. Real short selling involves borrowing and has special costs and risks. |
| **Signal** | A model's research output. It is a clue to investigate, not an instruction to trade. |
| **Confidence** | How complete and consistent the supporting evidence is. It is not the probability of making money. |
| **Backtest** | A historical simulation that asks how a fixed set of rules would have behaved in the past. |
| **Out of sample** | Data kept away from the model while it was being designed, then used as a more honest final exam. |
| **Sharpe ratio** | A rough comparison of return with volatility. Higher is generally better, but a good historical value can disappear in live markets. |
| **Drawdown** | The fall from a portfolio's previous high to a later low. It helps show how painful a strategy could have been. |
| **Nowcast** | An estimate of something happening now or soon, made before the final official number is known. |

## How it works

```mermaid
flowchart LR
    A[Historical public data] --> B[Check dates and data quality]
    B --> C[Build only information known at that time]
    C --> D[Train and compare models]
    D --> E[Simulate later trades with costs and delays]
    E --> F[Export a checked snapshot]
    F --> G[Show evidence in the macOS app]
```

### 1. Collect historical evidence

The bundled demo uses frozen public snapshots for three companies—Starbucks (`SBUX`), McDonald's (`MCD`), and Costco (`COST`)—plus Bitcoin (`BTC-USD`) and Ether (`ETH-USD`). It also includes broad-market and sector prices for comparison.

Company filings come from SEC data. Public-attention features come from Wikimedia page views. Price snapshots are stored with checksums so the same demo can be reproduced later.

### 2. Re-create what was knowable at the time

This is one of the most important safeguards. A model studying 2022 must not accidentally see a figure published in 2023. Nowcaster records when an input became available and shifts market features so future information cannot leak backward.

### 3. Produce separate earnings and intraday-strategy research

Stocks and cryptocurrencies behave differently, so they use separate research paths:

- The stock models estimate company revenue before an earnings event and compare it with a simple historical expectation.
- The intraday library evaluates 19 configured trend, mean-reversion, volatility/volume, session, and relative-value rules at `5m`, `15m`, `1h`, or `4h` where their requirements are met.

The bundled stock expectation is a seasonal historical proxy. It is **not Wall Street consensus**.

### 4. Backtest the rules

Nowcaster walks forward through time instead of randomly mixing old and new observations. It chooses the final 20% of chronology before filtering, keeps that period out of training/calibration/weight learning, and executes a bar's signal no earlier than the next actionable bar. Intraday simulations include fees, half-spread, slippage, latency, participation limits, funding/borrow policy, exposure limits, and adverse stop-before-target ordering when both prices occur inside one bar.

The tests also look for unstable subperiods, excessive drawdowns, sensitivity to higher costs, and results that may simply be statistical luck. A backtest is still only a simulation; it cannot recreate liquidity, exchange failures, taxes, capacity, or human behaviour perfectly.

### 5. Explain the result in the Mac app

The Python engine writes one validated JSON snapshot. The SwiftUI app reads that file directly, so there is no WebView, JavaScript frontend, account server, or background website.

The app keeps the last known good snapshot if a refresh fails. It never stores brokerage credentials or sends an order.

## What you can explore in the app

- **Today** — a plain overview of the current research snapshot and its warnings.
- **Markets** — the stock and crypto instruments included in the research universe.
- **Earnings** — historical company events and revenue forecasts.
- **Signals** — long, short, and abstain postures with supporting and invalidating evidence.
- **Backtests** — returns, risk, drawdowns, costs, and development versus final-test results.
- **Model Lab** — model comparisons, calibration, and diagnostic information.
- **Data Quality** — missing, late, or invalid information that could weaken a result.
- **Pipeline Runs** — the steps used to rebuild the local research snapshot.

A sensible beginner workflow is: start on **Today**, open one signal, read its invalidation evidence, and only then look at its backtest. Avoid judging a model from its headline return alone.

## What the bundled results currently say

These are frozen demo results through 22 August 2026. They are included to demonstrate the evaluation process, not to advertise a trading system.

| Research system | App status | Development Sharpe | Final-test Sharpe | Total trades | Worst drawdown |
|---|---|---:|---:|---:|---:|
| BTC-USD calibrated ensemble | Research only | 0.769 | 0.571 | 173 | -26.1% |
| ETH-USD calibrated ensemble | Not ready | 0.152 | 0.593 | 57 | -16.8% |

In plain language:

- Bitcoin produced an interesting historical result, but it was not profitable consistently enough across subperiods to pass the promotion rules.
- Ether's development result and sample size were too weak, even though its smaller final period was positive.
- The stock event study is also research only. Its small three-company demo did not establish a dependable edge.

No bundled strategy is considered ready for real-money decisions. The newer intraday CI fixture validates deterministic software behavior and is not market-performance evidence. Historical patterns can be overfit and can stop working.

## Install and run

### Open a built copy

Download `Nowcaster-macOS.zip` from the repository's Releases or Actions artifacts, unzip it, then Control-click `Nowcaster.app` and choose **Open** if macOS warns about an unnotarized local build.

The app requires macOS 15 or later. Locally created builds are ad-hoc signed unless Apple Developer signing credentials are supplied.

### Build it from source

You need macOS 15 or later, Xcode Command Line Tools, Python 3.11–3.13, and `uv`.

```bash
xcode-select --install
brew install uv
git clone https://github.com/james8464/nowcaster.git
cd nowcaster
make setup
make demo
make macos-app
open build/Nowcaster.app
```

The demo is deterministic and needs no API keys. `make demo` builds the local DuckDB database, runs the research stages, and exports the snapshot used by the Mac app.

## Useful developer commands

```bash
make lint                # Check Python formatting and common mistakes
make test                # Run the Python test suite
make demo                # Rebuild the bundled research demo
make research-ci         # Rebuild the network-free intraday research fixture
make research-live CACHE_DIR=/external/path # Exhaustive official Binance history
make research-live-probe CACHE_DIR=/external/path # Bounded official-provider coverage probe
make report              # Write a measured research note
make sync-macos-snapshot # Merge authoritative CI research into the app's first-launch data
make verify-swift-fixture-parity # Read-only check that the committed app fixture matches CI research
make macos-test          # Run Swift model and app tests
make macos-app           # Assemble build/Nowcaster.app
make macos-ui-test       # Launch the app and verify a real native window
make macos-screenshots   # Capture the primary native views
make release-archive     # Build the app ZIP and SHA-256 checksum
```

## Project layout

```text
macos/Nowcaster/    native SwiftUI app and Swift tests
src/                data ingestion, models, backtests, and snapshot export
config/             market universe, features, and model settings
data/demo/          frozen public snapshots and deterministic fixture manifests
data/research/      compact reproducible research summaries; never bulk bars
docs/               architecture, methodology, privacy, and native screenshots
tests/              Python unit, integration, leakage, and pipeline tests
scripts/            native app build and visual-verification tools
.github/workflows/  continuous integration and macOS release packaging
```

For deeper technical detail, see the [strategy methodology](docs/strategy-methodology.md), [provider guide](docs/data-providers.md), [research results](docs/research-results.md), [architecture](docs/architecture.md), [earnings/daily methodology](docs/methodology.md), [backtest protocol](docs/backtest_protocol.md), [data dictionary](docs/data_dictionary.md), [macOS guide](docs/macos_app.md), [privacy policy](docs/privacy.md), and [verification record](docs/native_verification.md).

## Data, privacy, and limitations

- The bundled demo is historical and is not a real-time market feed.
- Public data can be missing, revised, delayed, or wrong.
- Yahoo chart data comes from an unofficial endpoint with no service guarantee.
- Users are responsible for data-provider terms and production data licences.
- `BTC-USD` and `ETH-USD` map to Binance `BTCUSDT` and `ETHUSDT` for provider research. These are venue-specific USDT spot pairs, not composite USD prices.
- Raw credentials and bulk/licensed bars stay outside Git. Only fixture descriptors, checksummed manifests, and compact results are committed.
- The app does not collect personal information, connect to a broker, or store brokerage credentials.
- Short selling, leverage, crypto trading, and derivatives can lose more money or move faster than a beginner expects.

This standalone project was originally developed inside a downloaded GS Quant source tree and can optionally cross-check a small return calculation with the open-source `gs_quant` package. Nowcaster is not affiliated with or endorsed by Goldman Sachs.
