# Day-Trader Decision Stack Design

## Purpose

Extend Nowcaster's existing live paper-signal service into a traceable, automated
day-trader research workflow. It must combine regime, trend, liquidity, order-book,
scheduled-event, and simulated-risk context to explain a fresh paper-research
posture or a disciplined Stand aside. It is not an execution system, personal
recommendation, or claim of profitability.

## Scope and non-goals

- Initial universe remains public Binance spot `BTCUSDT` and `ETHUSDT`; only
  `long_research` and `stand_aside` are valid. Unsupported symbols and all short
  postures fail closed.
- Every derived input is causal: a decision may use only finalized observations,
  retained receipt/availability times, and event-calendar records available at
  decision time. No later price, event revision, or order-book state may rewrite
  an earlier decision.
- The frozen prospective study and its checkout, rules, gaps, losses, alerts and
  positions remain immutable and separate.
- No credentials, broker, account, orders, position tracking, auto-trading,
  qualified alerts, profit target, or profitability claim is added.

## Decision stack

### 1. Causal feature snapshot

For each finalized one-minute observation, produce an immutable bounded snapshot:

- 1m/5m/15m trend direction and strength from retained bars only;
- realized volatility, ATR-normalized range, spread and quote-quality measures;
- top-of-book imbalance only when contemporaneous bid/ask provenance is present;
- session classification and scheduled-event proximity from a versioned local
  calendar snapshot; missing, stale or future calendar data is an exclusion.

Snapshots carry `decision_at`, `available_at`, source identity hashes, and a
feature hash. Invalid inputs yield exclusions, never substituted defaults.

### 2. Regime and setup gates

Classify each symbol as `trend`, `range`, `volatile`, `illiquid`, or `unknown`.
A long-research posture requires trend agreement across configured timeframes,
acceptable spread/liquidity, no scheduled-event blackout, and existing Round 2
candidate/quality gates. Contradiction, low confidence, missing context, abnormal
volatility, or a quality gap produces Stand aside with machine-readable reasons.

The classifier is versioned and protocol-bound. Existing candidate selection and
walk-forward results remain binding; no strategy is promoted from live outcomes.

### 3. Paper position-management simulation

For each published research posture, create a non-executable simulated lifecycle:
entry-zone hypothesis, invalidation, target, maximum holding deadline, and a
time-stamped exit reason (`expired`, `invalidation`, `target`, `regime_change`,
or `time_limit`). Later price observations can close a prior simulated lifecycle
only prospectively; intrabar ordering stays conservative and never uses an
overlapping pre-entry range. No real position, P&L, sizing, or order exists.

### 4. Learning and evaluation

Keep an append-only outcome ledger for completed paper lifecycles and score fixed
protocol variants after outcomes are knowable. Variant comparisons use chronological
walk-forward folds, realistic fixed cost/slippage assumptions, confidence intervals,
drawdown and turnover constraints, and multiple-testing penalties. A variant may be
reported as diagnostic only; live outcome data never changes the registered protocol
or retroactively reweights a published decision. A new selection round requires a
new protocol, retained candidates and a separate report.

## macOS experience

Strategy Lab gains a Decision Context section showing regime, timeframe agreement,
liquidity/volatility state, event blackout status, decision expiry, and the concise
reasons for availability or Stand aside. A Paper Lifecycle section shows only
historical hypothetical outcomes, clearly marked as non-executable. Notifications
remain opt-in, paper-only, and evidence-linked; they never include a price, size,
order, profit, or instruction.

## Failure behavior

- Any stale, missing, non-final, conflicting, unavailable, future-dated, or
  causally unavailable input blocks publication and simulated lifecycle creation.
- A calendar/provider reconnect requires continuous warm-up and emits retained
  evidence; an old snapshot is displayed as stale, not current.
- A failed outcome, drawdown threshold, or insufficient sample does not cause
  parameter adaptation; it records a diagnostic exclusion.
- Native decoding rejects unknown fields, action-shaped payloads, expired context,
  mismatched hashes, and malformed lifecycle records.

## Acceptance criteria

1. Feature snapshots, regime/context records, simulated lifecycle events and
   evaluation results are immutable, bounded, protocol-bound and append-only.
2. Tests prove future, non-final, stale, conflicting, missing-calendar and
   post-decision data cannot create/repaint a posture or lifecycle.
3. Tests cover trend agreement, range/volatile/illiquid/event-blackout abstention,
   conservative barrier ordering, time exits and regime-change exits.
4. Variant evaluation preserves every candidate and fold, applies stated costs and
   rejects promotional claims from insufficient or in-sample-only evidence.
5. The native UI distinguishes current research, Stand aside, stale context and
   historical hypothetical outcomes without execution/broker controls.

## Success criteria

Nowcaster provides a repeatable, transparent paper-research approximation of a
disciplined trend trader's decision process. It helps inspect why an opportunity is
accepted or rejected and how a hypothetical lifecycle resolved, while remaining
honest that no result proves future profitability or executable fill quality.
