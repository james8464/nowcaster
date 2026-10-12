# Experimental OANDA Paper Round Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the installed Mac app observe complete, explicitly unqualified intraday paper trades on fresh OANDA practice quotes while preserving the existing diagnostic study and every loss and gap.

**Architecture:** Keep the running `LiveIndicatorSession` and its reports untouched. Add a separately registered experimental manifest and ledger, feed both consumers from one practice quote stream, and publish separate experimental status/report files. Reuse `LivePaperRuntime` and `PaperAccount` after correcting their next-quote causality and itemized accounting; the SwiftUI Trade Desk reads both lanes but never treats the experimental lane as a qualified recommendation.

**Tech Stack:** Python 3.12, Pydantic, pytest, httpx; SwiftUI, Swift Testing, XCTest; existing PyInstaller helper and Xcode macOS app.

**Spec:** `docs/superpowers/specs/2026-10-12-experimental-paper-round-design.md`

## Global Constraints

- Preserve `/Users/james/Library/Application Support/Nowcaster/IntradayResearch` diagnostic days and the frozen BTC/ETH study; no reset, rewrite, backfill, or rule change.
- OANDA practice read-only market-data endpoints only; no order or live-account endpoint or credential in a file, argument, log, or test snapshot.
- Experimental activity is `unqualified` and cannot enter the existing `paper_eligible` or qualified-notification path.
- Decisions use sealed account-sampled M5 bars; entry uses a *later* account quote and exit the correct adverse bid/ask side. Missing data is never interpolated.
- Keep 0.25% paper-equity risk, 1% realized daily-loss stop, 5% drawdown halt, one position per product, and aggregate effective notional leverage at or below 1.0x. Existing 0.25x exposure cap may remain stricter.
- Unknown source-backed cost or stale GBP conversion blocks paper entry; all rules, costs, sessions, and code identity are frozen before first quote.
- Historical and experimental results never become qualified merely because a paper balance is positive. Qualification still requires the spec's independent historical and 90-day/100-closed-trade forward gates.
- Deployment cannot restart the current collector mid-session without retaining the resulting gap; activate a new experimental round only at a declared session boundary.

## Review Focus

1. Duplicate or reordered quote after reconnect: one decision/open/close maximum, with the conflict retained as a gap (Task 2 tests).
2. Last quote before an exit or a Mac quit while a position is open: position remains unresolved rather than closed at an invented price (Tasks 2 and 5 tests).
3. `verified=true` cost flag without exact product/source identity, or conversion sampled after the entry quote: entry is rejected (Tasks 1 and 3 tests).
4. Old diagnostic `summary.json`/`report.json` and old UI fixtures: continue decoding and reporting unchanged after an experimental round exists (Tasks 3 and 5 tests).
5. Feed is healthy but setup has insufficient contiguous bars or no registered experiment: app says exactly why there is no experimental trade (Tasks 3 and 5 tests).

## File structure

- Create `src/intraday/experimental_round.py`: immutable manifest, cost provenance, registry, and account/product validation.
- Modify `src/intraday/runtime.py`, `src/intraday/paper.py`, and `src/intraday/journal.py`: next-quote pending plan, restart-safe execution, and reconciled cost components; no second P&L engine.
- Create `src/intraday/experimental_service.py`: one consumer per registered product, conversion snapshot handling, experimental status projection.
- Modify `scripts/intraday_service_entry.py`: fan out the existing practice stream to diagnostic and experimental consumers; do not change diagnostic files.
- Create `src/intraday/experimental_report.py`: adapt existing paper report calculation and measured quote coverage to the new manifest.
- Modify `macos/Nowcaster/Sources/NowcasterApp/Services/OandaPaperService.swift` and the two `Features/TradeDesk/Intraday*View.swift` files: lifecycle, separate experimental lane, report, tickets, and opt-in notifications.
- Create `scripts/register_intraday_experiment.py`: pre-register a candidate and exact cost evidence before a new day. Update `docs/research/live-paper-signals.md` with operational and qualification boundaries.

