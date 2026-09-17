# Multi-Asset Candidate Research Design

## Purpose

Add a separate, paper-only candidate-research path for evaluating whether a small liquid universe has any reproducible net edge. It is not a trading system, does not connect to accounts, and cannot promote a strategy to real-money use. It leaves the frozen BTC/ETH prospective study unchanged.

## Scope and decisions

- The initial research universe is BTCUSDT and ETHUSDT from Binance spot, plus an optional WTI candidate represented only by a verified, licensed, finalized intraday CSV dataset. WTI is **unavailable** until its provider, contract/continuous-series policy, session calendar, roll policy, bid/ask cost model, and coverage pass validation.
- The candidate families are the existing independently implemented trend, breakout/volatility-expansion, and mean-reversion families. The campaign uses their fixed, versioned strategy definitions; it does not search arbitrary indicator combinations or alter a definition after results are seen.
- Each asset/provider/feed/contract/interval combination is an independent cohort. A source change, contract-roll rule change, execution assumption change, code change, or parameter change creates a new candidate identity and a new retained campaign; it cannot replace earlier evidence.
- Every attempted candidate, including unavailable sources, invalid imports, failed quality checks, and losing evaluations, is retained in an append-only campaign registry. The registry provides a multiple-testing denominator.

## Data contract

The campaign uses finalized bars only. Each row must retain UTC open/close/availability timestamps, OHLCV, revision, provider/feed, and the original instrument identity. WTI imports additionally declare whether the data represents a specific futures contract or a continuous series; continuous data must state its roll methodology. Missing intervals stay missing. Daily proxy data, synthetic bars, and forward-filled bars are rejected for an intraday campaign.

## Evaluation protocol

For every accepted cohort, Nowcaster uses the existing chronological walk-forward and sealed-final-boundary pipeline. Signals are generated from a point-in-time revision ledger; an execution can occur only after the decision bar is final and available. Each cohort records ordinary and doubled execution costs, coverage, causal-prefix audit, all trial sharpes, Deflated Sharpe, probability-of-backtest-overfitting diagnostics, drawdown, trade count, and retained trial identities.

No result is eligible for more than research-only status unless it passes every existing strict gate: at least 300 closed trades, positive median walk-forward net return and Sharpe, positive doubled-cost return, 99% or higher adjusted Deflated-Sharpe and bootstrap positive-edge evidence, no more than 10% estimated overfitting probability, parameter stability at least 80%, drawdown at most 10%, profit concentration below 50%, positive sealed-final result, complete data/provenance/causal/execution audits, and a material improvement over the comparison cohort. Passing remains evidence, not a promise of future profit.

## User experience

The macOS Strategy Lab will show each campaign asset as `research only`, `unavailable`, or `rejected`; it must never show a long/short recommendation for this campaign. The display must name the exact market identity and explain unavailability—for example, “WTI needs verified intraday contract data”—rather than silently omitting it.

## Safety and non-goals

- No credentials, broker integrations, order submission, or notification promotion.
- No modification of `/Users/james/Library/Application Support/Nowcaster/ProspectiveStudies/study-001-20260908` or its frozen source checkout.
- No claim that an indicator, model, backtest, paper trade, or future study guarantees profitability.
- No WTI backtest until a compliant intraday source is supplied; absence of that source is an honest unavailable result.
