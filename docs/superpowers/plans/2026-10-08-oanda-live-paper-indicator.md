# OANDA Live Paper Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the native Mac app run a supervised OANDA practice-only intraday indicator with full paper tickets and an honest profitability report.

**Architecture:** Extend the existing `src/intraday` causal strategy and paper-execution core with a multi-product, append-only live session service. Bundle a paper-only helper in Nowcaster; the SwiftUI Trade Desk starts it with a Keychain credential passed only in the child environment, reads validated snapshots, and shows trade and report details. Keep the frozen BTC/ETH study and every existing research round separate.

**Tech Stack:** Python 3.11+, Pydantic/httpx/PyInstaller; SwiftUI, Security.framework/Keychain, XCTest; pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-oanda-live-paper-indicator-design.md`

## Global Constraints

- Use only `https://api-fxpractice.oanda.com` and `https://stream-fxpractice.oanda.com` for this mode. No order API, real-account endpoint, real order, or qualified alert.
- Preserve exact OANDA product identity: `DE30_EUR` is labelled “Germany 30” by the verified demo inventory; do not silently rename it Germany 40.
- The selected strategy, cost sources, sessions, risk policy, product identity, and round ID are immutable once paper collection starts. Keep all attempts and losses.
- Signal only after a complete confirmed five-minute bar and a later fresh quote; missing, stale, future, gapped, or non-tradeable data mean no trade.
- Risk limits: 0.25% initial equity per trade; 1% portfolio-wide daily realized-loss stop; 5% portfolio drawdown halt; one position per product; no more than 1.0× aggregate effective notional leverage.
- Do not put the token in a file, argument, status, ledger, log, Git commit, or test fixture. Existing Keychain service: `com.james8464.nowcaster.oanda.practice`.
- An observed paper result is not proof of real-money profitability. Evidence gate: at least 90 calendar days and 100 closed prospective paper trades, adequate coverage, positive after-cost expectancy and a positive 95% daily-block-bootstrap lower bound.

## Review Focus

1. A live account is later added to the same OANDA login: practice mode must still contact practice hosts only and never import order endpoints. Pin in Task 5 security tests.
2. The Mac sleeps, disconnects, or restarts with an open paper position: retain a gap and unresolved state; never invent a stop fill. Pin in Tasks 3–4.
3. Currency conversion or broker margin data disappears mid-session: block new entries, expose unavailable values, and keep management/ledger intact. Pin in Tasks 1 and 4.
4. A high win rate hides negative net returns: the report must show costs, expectancy, drawdown, sample and uncertainty. Pin in Task 6.
5. The app or helper crashes after writing an event but before publishing a snapshot: replay from the journal idempotently and fail stale until a fresh observation arrives. Pin in Tasks 3, 5 and 7.

---

### Task 1: Exact product, session, and cost eligibility

**Files:** Modify `src/intraday/contracts.py`, `src/intraday/workflow.py`, `src/intraday/quality.py`; create `src/intraday/eligibility.py`; test `tests/unit/test_intraday_eligibility.py`; update `README.md`.

**Interfaces:** Consume `OandaPracticeFeed.available_instruments()` and existing `InstrumentSpec`. Produce `evaluate_product(instrument: InstrumentSpec, broker_row: dict, costs: CostEvidence | None, conversion: FXConversion | None, at: datetime) -> EligibilityResult`, where the result carries product label, tradeable session, source-backed margin/cost values, and explicit blocking reasons. No product is paper-eligible from inventory alone.

- [ ] Write tests for exact symbols/types, Germany 30 naming, closed session, absent or unverified cost sources, stale/missing GBP conversion, and margin-rate changes; assert blocked entry and visible reasons.
- [ ] Run `PYTHONDONTWRITEBYTECODE=1 ../../.venv/bin/pytest -q tests/unit/test_intraday_eligibility.py`; confirm the new behavior fails for the intended reason.
- [ ] Implement the eligibility contract, requiring recorded source URL/date or user-attested exact-product terms; never default missing costs to zero.
- [ ] Run the focused test and existing `tests/unit/test_intraday_contracts.py tests/unit/test_intraday_quality.py` until green; commit Task 1.

### Task 2: Pre-registered strategy comparison and selection

**Files:** Modify `src/intraday/historical.py`, `src/intraday/research.py`, `src/intraday/workflow.py`; create `src/intraday/selection.py`; test `tests/unit/test_intraday_selection.py`; update `README.md`.

