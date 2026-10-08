# No tested intraday rule stayed positive across three historical periods

An exploratory screen of the retained OANDA practice historical bid/ask candles found **no product, rule and direction with positive modeled net P&L in all three chronological periods**. No rule is selected for the live paper service. The live account-quote monitor remains diagnostic; these candles cannot be relabelled as trades the account could have made.

The screen used all four retained products, four fixed rule IDs and long/short directions, preserving all 32 comparisons. Each cell is modeled GBP net P&L followed by closed hypothetical trades in parentheses. It is not a percentage return or a probability of winning.

| Exact product | Rule | Side | Apr–May development | Jun–Jul validation | Aug–Sep sealed |
|---|---|---|---:|---:|---:|
| DE30_EUR | Opening range 15 | Long | +16.29 (7) | +12.12 (9) | −42.37 (8) |
| DE30_EUR | Opening range 15 | Short | +0.98 (6) | +10.25 (6) | −4.99 (6) |
| DE30_EUR | Opening range 30 | Long | −3.84 (4) | −3.46 (4) | −11.34 (2) |
| DE30_EUR | Opening range 30 | Short | +18.55 (5) | −3.17 (3) | +4.29 (7) |
| DE30_EUR | Trend pullback | Long | −31.86 (30) | −38.48 (39) | −26.76 (32) |
| DE30_EUR | Trend pullback | Short | −8.07 (33) | +33.95 (38) | +6.45 (36) |
| DE30_EUR | Range reversion | Long | −22.93 (39) | −7.46 (45) | −65.08 (42) |
| DE30_EUR | Range reversion | Short | +24.85 (39) | +21.04 (42) | −30.09 (43) |
| SPX500_USD | Opening range 15 | Long | +5.11 (5) | −13.70 (13) | −8.53 (6) |
| SPX500_USD | Opening range 15 | Short | +10.77 (4) | −11.53 (6) | −3.04 (4) |
| SPX500_USD | Opening range 30 | Long | +6.71 (13) | −48.76 (16) | −6.92 (8) |
| SPX500_USD | Opening range 30 | Short | −6.84 (12) | −16.75 (11) | +16.47 (14) |
| SPX500_USD | Trend pullback | Long | −8.30 (28) | +21.69 (23) | −19.76 (19) |
| SPX500_USD | Trend pullback | Short | −13.86 (14) | +56.97 (20) | +15.67 (24) |
| SPX500_USD | Range reversion | Long | +3.87 (36) | −16.63 (37) | −8.73 (37) |
| SPX500_USD | Range reversion | Short | −1.95 (40) | −32.71 (34) | −5.10 (37) |
| EUR_USD | Opening range 15 | Long | +1.09 (3) | −13.82 (7) | −0.70 (7) |
| EUR_USD | Opening range 15 | Short | −7.43 (7) | −5.22 (6) | +3.26 (4) |
| EUR_USD | Opening range 30 | Long | −0.45 (4) | −2.49 (5) | −1.46 (8) |
| EUR_USD | Opening range 30 | Short | −4.77 (4) | −6.34 (4) | +4.59 (3) |
| EUR_USD | Trend pullback | Long | +1.18 (40) | −12.87 (32) | −20.86 (32) |
| EUR_USD | Trend pullback | Short | −9.44 (34) | −9.50 (42) | −31.33 (37) |
| EUR_USD | Range reversion | Long | −23.30 (43) | −28.73 (45) | −11.11 (42) |
| EUR_USD | Range reversion | Short | −19.39 (42) | −6.61 (45) | −15.38 (42) |
| WTICO_USD | Opening range 15 | Long | +44.88 (6) | −80.78 (7) | +5.50 (2) |
| WTICO_USD | Opening range 15 | Short | +35.97 (2) | −4.93 (5) | −5.89 (5) |
| WTICO_USD | Opening range 30 | Long | −45.11 (8) | +4.08 (2) | +42.29 (6) |
| WTICO_USD | Opening range 30 | Short | +50.00 (3) | −26.86 (6) | −28.78 (4) |
| WTICO_USD | Trend pullback | Long | −69.11 (25) | −36.79 (24) | +19.65 (28) |
| WTICO_USD | Trend pullback | Short | +1.84 (20) | −112.69 (27) | −41.51 (14) |
| WTICO_USD | Range reversion | Long | +139.84 (34) | −166.47 (36) | −173.04 (32) |
| WTICO_USD | Range reversion | Short | −23.75 (36) | −116.99 (38) | −21.87 (35) |

## What the comparison can and cannot establish

The source is the four retained JSONL files and SHA-256 hashes in `/Users/james/Library/Application Support/Nowcaster/IntradayHistorical/round-20261008/FEASIBILITY.md`. The requested window is 1 April through 1 October 2026. The screen used `replay_session` with a confirmed-bar decision, following historical bid/ask open for entry, adverse bid/ask stop and target, and stop-first handling when a candle touched both. It allowed at most one trade per instrument/session. Independent sessions each started with £10,000 paper equity and a 0.25% cost-inclusive risk cap; the displayed amounts are sums, not a compounded account curve.

The stage windows were fixed before calculating outcomes: April–May, June–July, and August–September. August–September is now inspected and **cannot be reused as untouched confirmation for a revised strategy**. The provisional UTC session windows were 07:00–20:00 for DE30/EUR_USD and 13:00–20:00 for SPX500/WTICO. A day with a missing five-minute interval inside its window was excluded, never filled in. Complete sessions were 40/45/43 for DE30, 41/43/42 for SPX500, 43/45/43 for EUR_USD, and 41/43/42 for WTICO across the three stages; respectively 0/0/0, 2/2/1, 0/0/0 and 1/2/1 observed days were incomplete. A wholly absent weekday and exact broker holiday/DST session calendars were **not** verified in this exploratory calculation.

The cost scenario is deliberately favorable and **not** an eligibility record: spread is present in historical bid/ask prices, but extra slippage, commission and financing were set to zero. EUR→GBP used a fixed 0.85 and USD→GBP a fixed 0.75, with a 1% conversion charge. Historical GBP conversion varied in reality, and WTICO financing terms especially need exact verification. OANDA's [pricing definitions](https://developer.oanda.com/rest-live-v20/pricing-df/) distinguish account gain, loss and position-value conversion factors; these were not available as a historical per-bar series here. Its [historical-pricing caveat](https://help.oanda.com/us/en/faqs/rest-v20-api-troubleshooting-guide.htm?Highlight=api) also prevents treating the candles as account-specific prospective quotes. Results are a sensitivity screen, not a registered selection round, uncertainty estimate, executable-fill study, or proof that any rule is profitable.

The striking apparent development gain for WTICO range-reversion long became losses in both later periods. DE30 opening-range-15 long and range-reversion short also reversed in the sealed period. SPX500 trend-pullback short was positive in the later two periods but lost in development; promoting it now would select on information the screen was supposed to test. The next hypothesis round should keep these failures, verify exact costs/hours and historical GBP conversion, and specify a distinct rule or market-quality filter before touching the later-period data again. The current live paper study remains unchanged.
