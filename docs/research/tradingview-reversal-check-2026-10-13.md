# TradingView reversal check — 13 October 2026

Status: exploratory chart research only. This is not the frozen OANDA practice study and is not a qualified trade rule.

## Chart and baseline

- TradingView desktop, `FX:GER30`, standard candles, 15 minutes; available Strategy Tester span shown as 1 July–13 October 2026.
- The initial script showed historical setup labels but no completed trades. Its one-unit order was unfundable with the declared EUR 10,000 starting capital against an index price near EUR 25,300. Changing only the tester's initial capital to EUR 100,000, with one unit and no leverage, exposed the baseline outcome.
- Baseline: 204 closed simulated trades; 101 profitable, 103 losing; 49.51% win rate; net P&L **−EUR 236.36**; profit factor **0.954**; maximum drawdown **EUR 1,128.74**; expectancy **−EUR 1.16 per trade**. Chart settings used EUR 0.50 commission per contract and 10 ticks of slippage. These are illustrative OHLC emulator fills, not account-specific bid/ask execution or a complete CFD cost model.
- The equity curve reached a positive peak and then fell below zero. A plausible-looking run of labels therefore did not demonstrate a profitable rule.

## Predeclared single-rule candidate

Require the ATR-sized reversal to be measured from a swing high/low already established before the confirmation bar. The original rule allowed the confirmation bar's own wick to define the extreme that triggered the signal. The candidate keeps the same session, threshold, entries, stop, target, time exit, and costs. It updates the rolling high/low after the decision.

This change is a causal-interpretability filter, **not** a claim of improved accuracy. The TradingView comparison and completed-trade result are pending because the Mac locked during the Pine Editor test. Do not select or promote the candidate from this note. The free TradingView tier did not permit choosing a custom Strategy Tester date range during this check; any result on the already-inspected July–October span would be exploratory and would require untouched future testing.

No active OANDA or BTC/ETH study source, rule, ledger, position, alert, or order was changed.