**Interfaces:** Consume exact-product exploratory bid/ask history, Task 1 cost evidence and fixed strategy IDs (`opening_range_15`, `opening_range_30`, `trend_pullback`, `range_reversion`). Produce `run_selection(manifest: SelectionManifest, bars_by_product: dict[str, tuple[ConfirmedBar, ...]]) -> SelectionReport` with every attempted product/rule/direction, chronological development/validation/sealed-test split, stressed costs, cash baseline, uncertainty, rejection reasons and immutable selected-rule identities. It must never use prospective account quotes to reselect a running rule.

- [ ] Write tests for all-attempt retention, zero-trade and cash baselines, short/long separation, sealed-test leakage, stressed-cost reversal, multiple-comparison reporting and failure to select when costs/data are insufficient.
- [ ] Run `PYTHONDONTWRITEBYTECODE=1 ../../.venv/bin/pytest -q tests/unit/test_intraday_selection.py`; confirm RED failures.
- [ ] Implement deterministic, versioned selection evidence, retaining all failed attempts. Treat historical base-price results as exploratory until prospective account quotes validate execution.
- [ ] Run focused plus `tests/unit/test_intraday_backtest.py tests/unit/test_intraday_historical_replay.py tests/unit/test_intraday_research_gate.py`; commit Task 2.

### Task 3: Durable multi-product quote capture and causal indication

**Files:** Create `src/intraday/live_service.py`, `src/intraday/session_journal.py`; modify `src/intraday/oanda_practice.py`, `src/intraday/live.py`, `src/intraday/strategies.py`, `src/intraday/desk.py`; test `tests/unit/test_intraday_live_service.py`.

**Interfaces:** Consume `OandaPracticeFeed.price_lines`, `LiveBarBuilder`, `evaluate_setup`, Task 1 eligibility and Task 2 immutable selection. Produce `LiveIndicatorSession.on_event(raw_line: str, received_at: datetime) -> DeskStatus` and `LiveIndicatorSession.restore(directory: Path, manifest: LiveRoundManifest) -> LiveIndicatorSession`. The session is one account stream for all configured symbols; each product has one immutable selected rule, independent warm-up and last-decision bar key. Append provider events and outcomes before publishing an atomic summary.

- [ ] Write recorded-stream tests for four-product routing, one evaluation per sealed bar, next-quote entry timing, duplicate/future events, partial bars, quote gap, reconnect, app sleep/restart and journal replay; assert stale/no-trade after interruption and no invented history.
- [ ] Run the focused test; confirm expected RED failures.
- [ ] Implement bounded reconnect/backoff, account quote validation, append-only journal identity and atomic status publication. Never feed historical base candles into a live prospective decision.
- [ ] Run focused plus `tests/unit/test_intraday_live_bar_builder.py tests/unit/test_intraday_strategies.py tests/unit/test_intraday_desk_status.py`; commit Task 3.

### Task 4: Portfolio paper tickets, management, and risk

**Files:** Modify `src/intraday/paper.py`, `src/intraday/runtime.py`, `src/intraday/journal.py`, `src/intraday/desk.py`; create `src/intraday/portfolio.py`; test `tests/unit/test_intraday_portfolio.py`.

**Interfaces:** Consume Task 3's fresh `SetupDecision` and `MarketQuote`. Produce `LivePaperPortfolio.on_decision(instrument: InstrumentSpec, plan: SetupDecision, quote: MarketQuote, eligibility: EligibilityResult) -> PaperEvent` and `on_quote(quote: MarketQuote, conversion: FXConversion | None) -> PaperEvent | None`, sharing equity and daily limits across products. `PaperTicket` includes units, notional and GBP conversion, effective leverage, broker margin rate/estimate, bid/ask entry, stop, target, time exit and itemized estimated/realized costs. Old journal schemas remain readable without changing old study files.

- [ ] Write tests for a complete long and short ticket, later quote fill, 0.25%/1%/5%/1.0× caps, one position per product, no entry without conversion/margin/costs, adverse stop/target, gap/slippage, session-end exit and unresolved disconnect.
- [ ] Run the focused test; confirm RED failures.
- [ ] Implement shared portfolio accounting and versioned append-only events; preserve old paper-runtime behavior and do not write to the frozen study.
- [ ] Run focused plus `tests/unit/test_intraday_paper_account.py tests/unit/test_intraday_live_runtime.py tests/unit/test_intraday_journal.py`; commit Task 4.

### Task 5: Safe bundled helper and Mac lifecycle

**Files:** Create `scripts/intraday_service_entry.py`, `macos/Nowcaster/Sources/NowcasterApp/Services/OandaPaperService.swift`, `macos/Nowcaster/Sources/NowcasterApp/Security/OandaPracticeCredentialVault.swift`; modify `scripts/embed_macos_runtime.sh`, `macos/Nowcaster/Sources/NowcasterApp/AppModel.swift`; test `tests/unit/test_intraday_service_entry.py`, `macos/Nowcaster/Tests/NowcasterAppTests/OandaPaperServiceTests.swift`.

