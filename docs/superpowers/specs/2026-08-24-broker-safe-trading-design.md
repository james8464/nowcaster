# Broker-Safe Trading Design

## Purpose

Nowcaster will gain a staged execution control plane that can shadow decisions, trade through an Alpaca paper account, accumulate frozen forward evidence, and eventually permit a tightly capped live pilot. This design does not claim that software, backtests, paper trading, or statistical confidence can guarantee profit. Real-money submission remains unavailable until every strategy, operational, security, distribution, and manual-arming gate passes.

## Product boundary

The product has three execution environments:

1. **Shadow** records broker-shaped order decisions without submitting them.
2. **Paper** uses Alpaca's paper endpoint and real-time order update stream.
3. **Live pilot** uses Alpaca's live endpoint but is locked unless a fresh readiness receipt and short-lived manual arm are both valid.

Research, shadow, paper, and live records are never relabelled across environments. Learning, parameter selection, calibration, thresholds, and ensemble weights are frozen while a forward-evidence window is active. Any change to strategy version, parameters, weights, code hash, configuration hash, provider, feed, symbol, interval, or cost policy creates a new forward cohort and resets its readiness clock.

## Non-goals

- No profitability guarantee or generic “high confidence” promise.
- No unattended unlocking of live trading.
- No support for a second broker in this implementation wave.
- No use of Binance evidence to authorize an Alpaca order.
- No options, futures, leverage configuration, margin borrowing policy, or derivatives execution.
- No cloud account, remote control plane, telemetry, or credential synchronization.
- No automated increase of live capital limits.

## Architecture

### Execution boundary

The existing causal strategy and deterministic fill simulator remain the research authority. New broker code lives under `src/trading/` and depends on explicit immutable DTOs rather than importing broker behavior into strategy functions.

- `BrokerClient` defines account, clock, asset, order, position, cancellation, and trade-update operations.
- `AlpacaTradingClient` implements that contract for either the fixed paper or live base URL. Arbitrary base URLs are rejected.
- `ShadowBrokerClient` implements the same contract without network submission.
- `TradingSupervisor` owns the run loop, recovery, reconciliation, health, and broker-event ingestion.
- `PreTradeRiskEngine` independently admits or rejects an `OrderIntent` using current broker state, current market state, the frozen research receipt, and explicit limits.
- `ReadinessEvaluator` derives a fail-closed readiness receipt from append-only forward and operational evidence.

The first paper implementation may use the repository Python runtime. Live pilot execution additionally requires the signed bundled engine identity recorded by the native app; an arbitrary Python path cannot arm live mode.

### Database schema v4

Append-only or versioned tables are added without changing v1/v2/v3 natural keys. Version 3 is already the intraday-research migration, so broker execution records use database schema version 4:

- `broker_sessions`: environment, account suffix, code/config hash, start/end, status, heartbeat, and terminal reason.
- `broker_order_intents`: deterministic intent ID, strategy and cohort identity, decision provenance, requested order, risk policy hash, and status.
- `broker_orders`: broker ID, deterministic client order ID, immutable submission request, latest broker status, and timestamps.
- `broker_order_events`: raw event hash, event type, broker/order identity, quantities, prices, broker timestamp, receipt timestamp, and deduplication identity.
- `broker_positions`: reconciliation snapshot, broker quantity/value, local derived quantity/value, and mismatch state.
- `broker_account_snapshots`: equity, buying power, trading flags, PDT state reported by the broker, and receipt timestamp.
- `risk_decisions`: complete input hash, policy hash, allowed flag, rejection codes, limits, and observed utilization.
- `reconciliation_runs`: compared ranges, open-order/position/account results, unresolved mismatch count, and status.
- `trading_health_events`: connection, data freshness, heartbeat, rate-limit, clock, and emergency-state events.
- `forward_evidence_daily`: exact cohort, session/day results, observed and stressed costs, execution quality, drawdown, and data-quality state.
- `readiness_receipts`: cohort and evidence hashes, gate results, issue time, expiry, status, and invalidation reason.
- `trading_arms`: environment, account suffix, receipt hash, effective/expiry timestamps, declared limits, and terminal state. No credential or secret field is permitted.

Broker events are idempotent by broker event/execution identity plus canonical payload hash. Order submission is idempotent by a deterministic client order ID derived from environment, account suffix, cohort, decision hash, side, quantity, and order parameters. After an ambiguous submission response, the supervisor queries by client order ID before any retry.