---

### Task 1: Immutable experiment registration and cost evidence

**Files:** Create `src/intraday/experimental_round.py`, `scripts/register_intraday_experiment.py`, `tests/unit/test_experimental_round.py`; modify `tests/unit/test_intraday_service_entry.py` only for registration compatibility.

**Interfaces:** Produce `ExperimentalRoundManifest` (Pydantic, frozen, extra-forbid) with `round_id`, cross-day `cohort_id`, `account_feed_hash`, exact `InstrumentSpec`, strategy ID and one declared direction, UTC session window and source-backed calendar hash, `PaperExecutionCosts`, `cost_source`/`cost_source_hash`/`verified_at`, account currency, margin rate, unit step, initial paper cash, bundled-helper SHA-256 `code_hash`, and `qualification="unqualified"`. Produce `register_experimental_round(root: Path, manifest: ExperimentalRoundManifest) -> Path` and `load_experimental_round(path: Path, *, account_feed_hash: str, code_hash: str, at: datetime) -> ExperimentalRoundManifest`. The registry path is `IntradayResearch/ExperimentalRounds/<round_id>/manifest.json`; exclusive creation or exact-byte idempotence only. The CLI only registers—it cannot start orders or mutate a retained round.

- [ ] **Step 1: Write failing tests** named `test_round_is_immutable_and_account_bound`, `test_missing_cost_source_or_wrong_product_cannot_register`, `test_wrong_helper_hash_cannot_load`, and `test_manifest_contains_no_token`. Assert exact-byte re-registration succeeds, a changed rule/cost/account/helper fails, and no raw account ID or token appears in files.
- [ ] **Step 2: Verify red:** run `.venv/bin/python -m pytest -q tests/unit/test_experimental_round.py`; expect import/test failures for the missing registry.
- [ ] **Step 3: Implement the two signatures above** and CLI `register --manifest <path> --directory <path>`; permit only known strategy IDs, exact inventory product, UTC windows, registration before `session_open`, `costs.verified is True`, nonempty source reference and SHA-256, and all numeric risk inputs finite/positive. A round registered after its declared start is rejected, not retroactively backfilled.
- [ ] **Step 4: Verify green:** rerun the new tests and `tests/unit/test_intraday_service_entry.py`; expect pass.
- [ ] **Step 5: Commit:** `git add src/intraday/experimental_round.py scripts/register_intraday_experiment.py tests/unit/test_experimental_round.py tests/unit/test_intraday_service_entry.py && git commit -m 'feat: register immutable practice paper experiments'`.

### Task 2: Causal and restart-safe paper execution

**Files:** Modify `src/intraday/runtime.py`, `src/intraday/paper.py`, `src/intraday/journal.py`, `tests/unit/test_intraday_live_runtime.py`, `tests/unit/test_intraday_paper_account.py`.

**Interfaces:** Keep `LivePaperRuntime.on_quote(quote: MarketQuote, conversion: FXConversion | None = None) -> PaperEvent | None`, but append a `planned` event with the complete serialized `SetupDecision` and decision-quote source key, then call `PaperAccount.open` only on a distinct later quote within the frozen 15-second entry window. Extend `PaperEvent.kind` to include `planned`, and `ClosedPaperTrade` with `gross_pnl_gbp`, `commission_gbp`, `financing_gbp`, and `conversion_fee_gbp`; `net_pnl` equals gross minus those three costs. Spread and slippage are explicitly marked as embedded in executable entry/exit prices and must not be subtracted twice. `opened` retains complete ticket identity and `closed` retains all reconciled components; both are idempotent by source key and round identity.

