# Live Monitor

The Live Monitor is Nowcaster's notification-only market watch mode. It watches a list of stocks and cryptocurrency spot pairs, waits for finalized bars, and may show a hypothetical **long** or **short** setup with an entry range, stop-loss, and two take-profit levels. It also reports when that setup reaches a target, touches its stop, expires, or is invalidated.

It cannot place orders. Every alert is a research prompt that a person must review independently. There is no guarantee of profit, and a historically profitable strategy can lose money when market conditions, costs, liquidity, or execution change.

## What the words mean

| Term | Beginner meaning |
|---|---|
| Long | A hypothesis that the price may rise. |
| Short | A hypothesis that the price may fall. Actual short selling may require borrowing and can have unusually large risk. |
| Entry range | A narrow hypothetical price zone where the setup was evaluated. It is not an instruction or promised fill. |
| Stop-loss (SL) | The price that invalidates the setup and limits its modelled loss. Real fills can be worse during gaps or fast markets. |
| Take-profit (TP) | A modelled price objective. TP1 is the nearer target; TP2 is the farther target. Neither is guaranteed. |
| Confidence | A calibrated evidence score from the sealed historical cohort. It is not the probability of making money. |
| Abstain | The safe result when one or more required checks are missing or weak. |

## How an alert is allowed

The engine never evaluates an unfinished candle. Finalized one-minute bars are combined into a finalized five-minute decision bar, and the prior decision cannot repaint when later bars arrive. A live alert requires all of the following:

1. A complete historical dataset from the same provider, feed, symbol, and interval.
2. At least two currently active strategy components from one complete sealed cohort.
3. Historical promotion, causal/no-repaint, calibration, cost, and positive net-edge evidence.
4. A live direction that matches the direction covered by the sealed calibration.
5. Fresh, healthy quote and bar data with sufficient vote margin and confidence.
6. Feasible spread, volatility, entry, stop, and reward-to-risk geometry.
7. For an Alpaca equity short, current broker-confirmed shortable and easy-to-borrow evidence. Because the notification feed does not provide that evidence, equity shorts fail closed.

If any check fails, the app displays **Abstain** and its reasons. The monitor does not substitute another provider, lower a threshold, extrapolate a probability, or backfill a missing bar.

## Data connections

- **Alpaca** supplies US equity quotes and finalized minute bars. Add paper/data credentials in Nowcaster Settings; credentials remain in macOS Keychain and enter the engine through a private standard-input bootstrap, never command-line arguments.
- **Binance Spot** supplies public `bookTicker` quotes and finalized one-minute klines for symbols such as `BTCUSDT`. A crypto short alert describes a directional hypothesis only; actual short execution depends on a separate margin, futures, or other venue and its rules.

Provider and feed identity are part of the evidence. Alpaca IEX research cannot authorize a SIP alert, and Binance evidence is never spliced with another crypto venue.

## Use it in the macOS app

1. Build historical evidence first in **Strategy Lab**. The monitor will correctly abstain if no strategy cohort has passed every gate.
2. Open **Settings**, enter comma-separated stock and crypto watchlists, and store Alpaca data credentials if stocks are included.
3. Open **Live Monitor**, choose **Start Monitoring**, and grant notification permission in context.
4. Check the text status—not only its color. **Warming Up**, **Reconnecting**, **Data Stale**, and **Attention Required** all block new entry alerts.
5. Read the alert's direction, entry range, SL, TP1, TP2, evidence status, venue note, and timestamp before making any independent decision.
6. Choose **Pause** from the app or menu-bar control to stop the child engine.

The optional login item can reopen Nowcaster. **Resume monitoring at login** works only when both login toggles are enabled and required Keychain credentials are already available. macOS does not let this local process monitor while the Mac sleeps, is shut down, the app is quit, or the network is offline.

## Risk lifecycle

Entry plans are immutable. Finalized one-minute bars monitor their hypothetical risk. If a single bar crosses both a stop and a target, the engine records the stop first, which is the conservative no-repaint assumption. TP1 keeps the setup active for TP2; TP2, stop, expiry, close, and invalidation are terminal. Active state is saved in local DuckDB and recovered after an app restart without replaying old entry notifications.

Notifications never include account identifiers, credentials, or an order button. They use the words *hypothetical* and *review before acting*. Nowcaster has no broker mutation dependency in the Live Monitor module.

## Troubleshooting

- **Qualified cohorts: 0** — ingest enough provider history, run the strategy evaluation, and inspect failed promotion or coverage gates.
- **Live quote unavailable** — wait for the provider subscription or check connectivity and entitlements.
- **Warm-up incomplete** — the matching local history is shorter than a strategy's indicator lookback.
- **Provider/feed mismatch** — research again on the exact feed you intend to monitor; do not merge feeds.
- **Risk/reward infeasible** — the spread, volatility, stop distance, or supported targets do not justify an alert.
- **No notification appears** — confirm macOS System Settings → Notifications → Nowcaster, then keep the app in the background; foreground events remain visible in the Live Monitor list instead of duplicating a system banner.

For reproducible protocol verification without credentials or network access, run `make verify-live-monitor` or `make replay-live-monitor`.
