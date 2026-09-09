# Live paper study

This is an experiment using public Binance BTCUSDT and ETHUSDT spot prices. The
balances are simulated USDT, approximately USD, not money held for you. Nothing
here sends orders, borrows, shorts, uses exchange keys or unlocks qualified app
alerts. There is no promise of profit.

The earlier inspected history did **not** establish a qualified strategy. Every
previously inspected period, including the old audit's holdout, is development
data. A fixed search of 24 longer-horizon configurations chooses at most one
candidate per asset for observation. A failed historical screen stays visible
and cannot receive a positive study verdict. The full discovery JSON and its
dated report must be retained, including losing configurations and exclusions.

The [8 September longer-holding-period results](holding-period-search-2026-09-08.md)
are available with all 24 trials. None passed; the current pair is diagnostic
only and cannot qualify even if a later short period happens to be profitable.
The [registered live experiment snapshot](live-paper-study-2026-09-08.md) records
the actual study window, operational checks and remaining limitations.

## Register once, then resume

Run from the retained source checkout and its installed environment. Keep that
checkout unchanged for the whole experiment: the collector verifies source,
study scripts, installed Python and calculation/transport package versions,
the full 24-trial discovery and strategy definitions before contacting the feed.
Documentation changes alone do not change the source identity. Use a separate
checkout for subsequent app development.

Keep every study in the same collection directory. A missing or truncated
campaign registry with retained study manifests blocks registration and resume;
it cannot restart the campaign count. Restore the retained registry evidence
before continuing.

```sh
.venv/bin/python scripts/run_prospective_study.py register \
  --discovery /absolute/path/to/discovery.json \
  --directory "$HOME/Library/Application Support/Nowcaster/ProspectiveStudies/study-001"
```

Registration freezes the future start (two minutes ahead by default), exact
90-day end, independent candidates, costs, risk limits and assessment thresholds.
Use `--starts-at 2026-09-09T00:00:00Z` only with a genuinely future UTC time.
Registering over an existing directory is refused. Keep all study directories
under the **same parent**: its append-only `campaigns.jsonl` preserves campaign
numbers and predecessor links. Never remove losing campaigns, registry entries
or the ledger. Changing the source requires a new retained successor study,
not resetting an old study to zero.

```sh
.venv/bin/python scripts/run_prospective_study.py run \
  --directory "$HOME/Library/Application Support/Nowcaster/ProspectiveStudies/study-001" \
  --duration-seconds 21600
```

This runs for six hours. Longer uninterrupted sessions improve coverage; for
the full study the operator can use `--duration-seconds 7776600`. Restarting every
hour can prevent observation of any complete hour, so a heartbeat should leave
a healthy collector running and resume it only after it stops.
Repeat the same command to resume. A single writer lock
prevents competing collectors. Press Control-C to stop; persisted decisions and
fills remain. Stopping or losing connection records an observation gap, cancels
pending entries and marks affected open trades as interrupted. Resuming cannot
invent fills during missed time. A Codex heartbeat may check and resume this
command, but it is not an operating-system daemon. The Mac must be awake and
online, with the separate background collector running. The Nowcaster window
does not need to stay open. Sleeping, network outages and stopping the collector
reduce coverage.
No power settings are changed by this feature.

## Read results

```sh
.venv/bin/python scripts/run_prospective_study.py status \
  --directory "$HOME/Library/Application Support/Nowcaster/ProspectiveStudies/study-001"
```

Open the study's `report.md` or `summary.json` in the local workspace/research
workflow to inspect it alongside the app. The collector publishes these files
atomically at least every minute, including while waiting for a stalled feed.
`status` reads the last published report and includes its age; an old report is
not evidence that collection is healthy. `manifest.json`, `discovery.json`,
`ledger.sqlite` and the parent registry hold the full audit evidence.
The collector section includes its process ID, source checkout, last feed event,
state and any failure category. A monitor should inspect the process as well as
the report age before resuming; an old process ID alone is not proof of health.

Each candidate starts with its **own** 10,000 paper USDT account. Do not sum
accounts into a portfolio. Compare each account's cash, open exposure, net and
stressed P&L, drawdown, closed-trade counts, costs, buy-and-hold comparison and
observable-minute coverage. An unfilled signal is not a trade; an open position
or stale valuation is not a successful closed trade. Warmup and repaired REST
candles provide context only. A decision requires a complete hour assembled
from promptly received live minute candles. Quotes, not candles, supply fills.
Hourly background REST refreshes can fill missing context from the initial
partial hour. They never rewrite already observed live bars or trigger old
decisions, and do not block quote processing or report publication.

`tainted_trades` counts only completed trades that crossed a missing-feed
interval. Read it alongside `open_position_tainted`: an open position can be
gap-tainted while the closed counter is still zero. Its
`open_position_diagnostics` retain the entry, stop-trigger bid, target-trigger
bid, expiry, remaining quantity, prior net exit proceeds and valuation freshness.
The diagnostics are `null` when there is no open position. After the fixed end,
they come from the frozen end-window account even if later public quotes finish
liquidating the live paper position. A stale mark leaves the frozen plan visible,
but its current marked value is stale. Gaps are not interpolated and do not imply
that an exit happened.

Hypothetical entries pay the observed ask plus 5 basis points slippage; exits
receive the observed bid minus 5 basis points. The model charges 10 basis points
per side, independently of anyone's actual fee tier. Stress deducts another
34 basis points of original entry notional per closed trade. Displayed sizes,
quote freshness, spread, lot size and minimum notional limit fills. Maximum
entry exposure is 25%; nominal planned risk is 0.25% of initial paper cash.
Price gaps and interrupted observation can cause larger eventual losses.

Open-position stop and target figures are modeled **total-trade** P&L if all
remaining quantity exits at that bid, after the existing exit slippage and fee
and including net proceeds from any partial exits. They are conditional
diagnostics, not guaranteed fills or evidence that displayed liquidity will be
available. The account equity already marks remaining quantity to net liquidation
value, so these exit costs are not deducted from it a second time. Additional
stress remains a separate deduction on full original entry notional. The
after-cost reward/loss ratio is shown only when target P&L is positive and stop
P&L is negative. Stop triggers can gap, and a profitable modeled target says
nothing about the probability of reaching it. A zero modeled break-even bid
means prior net proceeds have already recovered entry cost; it is not a
recommended price.

There is no positive verdict before the fixed end. Each candidate additionally
needs at least 100 closed trades, 99% observable-minute coverage, no tainted
trades, positive net and stressed P&L, drawdown below 10%, a passed historical
screen and a conservative dependence-aware daily-return check. The moving-block
bootstrap is approximate, with a penalty for all campaigns/candidates; it is
not a calibrated probability of profit. Too little data or inadequate tail
resolution means insufficient evidence. Even `positive_paper_evidence` would
not establish real execution profitability or real-money readiness.

See the [frozen study specification](superpowers/specs/2026-09-08-live-paper-study.md)
for the complete protocol. Retain and link dated historical and live evidence
reports regardless of outcome.