- [ ] **Step 1: Write failing tests** `test_signal_quote_cannot_be_its_own_entry`, `test_later_bid_ask_quote_opens_once`, `test_duplicate_and_reordered_quote_cannot_double_trade`, `test_missing_exit_leaves_unresolved_position`, and `test_closed_cost_components_reconcile`. Assert open time and source key are strictly later than the decision quote, and close net equals gross less commission, financing, and conversion.
- [ ] **Step 2: Verify red:** run `.venv/bin/python -m pytest -q tests/unit/test_intraday_live_runtime.py tests/unit/test_intraday_paper_account.py`; expect the new assertions to fail on same-quote opening and missing cost fields.
- [ ] **Step 3: Implement pending-plan state and ledger changes** within the existing runtime/account, and update existing same-quote-opening fixtures to require a distinct later entry quote. Restore a pending plan only from a durable event if its entry window has not expired; otherwise record expiry. Manage an existing position before evaluating new entries. Keep every gap and rejection.
- [ ] **Step 4: Verify green:** rerun both files plus `tests/unit/test_intraday_report.py`; expect pass and no altered prior ledger fixtures.
- [ ] **Step 5: Commit:** `git add src/intraday/runtime.py src/intraday/paper.py src/intraday/journal.py tests/unit/test_intraday_live_runtime.py tests/unit/test_intraday_paper_account.py && git commit -m 'fix: make paper entry causal and accounting reconcilable'`.

### Task 3: Isolated experimental consumer on the existing practice stream

**Files:** Create `src/intraday/experimental_service.py`, `tests/unit/test_experimental_service.py`; modify `scripts/intraday_service_entry.py`, `tests/unit/test_intraday_service_entry.py`.

**Interfaces:** Produce `ExperimentalPaperService(manifest: ExperimentalRoundManifest, directory: Path)` with `on_quote(quote: MarketQuote, *, conversion: FXConversion | None) -> PaperEvent | None`, `status(at: datetime) -> ExperimentalDeskStatus`, and `disarm_new_entries() -> None`; `ExperimentalDeskStatus` is a new Pydantic DTO in the same module with explicit `qualification="unqualified"`, quote age, contiguous-bar count, no-trade reason, and current paper ticket. The service owns a separate `SessionJournal` of accepted quotes, gaps, and one experimental decision per sealed bar. The helper dispatches `LiveIndicatorSession.last_quote` to this service only after the diagnostic journal accepted the raw quote. Publish `experimental-summary.json` atomically; keep existing `summary.json` byte/schema semantics. Use `OandaPracticeFeed.home_conversions` on a bounded separate worker, and a conversion snapshot may influence only quotes received after that snapshot. Missing/stale conversion blocks entries and emits one deduplicated gap reason. `pause.request` disarms entries and keeps quote management running until flat; `shutdown.request` journals any unresolved position and exits without a fabricated close.

- [ ] **Step 1: Write failing tests** `test_one_price_line_feeds_both_isolated_rounds`, `test_no_registered_round_keeps_old_files_unchanged`, `test_future_conversion_cannot_enter`, `test_gap_requires_fresh_declared_lookback`, `test_pause_manages_open_position_until_flat`, `test_shutdown_retains_unresolved_position`, and `test_error_or_closed_session_shows_specific_no_trade`. Use a fake stream and temporary directories; assert experimental-only events do not alter the diagnostic journal beyond its own expected quote/decision append.
- [ ] **Step 2: Verify red:** run `.venv/bin/python -m pytest -q tests/unit/test_experimental_service.py tests/unit/test_intraday_service_entry.py`; expect failures for the missing service/status.
- [ ] **Step 3: Implement the service and fan-out** without historical-candle warmup. Resolve the running bundled helper's SHA-256 and call `load_experimental_round` with it before dispatch; never fall back to a source-tree hash when the installed executable differs. Preserve confirmed pre-gap bars for audit, but evaluate only the contiguous post-gap lookback declared by the rule. Account conversion fetch must not block quote processing long enough to make a quote appear fresh after it is stale.
- [ ] **Step 4: Verify green:** rerun the two files and `tests/unit/test_intraday_live_service.py`; expect pass.
- [ ] **Step 5: Commit:** `git add src/intraday/experimental_service.py scripts/intraday_service_entry.py tests/unit/test_experimental_service.py tests/unit/test_intraday_service_entry.py && git commit -m 'feat: observe an isolated experimental paper round'`.

