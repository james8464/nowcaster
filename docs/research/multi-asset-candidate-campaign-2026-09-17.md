# WTI Candidate Campaign — 17 September 2026

## Outcome

WTI crude oil is recorded as **unavailable for research**, not as a failed or profitable strategy. The campaign is separate from the frozen BTC/ETH prospective study and did not alter its source, ledger, losses, gaps, rules, or end date.

| Field | Value |
|---|---|
| Campaign | `wti-intraday-2026-09-17` |
| Asset identity | NYMEX WTI (`CL`); exact delivery-month contract still required |
| Intended interval | Five minutes |
| Candidate families | volatility-scaled trend, Donchian breakout, Bollinger/Keltner squeeze, RSI reversal |
| Campaign hash | `b673f0a08893718eda631c930821666f18ba02fa24e1f1d8b08573f7932dffd6` |
| Receipt hash | `441e5f4b1cc4c951903013c58388f3ade8301f9edb2ca4d47ae0b74628cda1ba` |
| State | `unavailable` |

The retained reason is: “WTI needs verified intraday contract data before research can begin.” The receipt is append-only under the local Nowcaster application-support directory, outside the repository. It contains no account credentials and starts no collector, monitor, notification, or order flow.

## Required evidence before a WTI replay

A future intake must provide a licensed or otherwise verified, finalized intraday dataset for one named NYMEX WTI contract (or a continuous series with an explicit roll methodology), its CME Globex session calendar, UTC availability timestamps, revisions, OHLCV, documented bid/ask cost assumptions, and coverage gaps. Daily proxy prices, synthetic bars, and forward-filled intervals are rejected.

Every source assessment is retained as `available`, `unavailable`, or `rejected`. An accepted dataset would still have to pass the existing point-in-time walk-forward, sealed-final, cost-stress, causal/no-repaint, multiple-testing, trade-count, drawdown, and stability gates. None of those gates can prove future profitability or authorize a real-money trade.
