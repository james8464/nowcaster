# Live Market Alert Monitor Design

**Date:** 2026-08-26  
**Status:** Approved  
**Scope:** Native macOS live market monitoring and notification-only trade research alerts

## Purpose

Nowcaster will monitor a user-managed watchlist of US equities and spot crypto in real time. It will notify the user when the existing validated ensemble produces an eligible long or short research setup, when that setup should be closed or invalidated, and where hypothetical stop-loss and take-profit levels lie.

The monitor never submits, replaces, or cancels an order. It cannot promise profit, high confidence, or reliable execution. Confidence is a calibrated historical probability under recorded validation conditions. Missing evidence, unhealthy data, or an infeasible trade plan produces an abstention.

## Operating decisions

- Notification-only; no one-click or automatic order path.
- Configurable watchlist rather than an all-market scanner.
- Finalized closed-bar decisions only. Five-minute bars are the default decision interval; finalized one-minute bars monitor active setup risk.
- The monitor remains active after the main window closes through the main application process and a visible menu-bar extra.
- Launch at login and automatic resume are separate opt-in settings.
- Local notifications only. The monitor cannot operate while the Mac is asleep, offline, shut down, or explicitly quit.
- Existing live-money locks and broker execution boundaries remain unchanged.

## Architecture

The chosen design is a local hybrid:

```text
Alpaca equity stream      Binance spot stream
          \                    /
       provider adapters + bounded REST gap repair
                        |
          finalized one-minute bar ledger
                        |
      deterministic aggregation and strategy inference
                        |
       eligibility gates + trade-level planner
                        |
         append-only alert lifecycle ledger
                        |
      typed JSONL pipe to native Swift monitor actor
                        |
   SwiftUI Live Monitor + MenuBarExtra + UserNotifications
```

The Python engine remains the single source of strategy, ensemble, causal-validation, and risk logic. Swift supervises the signed bundled engine, owns user-facing settings and Keychain access, decodes typed events, and delivers local notifications. This avoids duplicating quantitative logic in Swift.

A Swift-only inference engine was rejected because it would create two implementations of validated strategy behavior. A cloud service and APNs were deferred because they add accounts, infrastructure, recurring cost, remote secret management, and a different security model.

## Provider and provenance rules

### Equities

- Use Alpaca Market Data WebSocket v2.
- The configured feed is explicit (`iex` or `sip`) and must match the historical dataset and promoted strategy evidence.
- One authenticated, multiplexed connection serves the watchlist to respect provider connection limits.
- Market session and asset metadata are checked through read-only Alpaca endpoints.
- A short setup is alert-eligible only when the symbol is currently shortable and easy to borrow.
- Extended-hours and overnight equity entry alerts are disabled.

### Crypto

- Use official Binance Spot WebSocket streams so live provider identity matches the existing Binance historical research datasets.
- App labels retain their exact mapping to provider symbols, for example `BTC-USD` to `BTCUSDT`; the UI discloses that this is a USDT-quoted Binance spot market rather than composite USD.
- Connections rotate before the provider's 24-hour limit and respond to ping frames.
- Crypto short alerts are explicitly labelled venue-dependent because the spot data feed does not establish whether the user has access to a short-capable venue.

### Shared rules

- Provider, venue/feed, provider symbol, interval, UTC bar bounds, finalization, revision, and payload identity are retained with every decision.
- Feeds and venues are never spliced. A strategy promoted on one provider/feed cannot infer from another.
- Credentials stay in macOS Keychain and pass to the engine through a private standard-input bootstrap message. They never enter command arguments, logs, persisted configuration, notification payloads, or Git.
- REST is used only for bounded initial warm-up and gap repair. Every repair is reconciled against stream sequence and timestamp state.

## Bar finalization and no-repainting contract

Provider events are normalized into typed trades, quotes, and bars. The monitor rejects malformed, non-finite, non-UTC, impossible-OHLC, negative-volume, unsupported-symbol, and excessively future-dated data.

Only a finalized provider bar may enter the immutable bar ledger. Five-, fifteen-, sixty-, and 240-minute bars are deterministically aggregated from finalized one-minute bars using UTC bounds plus the relevant equity exchange calendar. A short settlement grace permits normal provider arrival latency. A decision cannot exist before its bar's end and finalization time.

The decision identity includes provider/feed, symbol, interval, bar end, dataset/cohort hash, strategy versions, and configuration hash. A late provider revision creates a recorded revision and future recomputation context; it never mutates or retracts an already delivered alert. Prefix-invariance tests must prove that appending future bars does not alter prior decisions.

