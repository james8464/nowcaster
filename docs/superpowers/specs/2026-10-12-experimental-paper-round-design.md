# Separate experimental paper round for the OANDA practice desk

## Intent and observed fault

Nowcaster should show what an intraday trader *would have decided and done* on fresh OANDA practice bid/ask quotes, including abstention, complete hypothetical entries and exits, and net results. The goal is to discover a durable after-cost edge, not to create a prescribed number of trades or claim that a paper result is safe to copy with money.

The installed collector currently receives quotes but has no paper execution in its app-driven path: `intraday_service_entry.py` instantiates `LiveIndicatorSession` and an empty `PaperJournal`, but never invokes `LivePaperRuntime`. Its daily manifest also gives every rule a zero selection hash, which deliberately prevents paper entries. On 12 October 2026 the retained aggregate report showed 959 decisions, zero closed trades, and zero paper P&L. Its trend rule needs 62 consecutive five-minute bars; retained quote gaps and restarts repeatedly interrupt that history. These are separate causes. No eligibility or profit claim can be inferred from a healthy current quote alone.

The prior four-product, 32-side/rule historical screen selected no rule. A separate SPX500 closing-momentum development test also failed. Those losses and all active/frozen study records remain intact. A new *experimental* round may simulate trades to test execution and observe a preregistered hypothesis, but it is not a workaround for selection or a qualified signal.

## Study boundaries

- Do not change the frozen BTC/ETH checkout, its protocol or records. Do not retune, replace, delete, or backfill any active OANDA diagnostic day, quote gap, decision, or report.
- Create a distinct immutable experimental manifest and append-only ledger under a separate study directory. It identifies the exact practice account by a one-way digest, OANDA broker symbol/product, source and code hashes, round ID, hypothesis, direction, session hours, quote/cost/conversion policy, risk limits, and start/end timestamps. A revised rule or cost policy starts a new round; earlier rounds remain reportable.
- The existing zero-selection-hash gate remains authoritative for *qualified* live signals. The experimental path uses an explicit, different mode and displays `Unqualified experimental paper trade` on every ticket and notification. It cannot produce a real-money recommendation or order.
- No OANDA order or live-account endpoint is imported or called. The Keychain practice token is not stored in a manifest, journal, UI snapshot, command argument, or Git file. Preserve the current practice-only endpoint allowlist.
- Do not silently activate a new study during the running diagnostic day. Deploy at a session boundary, record any collector restart as a gap, and keep both study identities visible in the app.

## Quote-to-decision-to-paper path

One supervised practice quote stream feeds an append-only raw-quote journal and separate per-round consumers. The experimental consumer accepts only exact-product, tradeable bid/ask quotes with UTC provider and receive timestamps, a unique source key, and bounded lag. It builds five-minute account-sampled bars; a bar is available only after its close and a later sealing quote. Historical OANDA base-price candles never warm prospective account-quote decisions or supply fills.

Retain previously confirmed bars through a reconnect for audit, but mark the missing interval and prevent any rule from crossing it when its declared lookback requires continuity. Never synthesize a bar, interpolate a quote, or treat a retained pre-gap bar as fresh. Show per-product feed age, completed-bar count, longest valid lookback, gap count, and the precise abstention reason. A rule with a shorter declared lookback can resume only after its own complete post-gap window; the existing 62-bar trend rule still needs 62 valid consecutive bars.

Each experimental round freezes at most one hypothesis per exact broker product, with long and short direction declared separately. Selection is made *before* that round's live returns are seen and is logged with its rationale and every competing attempt. Initial candidates can be diagnostic even if historical results failed, but must be clearly identified as such and cannot enter the qualified signal lane. A decision is recorded once per sealed bar. An entry is assessed on a later quote, with the correct ask for long and bid for short, spread and adverse slippage. If quote, session, conversion, cost or risk evidence is missing, record no trade instead of substituting a convenient value.

Use the existing `LivePaperRuntime` and ledger contracts where possible, with an adapter from sealed account bars and next-quote decisions. Do not create a second P&L formula. A paper ticket records symbol/product, side, decision and entry times, entry condition and price, units, notional, effective leverage and estimated margin, stop, target, time exit, expected spread/slippage/commission/financing/conversion, risk budget, reason, evidence hashes, and its unqualified status. A subsequent account quote manages or closes it on the adverse side. Stop-first ordering, gap handling, and session-end behavior remain conservative; an unavailable exit leaves an unresolved position, not a fabricated close.

Risk is capped at 0.25% of paper equity per entry, a 1% realized daily-loss stop, 5% drawdown halt, one position per product, no overlapping contradictory positions, and at most 1.0x aggregate effective notional leverage. A source-backed product cost record and timestamped GBP conversion are mandatory before paper entry. The cost model and stress case are frozen in the round manifest. A missing component is `unknown` and blocks entry; it is never silently zero. The app never opens a real position or claims a hypothetical stop exists at the broker.

## Research and qualification

The experimental paper cohort answers whether the signal/execution plumbing works and what happened prospectively. It does not retroactively rescue a failed historical screen. In parallel, use *separate* registered research rounds to test new hypotheses on chronological development data, retain every attempted asset/rule/parameter and loss, verify exact product hours and source-backed costs, then leave a sealed period untouched until the development gate passes. Include no-trade, buy/hold or relevant session baseline, stressed fills, drawdown, and daily-block uncertainty. No selection can use the cohort's future returns or retune that cohort.

A rule can be marked `prospectively supported for further evaluation` only after its independent historical gate and an immutable practice-account forward period of at least 90 calendar days and 100 closed trades, reliable eligible-session coverage, positive net expectancy with a positive 95% daily-block-bootstrap lower bound, and compliance with the predeclared loss limits. Failure, an interrupted feed, or a smaller sample stays visible and cannot be renamed success. Neither status means safe real-money copying; that is a separate decision outside this feature.

## Mac app and reporting

The Trade Desk separates `Feed`, `Experimental paper activity`, and `Evidence`. It shows the exact block when there is no trade: unselected rule, incomplete post-gap history, session closed, non-tradeable/stale quote, unknown cost/conversion, risk halt, or simply no setup. A fresh hypothetical ticket and material paper-management/exit event may send an opt-in local notification marked `Experimental paper`; never notify on stale data or present a diagnostic setup as a qualified recommendation.

Build reports only from immutable ledgers. Display all decisions, attempted entries, rejected entries, opens, closed wins/losses, unresolved positions, gross and net P&L, costs by component, expectancy, win rate with denominator, profit factor when defined, drawdown, feed/eligible-session coverage, and per-product/rule/side breakdowns. Show chronological tickets and exits. Zero trades means `not measured`, not profitable or breakeven. Cross-round totals never replace round-specific evidence.

## Failure behavior and verification

Network interruption, quote gap, process crash, disk error, retained-identity mismatch, or unavailable conversion must fail closed for new entries. Keep an open paper position unresolved or manage it from later fresh quotes according to its frozen policy. A restart must replay and verify retained state before accepting a new quote; no duplicate decision, entry, or exit is allowed.

Test the complete path with deterministic account-quote fixtures: delayed sealing quote, later-entry quote, both long and short bid/ask accounting, missed stop, same-observation stop/target, partial or absent conversion, cost unknown, gap/restart recovery, exact identity mismatch, risk halts, and zero-trade reporting. Assert prefix invariance by changing future quotes without changing earlier decisions. Exercise the installed macOS app from the user's perspective for start/pause, background collection, no-trade reasons, ticket, position, closure, report, and notification labels. Verify the practice-mode dependency path has no order endpoint and preserve the existing frozen-study tests.