### Task 4: Round-specific report and explicit zero-trade evidence

**Files:** Create `src/intraday/experimental_report.py`, `tests/unit/test_experimental_report.py`; modify `src/intraday/report.py` only to reuse its existing trade arithmetic and coverage helpers, and `scripts/intraday_service_entry.py` to publish the new report.

**Interfaces:** Produce `ExperimentalPaperReport` (Pydantic DTO) containing `round_id`, `cohort_id`, `qualification="unqualified"`, `performance: PaperDecisionReport`, `attempted_entries`, `rejected_entries`, and exact expected/covered eligible M5 intervals. Produce `build_experimental_report(manifest: ExperimentalRoundManifest, paper: PaperJournal, session: SessionJournal, *, as_of: datetime) -> ExperimentalPaperReport` and `aggregate_experimental_cohort(reports: tuple[ExperimentalPaperReport, ...], *, as_of: datetime) -> ExperimentalPaperReport` (its `round_id` is `cohort:<cohort_id>`). Call `build_report` for the only P&L calculation, deduplicate no-trade reasons shared by the two journals by decision source key, and use `summarize_capture` for coverage. Aggregation requires identical cohort/product/rule/cost/calendar/code identity, retains missing eligible days, and never drops losing or interrupted rounds. Publish `experimental-report.json` atomically. Zero closes leave win rate/expectancy undefined. The existing aggregate `report.json` stays diagnostic and unchanged.

- [ ] **Step 1: Write failing tests** `test_zero_trades_is_not_zero_expectancy`, `test_wins_losses_fees_and_gaps_all_retained`, `test_reordered_or_orphan_close_invalidates_report`, `test_coverage_counts_wholly_missing_intervals`, `test_cohort_aggregate_keeps_losses_and_missing_days`, and `test_old_diagnostic_report_is_unchanged`.
- [ ] **Step 2: Verify red:** run `.venv/bin/python -m pytest -q tests/unit/test_experimental_report.py`; expect missing function or violated assertions.
- [ ] **Step 3: Implement the report adapter** using `build_report` for the single P&L computation; extend it only where the experimental manifest supplies exact session windows and coverage. Never infer a fill from a target touch or a missing quote.
- [ ] **Step 4: Verify green:** run the new tests and `tests/unit/test_intraday_report.py`; expect pass.
- [ ] **Step 5: Commit:** `git add src/intraday/experimental_report.py src/intraday/report.py scripts/intraday_service_entry.py tests/unit/test_experimental_report.py && git commit -m 'feat: report experimental paper outcomes separately'`.

### Task 5: Native paper lane, safe lifecycle, and labels

**Files:** Modify `macos/Nowcaster/Sources/NowcasterApp/Services/OandaPaperService.swift`, `Features/TradeDesk/IntradayResearchView.swift`, `Features/TradeDesk/IntradayReportView.swift`; create `Features/TradeDesk/ExperimentalPaperStatus.swift`; modify `macos/Nowcaster/Tests/NowcasterAppTests/OandaPaperServiceTests.swift`, `IntradayDeskStatusTests.swift`, `IntradayReportTests.swift`, `macos/Nowcaster/UITests/OandaPaperFlowUITests.swift`.

**Interfaces:** Swift `ExperimentalPaperStatus.decode(_ data: Data) throws -> Self` validates the new experimental JSON separately from `IntradayDeskStatus`; `IntradayPaperReport` gains a separate `decodeExperimental(_:)` that reads `ExperimentalPaperReport.performance` without changing `decode(_:)` for old files. The UI has `Feed`, `Experimental paper activity`, and `Evidence` sections. An experimental ticket always says `Unqualified experimental paper trade`, shows exact asset/side/units/leverage/entry/stop/target/time exit/cost source, and never passes `OandaPaperNotificationGate.eligibleSetupIDs`. Pause disarms new entries but continues management until flat; quit records unresolved state if a fresh exit is unavailable. Opt-in experimental notifications use a distinct ID/category and explicit paper label.