### Credentials and process boundary

The macOS app stores paper and live key pairs as separate generic-password items in macOS Keychain. UserDefaults stores only non-secret paths, selected environment, and display preferences. Secrets are retrieved only for an explicitly started broker session, passed to the child engine in its environment, redacted from diagnostics, and removed when the process exits. CLI/headless use may read the established Alpaca environment variables, but logs, database rows, snapshots, crash messages, and command arguments must not contain their values.

Paper and live credentials cannot share an environment label. The adapter verifies the returned account and persists only a short suffix for confirmation and audit.

### Broker protocol and recovery

REST establishes account, market clock, assets, open orders, and positions. The Alpaca `trade_updates` WebSocket is the primary event stream. REST reconciliation runs:

- before accepting any new order after startup;
- after reconnect;
- after an ambiguous submission or cancellation;
- periodically while armed;
- before shutdown completes;
- after freeze or flatten.

The state machine handles accepted, new, partial fill, fill, pending cancel, canceled, expired, rejected, replaced, suspended, calculated, and uncommon replace/cancel rejection events. Unknown events are persisted and cause fail-closed freeze until reconciled.

### Risk policy

Every intent is independently checked immediately before submission. Required gates include:

- environment and account match the active session and arm;
- broker account is active and not blocked;
- a current broker clock and fresh finalized market datum exist;
- exact provider/feed/symbol/interval/cohort matches the readiness receipt;
- strategy is frozen, promoted, causally audited, and non-repainting;
- no unresolved reconciliation mismatch or unknown broker event exists;
- global freeze/flatten switch is inactive;
- asset is tradable; live shorts additionally require broker-reported shortable and easy-to-borrow state;
- order is not a duplicate and does not conflict with broker open orders;
- quantity, price, tick/lot precision, buying power, position, gross exposure, turnover, order rate, spread, price collar, daily loss, and drawdown remain within limits;
- entries are marketable limit orders with a bounded price collar; unconstrained market entries are forbidden;
- extended-hours entries are disabled in the first live-pilot policy.

Any missing, stale, malformed, non-finite, contradictory, or unavailable input rejects the order. Broker acceptance never overrides a local rejection.

The initial live-pilot hard ceilings are:

- per-position notional: `min(100 USD, 0.10% of broker equity)`;
- total gross exposure: `min(500 USD, 0.50% of broker equity)`;
- daily loss: `min(25 USD, 0.05% of broker equity)`;
- arm lifetime: 30 minutes;
- no overnight equity position and no extended-hours entry;
- at most one outstanding entry per symbol and a bounded submission rate.

Configuration may make these limits stricter but cannot raise them in the first live-pilot release.

### Emergency behavior

**Freeze** is always available. It blocks new submissions and requests cancellation of outstanding entry orders. Existing positions remain visible and require an explicit next action.

**Flatten** requires separate destructive confirmation. It freezes first, cancels open orders, obtains current positions from the broker, submits bounded close instructions, and reconciles until terminal or timeout. Flatten failure remains prominently unresolved; the UI never claims success from request acceptance alone.

Loss of data, broker authentication, stream authorization, heartbeat, or reconciliation freezes new entries. Automatic flattening on connectivity loss is not the default because an uncertain network cannot prove that close requests arrived.

## Forward evidence and readiness

Readiness is evaluated per exact immutable cohort. The minimum observation gates are:

- equities: 60 completed market sessions;
- crypto: 90 completed UTC calendar days;
- 100 closed paper trades;
- zero unresolved reconciliation mismatches;
- zero causal, prefix-invariance, revision-fidelity, or data-freshness failures;
- no strategy, parameter, code, configuration, feed, or cost-policy mutation within the cohort;
- positive net forward edge under observed paper results and the predeclared conservative live-cost model;
- non-negative doubled-cost result;
- acceptable predeclared drawdown, fold/regime/side stability, bootstrap uncertainty, Deflated Sharpe, PBO, and parameter-neighborhood evidence;
- paper fill quality and modeled slippage error within the predeclared tolerance;
- all required operational sessions terminated cleanly or have adjudicated failures.

Paper fills are never treated as measured live slippage. Readiness therefore authorizes only the capped live pilot, not general capital deployment. Increasing capital is outside this specification and requires a new design based on real pilot evidence.

