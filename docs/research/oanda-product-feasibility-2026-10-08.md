# OANDA product feasibility checkpoint — 8 October 2026

This is a **data and product-identity check**, not a strategy selection or a
claim of profitable trading. No paper rule is promoted. The already-inspected
April–September market history cannot become a fresh holdout for a revised rule.

The retained practice inventory at
`~/Library/Application Support/Nowcaster/IntradayResearch/inventory.json`
confirms `SPX500_USD` as “US SPX 500” CFD at a 0.05 margin rate,
`DE30_EUR` as “Germany 30” CFD at 0.05, `WTICO_USD` as “West Texas Oil”
CFD at 0.10, and `EUR_USD` as a currency product. These are *account demo
identities*, not verified live-account terms or executable fills. In
particular, the inventory's “Germany 30” label conflicts with OANDA UK's
public [Germany 40 schedule](https://www.oanda.com/uk-en/trading/hours-of-operation/);
do not silently relabel that product or substitute DAX futures prices.

OANDA UK's [hours page](https://www.oanda.com/uk-en/trading/hours-of-operation/)
lists US SPX 500 in Chicago local time, Sunday–Friday 17:01–15:59, and
warns that holidays and daylight saving alter availability. It lists West
Texas Oil separately. The fixed UTC research windows in the exploratory
[historical screen](intraday-sensitivity-2026-10-08.md) are therefore
*provisional*, not a validated broker calendar.

For a GBP-denominated v20 account, OANDA UK's
[charges page](https://www.oanda.com/uk-en/trading/our-charges/) describes a
1% home-currency conversion adjustment for non-GBP settled P&L and charges.
The account-specific gain/loss conversion factors already incorporate that
adjustment; adding another 1% to paper P&L would double-count it. OANDA's
[financing explanation](https://help.oanda.com/uk/en/faqs/financing-costs-uk.htm)
describes index-CFD funding on positions held through the daily 5pm ET
assessment, with variable rates and example rates that are **not current
quotes**. Dividend adjustments can also affect index CFD returns
([OANDA pricing](https://www.oanda.com/uk-en/trading/our-pricing/)).

Next feasibility priority is `SPX500_USD`, because its exact demo label
matches the named public product and its retained historical bid/ask data
have 35,871 bars with no duplicate start times
(recorded in `~/Library/Application Support/Nowcaster/IntradayHistorical/round-20261008/FEASIBILITY.md`).
This prioritizes
*checking execution and data quality*, not its best historical P&L: no tested
rule was positive across all three historical periods. Before even a new
paper-eligible study, verify the exact account session calendar, quote
continuity, current bid/ask spread distribution, index dividend treatment,
financing cut-off, conversion factors, and cost schedule; retain failures and
all candidate comparisons. The running diagnostic collector and frozen
BTC/ETH study remain separate and unchanged.
