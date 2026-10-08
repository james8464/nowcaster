# SPX500 last-half-hour research round

This is a separate, paper-only historical hypothesis. It does not modify the active OANDA account-quote study, select a live rule, or imply a profitable CFD strategy. The motivation is Gao, Han, Li and Zhou's SPY intraday-momentum study; SPY evidence is not evidence for OANDA's `SPX500_USD` CFD.

## Frozen hypothesis, before outcome inspection

- Exact product: OANDA practice `SPX500_USD` CFD, historical bid/ask M5 base candles only. No other asset or broker price is substituted.
- Session: 09:30–16:00 `America/New_York`, regular full sessions only. Require all 78 session bars and a prior full-session 15:55–16:00 close, otherwise abstain. Skip weekends and abbreviated sessions, never fill holes.
- Signal: compare the previous full-session 16:00 midpoint close with today's 10:00 midpoint close; compare today's 15:00 and 15:30 midpoint closes. At 15:30, long if both changes are strictly positive, short if both strictly negative, otherwise abstain. No volume filter.
- Causal fill: the 15:30 bar's close is not an entry price. Enter at the 15:35 M5 open on ask for long, bid for short. Close on the opposite side by 16:00. Apply adverse slippage per side. If a stop and target are both touched inside one bar, take the stop first. A gap through a stop fills at the adverse opening side.
- Fixed protection: stop 0.25% from the slippage-adjusted entry, target 0.5%. No optimization of these distances in this round. Limit to one hypothetical trade/day; no position past 16:00.
- Costs: historical bid/ask spread, adverse slippage (baseline 0.5 index points/side, stress 1.0 points/side), and a 1% conversion charge on absolute net proceeds. No historical account-specific financing or GBP conversion is available; report USD-equivalent exploratory results only, never account GBP P&L or eligibility. Missing cost evidence blocks promotion.
- Chronology: 2022–2023 development; 2024 validation; 2025 sealed final period. The prior April–September 2026 screen is already inspected and is excluded. Preserve every attempted run and source hash. Only inspect the sealed period once after fixing the rule using earlier periods.
- Comparators: no-trade cash baseline, always-long and always-short on the same eligible last-25-minute windows. Report per-day net results, both directions separately, missing days, max drawdown and a 95% daily-block-bootstrap interval. No promotion from historical results alone.

The prior BTC/ETH and current OANDA prospective studies remain separate and unchanged. The minimum prospective gate stays 90 calendar days and 100 closed account-quote paper trades, with positive after-cost daily-block lower bound and reliable coverage. No real orders or advisory alerts are authorized.

Research basis: [Gao et al., *Market Intraday Momentum*](https://smallake.kr/wp-content/uploads/2015/01/SSRN-id2440866.pdf), especially its prior-close-to-10:00 predictor and combined first/penultimate-half-hour rule. OANDA [historical candles](https://developer.oanda.com/rest-live-v20/instrument-ep/) are not account-specific executable prices.
