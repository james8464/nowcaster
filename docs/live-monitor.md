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

The detail view also shows a probability range, Brier score, calibration error, raw and effective calibration samples, selective coverage, modeled costs, and lower net edge. Lower net edge is the conservative bound after costs and uncertainty; zero or less always means Abstain. These diagnostics describe historical calibration, not a guaranteed win rate.

## How an alert is allowed

The engine never evaluates an unfinished candle. Finalized one-minute bars are combined into a finalized five-minute decision bar, and the prior decision cannot repaint when later bars arrive. A live alert requires all of the following:

1. A complete historical dataset from the same provider, feed, symbol, and interval.
2. At least two currently active strategy components from one complete sealed cohort.
3. Historical promotion, causal/no-repaint, bidirectional calibration, cost, uncertainty, and positive net-edge evidence sealed from development data only.
4. An exact match between the native snapshot cohort, the selected database cohort, and an active, unexpired readiness receipt. The receipt is bound to the current calibration/economic evidence, forward-period evidence, monitor policy, and the exact seven required gates.
5. Fresh, continuous, healthy quote and bar data with sufficient vote margin and confidence. Entry levels use the first eligible provider quote after the decision bar became available.
6. Feasible spread, volatility, entry, stop, and reward-to-risk geometry during the regular exchange session for US equities.
7. For an Alpaca equity short, current read-only broker metadata must confirm that the asset is shortable and easy to borrow.

If any check fails, the app displays **Abstain** and its reasons. Zero qualified cohorts is a safe connected state: quotes and health remain visible, but entry alerts are impossible. The monitor does not substitute another provider, lower a threshold, extrapolate a probability, or infer across a missing bar.

Streaming drift checks compare current feature distributions, predictions, calibration residuals, observed costs, latency, and net edge with the sealed reference. Warming or warning status blocks new entries. Confirmed drift invalidates the readiness receipt, so restarting the app cannot clear the condition.

After a disconnect, pending pre-gap decisions are discarded and quotes cannot silently restore health. A gap of at most 60 one-minute bars is repaired from the provider's read-only historical endpoint and accepted only if every minute is present. Larger or incomplete gaps fail closed and require a new contiguous live window. Any stop or target first observed in repaired data is labelled **delayed observation**; the app never turns a repaired historical bar into a retrospective entry.

## Data connections

- **Alpaca** supplies US equity quotes and finalized minute bars. A read-only metadata request validates each symbol, price precision, tradability, and borrow status. Add paper/data credentials in Nowcaster Settings; credentials remain in macOS Keychain and enter the engine through private standard input, never command-line arguments.
- **Binance Spot** supplies public trades, best bid/ask, depth updates, and finalized one-minute klines for symbols such as `BTCUSDT`; read-only exchange metadata validates symbol status and tick size. The configured spot product is not shortable. A short hypothesis is therefore ineligible for execution on that product; margin or derivatives would require a separate instrument, cost model, data history, and validation cohort.

Provider and feed identity are part of the evidence. Alpaca IEX research cannot authorize a SIP alert, and Binance evidence is never spliced with another crypto venue.

## Use it in the macOS app

1. Build historical evidence first in **Strategy Lab**. The monitor will correctly abstain if no strategy cohort has passed every gate.
2. Open **Settings**, enter comma-separated stock and crypto watchlists, and store Alpaca data credentials if stocks are included.
3. Open **Live Monitor**, choose **Start Monitoring**, and grant notification permission in context.
4. Check the text status—not only its color. **Warming Up**, **Reconnecting**, **Data Stale**, and **Attention Required** all block new entry alerts.
5. Read the alert's direction, entry range, SL, TP1, TP2, evidence status, venue note, and timestamp before making any independent decision.
6. If you acted independently, use **Track Fill** to record the hypothetical fill price so later SL/TP lifecycle updates reflect an explicitly tracked setup. This still cannot place an order.
7. Choose **Pause** from the app or menu-bar control to stop the child engine.

### Creating a readiness receipt

A promoted backtest alone cannot create a receipt. First collect the untouched forward paper evidence required by the policy: at least 60 equity sessions or 90 crypto days, at least 100 closed paper trades, complete reconciliation, positive paper and stressed returns, and passing causal/robustness statistics. Forward periods are stored under the same canonical watchlist cohort identity.

When those records exist, evaluate and persist the short-lived receipt with:

```bash
python -m src.cli monitor qualify \
  --project-root . \
  --stock AAPL \
  --crypto BTCUSDT \
  --interval 5m \
  --stock-feed iex
```

The command does not accept caller-supplied pass/fail statistics. It derives bootstrap and slippage accuracy from closed forward records, reads DSR/PBO/stability/causal values from sealed validation receipts, and binds their hashes into the receipt. Missing or mismatched evidence locks qualification. A locked result lists every failed gate and does not write an active receipt. A successful receipt lasts at most 24 hours; changing the cohort, evidence, costs, calibration, thresholds, feed, or watchlist invalidates it.

The optional login item can reopen Nowcaster. **Start monitoring whenever Nowcaster opens** is a separate explicit preference and requires available Keychain credentials. macOS does not let this local process monitor while the Mac sleeps, is shut down, the app is quit, or the network is offline.

## Risk lifecycle

Entry plans are immutable. Finalized one-minute bars monitor their hypothetical risk. If a single bar crosses both a stop and a target—or crosses a stop on its expiry bar—the engine records the stop first, which is the conservative no-repaint assumption. TP1 keeps the setup active for TP2; TP2, stop, expiry, close, and invalidation are terminal. Active state is saved in local DuckDB, restored into a dedicated native setup list after restart, and is not lost when old diagnostic events roll out of view.

The engine persists the last finalized minute for every provider/feed/symbol. After a disconnect, app restart, or Mac wake it requests the exact missing expected minutes; XNYS nights, weekends, holidays, and early closes are not treated as gaps. A failed or oversized repair retains the old watermark, stays stale, and retries without allowing heartbeats to reopen decisions. Repaired historical stop/target crossings are labeled delayed observations.

Lock-screen notifications never include prices, account identifiers, credentials, or an order button; the detailed plan remains inside the app. Nowcaster has no broker mutation dependency in the Live Monitor module.

## Troubleshooting

- **Qualified cohorts: 0** — ingest enough provider history, run the strategy evaluation, and inspect failed promotion or coverage gates.
- **Awaiting post-finalization quote** — the confirming bar closed, but no causally eligible quote has arrived yet.
- **Readiness receipt required** — refresh research/forward evidence and create a current receipt for the exact unchanged cohort; the app will not reuse a receipt from another snapshot.
- **Warm-up incomplete** — the matching local history is shorter than a strategy's indicator lookback.
- **Provider/feed mismatch** — research again on the exact feed you intend to monitor; do not merge feeds.
- **Risk/reward infeasible** — the spread, volatility, stop distance, or supported targets do not justify an alert.
- **No notification appears** — confirm macOS System Settings → Notifications → Nowcaster, then keep the app in the background; foreground events remain visible in the Live Monitor list instead of duplicating a system banner.

For reproducible protocol verification without credentials or network access, run `make verify-live-monitor` or `make replay-live-monitor`.