- [ ] **Step 1: Write failing Swift/UI tests** `experimentalStatusCannotDecodeAsQualified`, `oldDiagnosticFixtureStillDecodes`, `paperTicketShowsUnqualifiedCostsAndLeverage`, `pauseWithOpenPositionDoesNotForceClose`, and `staleExperimentalStatusCannotNotify`.
- [ ] **Step 2: Verify red:** run `make macos-test` and the targeted `xcodebuild -project macos/Nowcaster/Nowcaster.xcodeproj -scheme Nowcaster -destination 'platform=macOS' test`; expect the new tests to fail before implementation.
- [ ] **Step 3: Implement the separate DTO/presentation and pause/quit handshake** with the helper. Remove the existing unconditional `process?.terminate()` path for a running experimental position; ensure a bounded clean stop writes a retained unresolved state if no fresh exit arrives. Keep window-closed collection while the app process runs.
- [ ] **Step 4: Verify green:** rerun `make macos-test` and the targeted Xcode tests; expect pass, including old diagnostic fixtures.
- [ ] **Step 5: Commit:** stage only the listed Swift/test files and commit `feat: show isolated experimental paper tickets in Mac app`.

### Task 6: Research boundary, end-to-end acceptance, and release

**Files:** Modify `docs/research/live-paper-signals.md`, `README.md`, `tests/integration/test_live_paper_lifecycle_runtime.py`, `tests/integration/test_app_snapshot_export.py`, `macos/Nowcaster/UITests/OandaPaperFlowUITests.swift`; create `tests/integration/test_experimental_paper_security.py`; modify build scripts only if the new Python module is not bundled.

**Interfaces:** The registered first observation round is one `DE30_EUR` opening-range-15 long *execution sentinel*, explicitly labelled as historically failed and unqualified; it is never a selected or qualified rule and its alerts remain off by default. Register it only after the exact product cost record, GBP conversion, and session policy are source-backed. Separate research rounds retain all new hypotheses/attempts and cannot use this prospective cohort's future returns for selection. A future qualified rule needs the spec's independent historical gate plus at least 90 days/100 closed practice trades and positive net 95% daily-block lower bound.

- [ ] **Step 1: Add failing acceptance tests** `test_stream_to_closed_ticket_to_report_without_orders`, `test_future_quote_mutation_keeps_prior_decision_hashes`, `test_unknown_cost_or_missing_conversion_never_opens`, `test_experimental_pnl_cannot_select_historical_rule`, and `test_installed_app_shows_experimental_label_and_no_qualified_alert`. Assert the frozen BTC/ETH and current OANDA diagnostic directories are never written by tests.
- [ ] **Step 2: Verify red:** run `.venv/bin/python -m pytest -q tests/integration/test_live_paper_lifecycle_runtime.py tests/integration/test_app_snapshot_export.py`; expect the new acceptance cases to fail before wiring/docs are complete.
- [ ] **Step 3: Finish the integration and operations guide.** Keep the existing `ResearchCatalog` in the separate historical research workflow; the live helper must not import it or convert experimental P&L into a `record_attempt` call. The security test scans that dependency boundary. Document exact official cost/product sources and the sentinel's prior losses; if costs cannot be verified, leave activation disabled and report the blocker rather than declaring zero costs. Add a source-tree scan test rejecting OANDA order/live-account routes in the helper dependency path.
- [ ] **Step 4: Verify green and package:** run the targeted Python suites, `make macos-test macos-app macos-ui-test`, `make macos-xcui-test`, and the repository's secret scan. Test the built app from the user's perspective with a deterministic fixture, then inspect the installed app's helper identity and report files. Do not interrupt the running collector mid-session; activate a new round only after the declared boundary and verify a fresh status/report.
- [ ] **Step 5: Commit and push:** commit the acceptance/docs/build changes; run `git status --short --branch`, then push the single `feature/research-round-2` branch to `origin`. Report the actual test and paper-evidence results, including any unverified-cost or live-activation limitation; never claim real-money profitability.