On reconnect, the monitor requests the missing bounded range, validates continuity, and restores warm-up state. It never emits retrospective entry notifications. If an already tracked setup crosses a risk level during a gap, the first healthy post-gap state is recorded as a delayed risk observation with the gap disclosed.

## Signal eligibility

The live monitor consumes only immutable strategy versions and evidence weights from a frozen or paper-qualified cohort. Development and learning candidates cannot enter live alerts directly.

An entry alert requires all of the following:

- a current valid dataset/cohort identity and unexpired readiness evidence;
- strategy promotion gates for sample size, purged walk-forward evidence, calibration, robustness, costs, stability, and economic evidence;
- the ensemble's minimum breadth, vote margin, calibrated probability, and positive net edge after cost and uncertainty buffers;
- a finalized current bar, complete warm-up, no unresolved provider gap, and data freshness within policy;
- acceptable spread, price precision, market/session state, and asset feasibility;
- no existing active setup for the same symbol and interval;
- an entry zone, structural/volatility stop, and supported targets that satisfy the risk policy.

Any failed condition yields `abstain` with machine-readable reasons. The current bundled research snapshot promotes no decision-ready strategy, so a correctly configured monitor may remain in abstention until provider-backed research passes the gates.

## Trade-level planner

The planner creates research levels, not instructions or orders.

- The entry reference is the first eligible live quote after the confirmed decision bar.
- The entry zone includes the displayed bid/ask spread, configured slippage buffer, tick size, and a bounded maximum chase distance.
- The long stop lies below both the validated structural invalidation and the configured ATR noise allowance. The short stop is symmetric above the entry.
- The stop distance must be finite, positive, within volatility-relative and policy ceilings, and large enough to exceed spread plus slippage noise.
- `R` is the absolute distance between the executable entry reference and stop.
- TP1 uses the nearest supported structure or expected-move objective at or beyond 1R.
- TP2 uses a further supported objective at or beyond 1.5R.
- If supported targets do not meet the minimum reward-to-risk policy, the engine abstains.
- Price levels are rounded outward using provider tick-size rules so rounding never makes risk appear smaller.
- Every plan includes an expiry and a textual plus structured invalidation reason.

The policy is configuration-driven and versioned. Asset-class defaults may differ, but no setting may bypass data health, causal, cost, or promotion gates.

## Alert lifecycle

The durable state machine is:

```text
watching -> candidate -> entry_alerted -> tracked | untracked
tracked/untracked -> target_1 | target_2 | stopped | closed | invalidated | expired
```

Transitions are monotonic and idempotent. Stable event identifiers prevent duplicate alerts after replay, crash, or restart. A setup may not silently change side; an opposite eligible decision closes or invalidates the existing setup before a new candidate can form.

The user may select **Track This Setup** and enter their actual fill price. This stores local tracking state and recalculates displayed distances; it never calls a broker or creates an order. Without tracking, lifecycle notifications say that the research setup ended rather than claiming that a user position was closed.

Close or risk transitions occur when:

- the stop or a target is crossed by healthy finalized one-minute data;
- the eligible ensemble reverses or its expected net edge becomes non-positive;
- a strategy-specific invalidation occurs;
- the setup expires or the applicable equity session ends.

Stale, disconnected, or unresolved-gap data produces a separate `monitoring_unavailable` risk notification for active tracked setups. It does not fabricate a close signal from uncertain data.

## Native macOS experience

### Live Monitor destination

The SwiftUI sidebar gains a **Live Monitor** destination containing:

- a prominent monitoring state and Pause/Resume control;
- provider/feed identity, last finalized bar, latency, gap, and connection health;
- editable stock and crypto watchlists with normalization and subscription-limit validation;
- active, recent, and abstaining setup views;
- entry zone, SL, TP1, TP2, expiry, confidence, net edge, and evidence details;
- chronological alert and health history;
- **Track This Setup** using an optional actual fill price;
- clear notification-only and market-data limitations.

### Menu bar and login

A SwiftUI `MenuBarExtra` remains visible while monitoring is enabled. Its icon and accessible label distinguish healthy, warming, stale/reconnecting, paused, and failed states. It provides status, active-setup count, the most recent alert, Pause/Resume, Open Nowcaster, and Quit.