**Interfaces:** The helper accepts only non-secret round/directory/control paths and obtains `OANDA_PRACTICE_ACCOUNT_ID`/`OANDA_PRACTICE_TOKEN` in its environment. Swift reads the existing Keychain item, launches the signed bundled helper, owns Start/Pause/shutdown, keeps management active until flat after Pause, and reports helper death as stale. No `subprocess` path uses a live host or order command.

- [ ] Write tests for Keychain missing/invalid state, token absent from process arguments and persisted evidence, practice-host allowlist, denied order endpoint, helper crash/restart, Pause with open position and Quit retention.
- [ ] Run focused pytest and `swift test --package-path macos/Nowcaster --filter OandaPaperServiceTests`; confirm RED failures.
- [ ] Implement the minimal signed helper packaging and service lifecycle; keep secret handling isolated from UI and logs.
- [ ] Run focused suites, build the helper/app, verify codesign, and commit Task 5.

### Task 6: Rebuildable profitability and decision report

**Files:** Create `src/intraday/report.py`, `scripts/report_intraday_paper.py`; modify `src/intraday/desk.py`; test `tests/unit/test_intraday_report.py`.

**Interfaces:** Produce `build_report(journal: PaperJournal, manifest: LiveRoundManifest, as_of: datetime) -> PaperDecisionReport` and atomic `report.json`/`report.md`. Include all decisions, blocked/no-trade reasons, full trade tickets and exits, gross/net P&L and each cost component, win rate, net expectancy, profit factor when defined, drawdown, feed/session coverage and daily-block 95% interval. Group by exact product/rule/direction/session; never count open/censored trades as closed wins or silently omit losses.

- [ ] Write tests for a winning and losing closed trade, negative-net high-win-rate case, unresolved position, missing cost component, gap day, empty sample, loss-retaining restart and report reconciliation against journal totals.
- [ ] Run the focused test; confirm RED failures.
- [ ] Implement deterministic report reconstruction and honest undefined/insufficient-evidence labels; retain separate historical and prospective sections.
- [ ] Run focused plus `tests/unit/test_intraday_research_gate.py tests/unit/test_intraday_backtest.py`; commit Task 6.

### Task 7: Compact Trade Desk, notifications, and user acceptance

**Files:** Modify `macos/Nowcaster/Sources/NowcasterApp/Features/TradeDesk/IntradayResearchView.swift`, `TradeDeskView.swift`; create `macos/Nowcaster/Sources/NowcasterApp/Features/TradeDesk/IntradayReportView.swift`; update `macos/Nowcaster/Tests/NowcasterAppTests/IntradayDeskStatusTests.swift`; create `macos/Nowcaster/Tests/NowcasterAppTests/IntradayReportTests.swift`, `macos/Nowcaster/UITests/OandaPaperFlowUITests.swift`; update `README.md`, `docs/native_verification.md`.

**Interfaces:** Consume the validated `DeskStatus` and Task 6 `PaperDecisionReport` only. Display Start/Pause, per-product feed age, experimental long/short/no-trade, complete on-demand ticket, paper position management, and report. Opt-in notifications announce fresh paper-only setup/open/close changes once; stale or duplicate data never notify. No order button.

- [ ] Write native model/UI tests for complete ticket, leverage/margin/cost wording, stale fail-closed state, no-trade reason, report sample/uncertainty, notification suppression, window close/reopen and distinct legacy BTC/ETH data.
- [ ] Run focused Swift/XCTest tests; confirm RED failures.
- [ ] Implement the view/presentation and notification logic, preserving a simple primary Trade Desk and accessible on-demand details.
- [ ] Run `PYTHONDONTWRITEBYTECODE=1 ../../.venv/bin/pytest -q`, `swift test --package-path macos/Nowcaster`, `NOWCASTER_BUILD_PYTHON=../../.venv/bin/python make macos-app`, codesign verification, and the native UI flow. Exercise a marked test fixture for feed interruption and paper lifecycle; do not write test events into the user's study.
- [ ] Review the entire diff for secrets, order routes, repainting, metric omissions and source changes; update docs with actual observations, commit, and push `feature/research-round-2` only after verification.

## Live acceptance and honest handoff

After the deterministic acceptance suite passes, connect the actual OANDA practice feed with the stored Keychain token and retain a new, separate paper round. Confirm fresh account bid/ask for all eligible products, report exact blocked products/cost terms, and show the app's live status. Do not manufacture a paper trade or claim profitability when no valid setup occurs during the observation window. A prospective 90-day/100-trade gate cannot be completed instantly; leave its collector running only when explicitly started in the app and report its early results as experimental.
