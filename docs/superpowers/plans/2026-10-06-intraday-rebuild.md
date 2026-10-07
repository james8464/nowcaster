# Intraday CFD research rebuild

## Authority

The user-approved 6 October 2026 plan in this chat is the specification. This implementation is paper-only. The frozen BTC/ETH prospective study and its source checkout are immutable.

## Tasks

### Task 1: New instrument and provider contract

Produce a separate CFD research namespace rather than widening the frozen Binance spot protocol. Model exact broker product, quote/candle source, observed and available times, bid/ask, session, costs and missing-data states. Add an OANDA practice-only adapter with no order endpoint, secrets in the macOS keychain/environment only, and explicit data caveats. Test malformed/future/stale data and provider parsing before implementation.

### Task 2: Causal strategy and execution core

Implement fixed opening-range, trend-pullback and range-reversion hypotheses for confirmed five-minute bars, with long/short symmetry and abstention. Entries use the next available bid/ask quote; exits include stop, target and session close, with conservative gap/slippage handling. Add risk caps and an append-only paper event ledger. Test each behavior RED then GREEN.

### Task 3: Historical evaluation and prospective gates

Add an immutable round manifest, market feasibility gates, chronological train/validation/sealed-test evaluation, all-attempt retention, daily-block uncertainty and a prospective qualification gate (90 days, 100 trades, positive net lower bound, coverage and drawdown). Historical broker candles cannot qualify account-specific execution. Test leakage, stressed costs, ledger restart integrity and failures.

### Task 4: Native and Pine presentation

Expose a read-only research summary to the existing Trade Desk: feed health, no-trade reasons, full paper trade plan, positions and evidence status. Add a Pine v6 companion reflecting confirmed-bar rules without presenting it as an independent profit proof. Native views never enable real orders. Test decoding/presentation and Pine parity on fixed fixtures.

### Task 5: End-to-end verification and release

Run Python and Swift tests, fixture parity, macOS build/UI smoke where available, documentation and secret scan. Review whole diff and correct high-severity findings. Keep work on `feature/research-round-2`; do not touch the frozen checkout or study. Commit and push only after fresh verification.

## Constraints

- No real trades, broker execution, market-data purchase or account registration.
- The OANDA practice token is a future user prerequisite; without it, provider integration is verified by deterministic fixtures only.
- No automatic strategy promotion from historical data or the existing BTC/ETH paper study.
