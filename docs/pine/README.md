# TradingView chart companion (research only)

The [DE30 opening-range strategy](nowcaster-de30-or15-chart-strategy.pine) is a
visual, **unqualified** chart experiment. Paste it into TradingView's Pine
Editor on a **standard OANDA:DE30EUR five-minute chart**, then choose **Add to
chart**. It uses the first three complete bars from 07:00 UTC as the opening
range. Only the confirmed close of the 07:15–07:20 bar can propose a long,
short or no trade. It displays an indicative entry, stop at the opposite side
of the opening range, 2R target and a one-hour/session time exit. A missing
first bar or later chart gap suppresses the decision. It has no access to
Nowcaster's OANDA practice account or order API.

TradingView's Strategy Tester can display its *chart-emulator* results. The
script uses one illustrative contract, 0.5 EUR commission per contract and
10 ticks of slippage. These are **not verified broker costs** or an account
bid/ask fill model; the next-bar entry may differ from the close printed on
the label. A market close requested at the time cutoff can fill on a later
bar. Chart history, session availability, and revised provider bars can also
differ from the practice feed. Do not treat its net return or win rate as
evidence that a real trade would be profitable. Free TradingView history is
limited; do not assume the Strategy Tester has every historical five-minute
bar.

The script's `alert()` call emits **one decision JSON object** for each
complete 07:20 UTC opening-range decision. An alert is optional. If you
create one, select the script's **alert() function calls only** condition (not
the strategy's order-fill alerts) and
save each message as one line in a local JSONL file. Do not route it to a
broker, copy-trading service, or order-capable webhook. An alert saved after
07:20 cannot recover earlier messages; an absent message is a missing
observation, not an abstention.

To compare decisions (not prices or fills), run the read-only comparator
after both sources have recorded the same dates:

```sh
python scripts/compare_pine_nowcaster.py \
  --pine-alerts /absolute/path/to/tradingview-decisions.jsonl \
  --account-session '/absolute/path/to/retained/Nowcaster/session' \
  --output /absolute/path/to/new-parity-report.json
```

The output **must be outside** the retained account session and must not
exist yet. The comparator validates the account journal's hash chain,
matches only the exact DE30 07:20 UTC opening-range decision, and reports
direction agreement, disagreement, abstentions and missing observations.
Late account abstentions within the same five-minute decision window are
retained with their delay and flagged separately; they are **not** counted as
timely agreement. A journal timestamp alone cannot prove exact bar identity
after that window, so later records are not silently matched to 07:20.
It never compares the chart's indicative entry/exit to executable account
quotes, changes an active round or qualifies either strategy. A TradingView
alert needs manual export/capture; this project does not request credentials
or silently read the user's TradingView account.

The older [multi-market visual indicator](nowcaster-intraday-companion.pine)
is a separate diagnostic overlay, not this strategy or the account-quote
source of truth.

## Reversal Pro observation and causal alternative

The installed, read-only Reversal Pro v3.0 script was inspected on 13 October
2026. Its `REVERSAL` label uses an earlier recorded pivot bar after a
reversal threshold confirms that pivot. The earlier label position makes a
historical chart look more predictive than a real-time observer could have
been. This is *backplotting*, a form of misleading historical presentation;
it does **not**, by itself, establish that its real-time alerts change after
being sent. See the [inspection note](reversal-pro-assessment.md).

The [original causal reversal strategy](nowcaster-causal-reversal-chart-strategy.pine)
is a separate, **unqualified** research hypothesis. It does not reproduce
Reversal Pro's proprietary rules. It supports only standard OANDA:DE30EUR
five-minute and FXCM FX:GER30 fifteen-minute charts. These are separate
chart-research modes, not interchangeable broker execution feeds. It uses
confirmed closes at the selected interval, a fixed two-ATR reversal threshold,
and draws a marker on the *confirmation* bar only. It suppresses entries on
other instruments or intervals, nonstandard charts, detected intraday bar
gaps (checked against the selected interval), or the final bar whose close
reaches the session end. It requests its time exit on the current confirmed
bar rather than carrying a pending market exit into the next session. Missing
chart bars can still delay an exit. Its chart-emulator entries, stops, targets,
and costs are illustrative, not account bid/ask fills. FXCM chart results must not
be compared directly with OANDA practice-account fills. It is not connected
to Nowcaster's live paper ledger, and should not be copied as real-money
advice. The frozen BTC/ETH and OANDA studies are unaffected.