A readiness receipt includes every gate, exact evidence hashes, code/config/risk-policy identities, issue time, and 24-hour expiry. A new mismatch or health breaker invalidates it immediately.

## Native macOS experience

Add an **Execution Center** to the existing System section using the established SwiftUI tokens, NavigationSplitView behavior, SF Symbols, accessibility identifiers, and non-color status language.

The view contains:

- environment and broker connection status;
- account suffix, market clock, data freshness, and last reconciliation;
- prominent `Research`, `Shadow`, `Paper`, `Live Locked`, or `Live Pilot Armed` state;
- positions, open orders, recent fills, rejections, and broker events;
- risk-limit utilization and rejection reasons;
- forward-evidence duration, trade count, stability, execution-quality, and readiness gates;
- Freeze and separately confirmed Flatten controls;
- paper-session start/stop controls;
- live arming sheet requiring the exact account suffix, explicit maximum-loss acknowledgement, valid readiness receipt, signed-engine identity, and 30-minute expiry.

No button is labelled “Trade with confidence,” “Guaranteed,” or equivalent. The app distinguishes broker acceptance, partial fill, fill, cancellation, and reconciliation.

Keychain settings use SecureField inputs and explicit Save/Replace/Delete/Test actions. Secrets are never read back into visible text.

## Packaging and release

Research builds may remain ad-hoc signed. A tagged production release that exposes live-pilot UI must fail unless all of the following are present and verified:

- Developer ID Application signing identity;
- hardened runtime and secure timestamp;
- all nested executables and the bundled engine are signed;
- notarization succeeds and the ticket is stapled;
- `codesign --verify --deep --strict`, `spctl --assess`, and `stapler validate` succeed;
- archive checksum and software bill of materials are published;
- Python, Swift, fixture parity, secret-history, dependency, and broker-contract tests pass.

The live environment remains unavailable in ad-hoc, development, or externally mutated-engine builds.

## Testing strategy

All behavior changes follow red/green TDD. Network tests use complete official-shape fixtures and controlled transports; CI never contacts or submits to a brokerage account.

Required test categories:

- DTO/schema validation, natural keys, append-only behavior, and migrations;
- REST request/response contracts, fixed endpoint isolation, redaction, retries, and rate-limit handling;
- deterministic client order IDs and ambiguous-submit recovery;
- WebSocket authorization/listen handling and every documented/common broker event;
- duplicate, reordered, delayed, unknown, and partial-fill events;
- startup/reconnect/shutdown reconciliation and position/order disagreement;
- every risk gate at pass, reject, exact-boundary, stale, missing, NaN, and contradictory inputs;
- freeze, flatten, cancellation races, and restart recovery;
- cohort invalidation, forward-day/trade floors, cost stress, and readiness expiry;
- proof that learner or configuration changes reset readiness;
- proof that Binance evidence cannot authorize an Alpaca order;
- Keychain CRUD without secret disclosure;
- native execution-state decoding, accessibility, keyboard behavior, confirmation, and narrow/wide layouts;
- production release workflow refusal without signing/notarization inputs;
- full Python and Swift suites, Ruff, release builds, snapshot parity, secret-history scan, and artifact verification.

## Implementation decomposition

The design is implemented as three plans:

1. **Broker-safe paper execution:** schema v3, broker contract, Alpaca/shadow adapters, event stream parser, supervisor, idempotency, reconciliation, CLI, and paper fixtures.
2. **Capital controls and forward readiness:** risk engine, emergency behavior, forward evidence, readiness receipts, snapshot schema v3, and Execution Center UI.
3. **Hardened live pilot:** Keychain credentials, signed bundled engine identity, live endpoint lock, arming ceremony, hard live caps, release hardening, notarization checks, security documentation, and final verification.

Each plan must finish green and review-clean before the next begins. No plan may weaken a previous fail-closed gate to make later integration pass.

## External completion conditions

The code can implement and test every control, but the following cannot be truthfully completed without user-owned external state or elapsed market time:

- Alpaca paper/live credentials and account eligibility;
- 60 equity sessions or 90 crypto days plus 100 closed paper trades for an unchanged cohort;
- Apple Developer ID certificate and notarization credentials;
- independent security review acknowledgement;
- real live-pilot observations.

Until those conditions exist, the correct shipped state is paper-capable and `Live Locked`.