`SMAppService.mainApp` implements an opt-in **Start at Login** setting. **Resume Monitoring at Login** is a second opt-in. Background activity is never hidden: the menu-bar extra is present whenever the monitor is running.

### Notifications

The app requests `UserNotifications` permission in context when the user enables alerts and re-checks authorization before delivery. Notification categories are entry, target, stop, close/invalidation, and monitoring health. They contain no order actions. Selecting a notification deep-links to its setup.

Notifications are concise, deduplicated, and coalesced. Foreground events update the in-app feed and badge without an unnecessary system banner. A privacy setting can hide prices and levels on the lock screen. Quiet hours affect entry alerts; active tracked risk alerts remain enabled unless the user explicitly disables that category.

## Process supervision and protocol

`LiveMonitorService`, a Swift actor, starts the exact signed bundled engine as a long-lived child process. The app sends one bounded configuration/bootstrap JSON object through standard input. The engine writes newline-delimited typed events to standard output and diagnostics without secrets to standard error.

Protocol events include schema version, event ID, event type, provider time, receive time, symbol, feed, payload, and health sequence. Event types cover ready, heartbeat, quote, bar finalized, decision, lifecycle transition, notification request, provider health, configuration rejection, and fatal error.

The Swift supervisor enforces payload-size, line-length, nesting, numeric, enum, timestamp, and event-sequence limits. It terminates the child on protocol corruption, excessive output, signature mismatch, or heartbeat timeout. Pause performs an orderly shutdown and records the terminal reason. App termination also terminates the child.

The monitor subsystem exposes no `BrokerClient`, `submit_order`, `cancel_order`, or mutation endpoint. Static dependency tests and controlled HTTP transport tests enforce that boundary.

## Persistence and recovery

DuckDB stores normalized finalized bars needed for bounded recovery, monitor sessions, health events, immutable decisions, alert setups, lifecycle transitions, notification receipts, and user tracking adjustments. Raw high-frequency events use a bounded external cache and retention policy rather than unbounded database growth.

Startup recovery replays the durable ledger, rebuilds the in-memory state machine, reconciles the watchlist and cohort identity, and then reconnects providers. No entry notification is redelivered. Active setups are marked warming until continuity and freshness are re-established.

## Error behavior

- Authentication or entitlement failure: provider unavailable; no affected alerts.
- Subscription limit: reject the excess watchlist change and preserve the last valid configuration.
- Stale data, sequence gap, or clock skew: freeze affected inference and display the exact reason.
- Unsupported symbol/feed/interval: reject configuration before starting.
- Engine crash or heartbeat loss: change status to failed, notify once if tracked setups exist, and require bounded supervised restart.
- Notification denied: monitoring continues, the app shows in-app events, and Settings offers a route to System Settings.
- Mac sleep/wake: record the gap, warm/backfill, suppress retrospective entries, then resume only after health gates pass.
- No eligible evidence: remain connected and display abstention reasons; never lower thresholds automatically.

## Testing and verification

All behavior is developed test-first.

Python coverage includes typed models, provider frame fixtures, reconnect/rotation, ping handling, warm-up and gap repair, finalized aggregation, exchange sessions, causal timing, prefix invariance, strategy/cohort eligibility, level invariants, tick rounding, alert state transitions, replay/idempotency, persistence recovery, stale-data breakers, and the no-order architectural boundary.

Swift coverage includes protocol decoding limits, supervisor lifecycle, heartbeat timeout, settings persistence, watchlist validation, notification authorization and deduplication, foreground presentation, privacy copy, deep links, tracking behavior, menu-bar health presentation, accessibility, and deterministic UI fixtures.

An end-to-end deterministic replay drives recorded historical provider sessions through the exact live monitor command and native event decoder. Verification also runs the complete Python suite, lint and format checks, complete Swift suite, app and engine bundle builds, manifest and code-signing checks, secret scans, SBOM generation, and existing release gates.

Optional credentialed provider smoke tests remain separate from deterministic CI and never log secrets or assert provider availability as a product guarantee.

## Documentation and release posture

README, data-provider, privacy, architecture, and live-readiness documentation will explain setup, feed entitlements, watchlists, notifications, background limitations, levels, abstention, and troubleshooting in beginner-friendly language.

The release remains research and notification software. It must not claim reliable profitability, guaranteed confidence, financial advice, automatic execution, or that a notification proves a trade is suitable for the user. Real-money execution remains independently locked behind the existing controls and is not expanded by this feature.
