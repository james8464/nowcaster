# Live Paper Signal Service Design

## Purpose

Add a local macOS service that continuously captures public market data and publishes fresh, paper-only research postures to Nowcaster. It should help a user inspect repeated trend-research opportunities without implying profitability, giving executable instructions, connecting to an account, or modifying the frozen prospective study.

## Scope and non-goals

- The service initially observes public Binance spot `BTCUSDT` and `ETHUSDT` only. It can publish a long research posture or Stand aside; spot short research is unsupported and must fail closed.
- It runs only while the user explicitly starts it in the Mac app. It keeps append-only evidence and resumes safely after interruption.
- It produces a notification only for a materially new, fresh research posture after the user has opted in. Notifications are paper-only and contain no order, broker, size, or personal recommendation.
- It never uses account credentials, connects to a broker, places orders, or turns a posture into a qualified setup. A future broker adapter is an interface only and disabled by default.
- `study-001-20260908` and its checkout, collector, evidence, rules, alerts, and positions remain immutable and separate.

## Architecture

### Live feed and persistence

`LivePaperSignalService` owns one local runner per registered Research Round 2 protocol. Its `PublicSpotFeed` fetches/receives finalized one-minute Binance spot observations, assigns provider/receipt/availability provenance, and appends them through the existing Round 2 quality ledger. It must reject non-final, duplicate-conflicting, stale, late, malformed, or source-mismatched observations.

The service records an append-only `signal-events.jsonl` ledger containing start/stop, provider health, gaps, reconnects, evaluated decisions, posture publication, notification attempt, and notification outcome. It never reconstructs missing observations. A process restart resumes the same directory only if the immutable protocol identity matches; otherwise it refuses and asks for a newly registered round.

### Causal evaluation and cadence

The service evaluates at most once per finalized one-minute observation after its `available_at` timestamp. It derives a bounded Round 2 report and Trend Advisor suggestion from retained causal observations only. It publishes a Long research posture only when every existing quality/candidate/trend gate passes. Otherwise it publishes or retains Stand aside with reasons.

An advisor decision expires no later than its source-freshness deadline. The service suppresses duplicate publications when posture, candidate hash, entry zone, invalidation, target, and expiry are unchanged. It enforces a per-symbol notification cooldown and never sends a notification for stale, gapped, unavailable, rejected, insufficient-data, or expired evidence.

### App and notifications

The macOS app exposes a separate “Live paper signals” control in Strategy Lab. Start/stop is explicit, visibly displays provider health, source age, last evaluation, retained event history, and an available/abstaining state. It never shares control state, events, settings, or models with Live Monitor, Execution Center, broker credentials, or qualified alerts.

The app requests notification permission only after a user turns on paper-only notifications. Each notification says “Paper-only research posture — not a trade instruction,” names the symbol and expiration, and opens the read-only Strategy Lab evidence view. No notification uses “buy,” “sell,” “guaranteed,” “profit,” “entry order,” or an order identifier.

### Future broker boundary

`BrokerExecutionAdapter` is a protocol with no implementation, no credentials, and no app route. It has explicit capabilities and a `disabled` state. A future integration requires a new design, separate risk/execution testing, explicit credentials, and user authorization; no current signal path may import or invoke it.

## Failure handling

- Provider transport failure, stalled feed, clock regression, invalid observation, quality gap, or source mismatch stops new postures and exposes an explicit stale/unavailable reason.
- A notification failure is retained as an event but does not retry a stale posture or generate a new signal.
- Reconnects require the configured continuous warm-up before new research posture evaluation.
- App restart preserves evidence but does not present an old posture as current; it is expired until a fresh, valid decision arrives.

## Testing and acceptance criteria

1. A feed observation reaches the append-only quality ledger with causal timestamps; malformed, future, conflicting, and stale observations are rejected.
2. Evaluation occurs no more than once per finalized causal bar and never uses a post-decision observation.
3. Duplicate, unchanged, stale, gapped, rejected, and expired suggestions do not create notifications; a materially new fresh paper-only posture can create one opt-in notification event.
4. Stop/restart/resume retains events and protocol identity; mismatched protocol restart fails closed.
5. The native UI cleanly distinguishes running, warming, stale, failed, and stopped states, with no order/broker/qualified-alert control or model reference.
6. Notification payloads and UI copy explicitly remain paper-only research and contain no executable trade language.
7. The disabled broker interface cannot be constructed into an execution path.

## Success criteria

The app can automatically collect a live public spot stream and surface fresh, traceable, paper-only research postures while it is running, including honest abstention and provider-health state. It cannot establish or promise profitability; it must remain safe when the proper answer is no signal.
