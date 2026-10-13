# OANDA latest account-candle feasibility — 13 October 2026

This is a **read-only data-source check**, not a new trading rule, backtest, or
profitability result. The running diagnostic session and the registered
14 October experimental paper round remain unchanged.

OANDA documents `GET /v3/accounts/{accountID}/candles/latest` as returning
the latest completed candle for a requested account/instrument/granularity/
price combination. Its price stream is sampled rather than tick-complete.
The [pricing endpoint documentation](https://developer.oanda.com/rest-live-v20/pricing-ep/)
defines both behaviours. OANDA also cautions that historical base-price
candles can differ from account live prices in its
[API troubleshooting guide](https://help.oanda.com/us/en/faqs/rest-v20-api-troubleshooting-guide.htm?Highlight=api).
The account-route candle must still be checked against account quotes; its
route name alone is not proof of an executable quote or identical pricing
segment.

One practice-account request for `DE30_EUR:M5:BA` returned HTTP 200. At
approximately 10:26 UTC, it included a completed 10:20–10:25 UTC candle
and an incomplete 10:25–10:30 candle. The completed candle reported bid
O/H/L/C 25407.6 / 25419.6 / 25400.6 / 25408.6 and ask
25408.8 / 25421.8 / 25401.8 / 25410.8. The retained account stream
contained 290 quotes timestamped inside that completed five-minute window.
Its first observed quote arrived at 10:20:00.392749 UTC and had bid/ask
25403.6 / 25405.8. The stream's observed highs, lows, and closes matched
the returned candle; its first observed open did not. This single interval
shows why a sampled quote-built open need not equal the endpoint candle
open. It does **not** establish typical divergence, full-session coverage,
or which source would yield a profitable decision.

The new research-only reader accepts only a completed, aligned bid/ask M5
candle with exact product identity and a receipt time after close. It
preserves that actual receipt time as `available_at`, binds the record to
the practice account by a one-way digest, and rejects future, malformed,
or mismatched data. It does not feed the current signal or paper-execution
lane. A subsequent, separately frozen round would first need repeated
coverage/divergence measurements and a causal policy for using this candle
as an *indicator input* while hypothetical entry/exit still use later
account bid/ask quotes. It must retain every failed and missing interval.
